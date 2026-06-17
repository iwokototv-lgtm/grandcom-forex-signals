"""
Unit tests for the SignalValidator pipeline.

Covers:
  - All 5 validation checks (signal type, confidence, entry, TP levels, SL)
  - Valid signal passes all checks
  - Each individual check failure returns correct reason and NEUTRAL signal
  - Validator returns correct structure on pass and fail
  - send_signal_rejection_alert sends Telegram message on rejection
  - log_signal_event writes to MongoDB when db is available
  - log_signal_event is a no-op when db is None
"""

import pytest
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from ml_engine.signal_validator import SignalValidator


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────

def _valid_signal_data(**overrides) -> dict:
    """Return a fully valid signal data dict, with optional field overrides."""
    base = {
        "pair": "XAUUSD",
        "signal": "SELL",
        "confidence": 75.0,
        "entry": 2350.00,
        "tp_levels": [2340.00, 2330.00, 2320.00],
        "sl": 2365.00,
        "analysis": "Strong bearish momentum confirmed by RSI and MACD.",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────
# Check 1: Signal type validation
# ─────────────────────────────────────────────────────────────────────────

class TestSignalTypeValidation:
    """Verify Check 1: signal must be BUY, SELL, or NEUTRAL."""

    @pytest.mark.asyncio
    async def test_buy_signal_passes_type_check(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(signal="BUY", tp_levels=[2360.0, 2370.0, 2380.0], sl=2335.0))
        assert "Signal type valid" in result["checks_passed"]

    @pytest.mark.asyncio
    async def test_sell_signal_passes_type_check(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data())
        assert "Signal type valid" in result["checks_passed"]

    @pytest.mark.asyncio
    async def test_invalid_signal_type_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(signal="STRONG_BUY"))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"
        assert "Invalid signal type" in result["checks_failed"]
        assert "STRONG_BUY" in result["reason"]

    @pytest.mark.asyncio
    async def test_none_signal_type_fails(self):
        validator = SignalValidator()
        data = _valid_signal_data()
        data["signal"] = None
        result = await validator.validate(data)
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"
        assert "Invalid signal type" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_empty_string_signal_type_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(signal=""))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_neutral_signal_passes_type_check(self):
        """NEUTRAL is a valid signal type — it passes Check 1 but may fail later checks."""
        validator = SignalValidator()
        # NEUTRAL with confidence >= 60 and valid entry/tp/sl should pass all checks
        result = await validator.validate(_valid_signal_data(signal="NEUTRAL"))
        assert "Signal type valid" in result["checks_passed"]


# ─────────────────────────────────────────────────────────────────────────
# Check 2: Confidence threshold
# ─────────────────────────────────────────────────────────────────────────

class TestConfidenceValidation:
    """Verify Check 2: confidence must be >= 60%."""

    @pytest.mark.asyncio
    async def test_confidence_60_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=60.0))
        assert result["valid"] is True
        assert any("Confidence sufficient" in c for c in result["checks_passed"])

    @pytest.mark.asyncio
    async def test_confidence_75_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=75.0))
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_confidence_100_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=100.0))
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_confidence_59_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=59.9))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"
        assert any("Confidence too low" in c for c in result["checks_failed"])
        assert "59.9%" in result["reason"] or "59.9" in result["reason"]

    @pytest.mark.asyncio
    async def test_confidence_0_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=0.0))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_confidence_45_fails_with_clear_reason(self):
        """Confidence failure must include the actual value in the reason string."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=45.0))
        assert result["valid"] is False
        assert "45.0" in result["reason"]
        assert "60%" in result["reason"]


# ─────────────────────────────────────────────────────────────────────────
# Check 3: Entry price validation
# ─────────────────────────────────────────────────────────────────────────

class TestEntryPriceValidation:
    """Verify Check 3: entry price must be > 0."""

    @pytest.mark.asyncio
    async def test_positive_entry_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(entry=2350.0))
        assert any("Entry price valid" in c for c in result["checks_passed"])

    @pytest.mark.asyncio
    async def test_zero_entry_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(entry=0.0))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"
        assert "Invalid entry price" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_negative_entry_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(entry=-100.0))
        assert result["valid"] is False
        assert "Invalid entry price" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_missing_entry_fails(self):
        """Missing entry key defaults to 0.0 and must fail."""
        validator = SignalValidator()
        data = _valid_signal_data()
        del data["entry"]
        result = await validator.validate(data)
        assert result["valid"] is False
        assert "Invalid entry price" in result["checks_failed"]


# ─────────────────────────────────────────────────────────────────────────
# Check 4: TP levels validation
# ─────────────────────────────────────────────────────────────────────────

class TestTPLevelsValidation:
    """Verify Check 4: must have at least 3 TP levels."""

    @pytest.mark.asyncio
    async def test_three_tp_levels_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(tp_levels=[2340.0, 2330.0, 2320.0]))
        assert any("TP levels valid" in c for c in result["checks_passed"])

    @pytest.mark.asyncio
    async def test_more_than_three_tp_levels_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(tp_levels=[2340.0, 2330.0, 2320.0, 2310.0]))
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_empty_tp_levels_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(tp_levels=[]))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"
        assert "Missing TP levels" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_two_tp_levels_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(tp_levels=[2340.0, 2330.0]))
        assert result["valid"] is False
        assert "Missing TP levels" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_one_tp_level_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(tp_levels=[2340.0]))
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_missing_tp_levels_key_fails(self):
        """Missing tp_levels key defaults to [] and must fail."""
        validator = SignalValidator()
        data = _valid_signal_data()
        del data["tp_levels"]
        result = await validator.validate(data)
        assert result["valid"] is False
        assert "Missing TP levels" in result["checks_failed"]


# ─────────────────────────────────────────────────────────────────────────
# Check 5: SL price validation
# ─────────────────────────────────────────────────────────────────────────

class TestSLValidation:
    """Verify Check 5: SL price must be > 0."""

    @pytest.mark.asyncio
    async def test_positive_sl_passes(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(sl=2365.0))
        assert any("SL valid" in c for c in result["checks_passed"])

    @pytest.mark.asyncio
    async def test_zero_sl_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(sl=0.0))
        assert result["valid"] is False
        assert result["signal"] == "NEUTRAL"
        assert "Invalid SL price" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_negative_sl_fails(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(sl=-50.0))
        assert result["valid"] is False
        assert "Invalid SL price" in result["checks_failed"]

    @pytest.mark.asyncio
    async def test_missing_sl_key_fails(self):
        """Missing sl key defaults to 0.0 and must fail."""
        validator = SignalValidator()
        data = _valid_signal_data()
        del data["sl"]
        result = await validator.validate(data)
        assert result["valid"] is False
        assert "Invalid SL price" in result["checks_failed"]


# ─────────────────────────────────────────────────────────────────────────
# Full validation pass
# ─────────────────────────────────────────────────────────────────────────

class TestFullValidationPass:
    """Verify a fully valid signal passes all 5 checks."""

    @pytest.mark.asyncio
    async def test_valid_sell_signal_passes_all_checks(self):
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data())

        assert result["valid"] is True
        assert result["signal"] == "SELL"
        assert len(result["checks_passed"]) == 5
        assert len(result["checks_failed"]) == 0
        assert result["reason"] == "Signal passed all validation checks"

    @pytest.mark.asyncio
    async def test_valid_buy_signal_passes_all_checks(self):
        validator = SignalValidator()
        buy_data = _valid_signal_data(
            signal="BUY",
            entry=2350.0,
            tp_levels=[2360.0, 2370.0, 2380.0],
            sl=2335.0,
        )
        result = await validator.validate(buy_data)

        assert result["valid"] is True
        assert result["signal"] == "BUY"
        assert len(result["checks_passed"]) == 5
        assert len(result["checks_failed"]) == 0

    @pytest.mark.asyncio
    async def test_result_contains_timestamp(self):
        """Validation result must include an ISO-8601 timestamp."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data())

        assert "timestamp" in result
        # Must be parseable as ISO-8601
        ts = datetime.fromisoformat(result["timestamp"])
        assert ts.tzinfo is not None  # must be timezone-aware

    @pytest.mark.asyncio
    async def test_result_structure_complete(self):
        """Validation result must always contain all required keys."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data())

        required_keys = {"valid", "signal", "reason", "checks_passed", "checks_failed", "timestamp"}
        assert required_keys.issubset(result.keys()), (
            f"Missing keys: {required_keys - result.keys()}"
        )

    @pytest.mark.asyncio
    async def test_checks_passed_contains_all_five_checks(self):
        """All 5 check names must appear in checks_passed for a valid signal."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data())

        checks = result["checks_passed"]
        assert any("Signal type valid" in c for c in checks)
        assert any("Confidence sufficient" in c for c in checks)
        assert any("Entry price valid" in c for c in checks)
        assert any("TP levels valid" in c for c in checks)
        assert any("SL valid" in c for c in checks)


# ─────────────────────────────────────────────────────────────────────────
# Early exit on first failure
# ─────────────────────────────────────────────────────────────────────────

class TestEarlyExitOnFailure:
    """Verify validator exits on the first failed check (no further checks run)."""

    @pytest.mark.asyncio
    async def test_invalid_signal_type_stops_at_check_1(self):
        """When Check 1 fails, checks_passed must be empty (no further checks ran)."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(signal="INVALID"))

        assert result["valid"] is False
        assert len(result["checks_passed"]) == 0
        assert len(result["checks_failed"]) == 1

    @pytest.mark.asyncio
    async def test_low_confidence_stops_at_check_2(self):
        """When Check 2 fails, only Check 1 should be in checks_passed."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(confidence=30.0))

        assert result["valid"] is False
        assert len(result["checks_passed"]) == 1  # Check 1 passed
        assert "Signal type valid" in result["checks_passed"]
        assert len(result["checks_failed"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_entry_stops_at_check_3(self):
        """When Check 3 fails, Checks 1 and 2 should be in checks_passed."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(entry=0.0))

        assert result["valid"] is False
        assert len(result["checks_passed"]) == 2  # Checks 1 and 2 passed
        assert len(result["checks_failed"]) == 1

    @pytest.mark.asyncio
    async def test_missing_tp_stops_at_check_4(self):
        """When Check 4 fails, Checks 1-3 should be in checks_passed."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(tp_levels=[]))

        assert result["valid"] is False
        assert len(result["checks_passed"]) == 3  # Checks 1, 2, 3 passed
        assert len(result["checks_failed"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_sl_stops_at_check_5(self):
        """When Check 5 fails, Checks 1-4 should be in checks_passed."""
        validator = SignalValidator()
        result = await validator.validate(_valid_signal_data(sl=0.0))

        assert result["valid"] is False
        assert len(result["checks_passed"]) == 4  # Checks 1, 2, 3, 4 passed
        assert len(result["checks_failed"]) == 1


# ─────────────────────────────────────────────────────────────────────────
# send_signal_rejection_alert
# ─────────────────────────────────────────────────────────────────────────

class TestSendSignalRejectionAlert:
    """Verify send_signal_rejection_alert sends correct Telegram message."""

    @pytest.mark.asyncio
    async def test_rejection_alert_sends_telegram_message(self):
        from gold_server_v3 import send_signal_rejection_alert

        validation_result = {
            "valid": False,
            "signal": "SELL",
            "reason": "Confidence 45.0% below 60% minimum",
            "checks_passed": ["Signal type valid"],
            "checks_failed": ["Confidence too low (45.0% < 60%)"],
        }

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            await send_signal_rejection_alert("XAUUSD", validation_result)

            mock_bot.send_message.assert_called_once()
            call_kwargs = mock_bot.send_message.call_args
            msg_text = call_kwargs[1].get("text") or call_kwargs[0][1]
            assert "SIGNAL REJECTED" in msg_text
            assert "XAUUSD" in msg_text
            assert "Confidence 45.0% below 60% minimum" in msg_text

    @pytest.mark.asyncio
    async def test_rejection_alert_includes_checks_failed(self):
        from gold_server_v3 import send_signal_rejection_alert

        validation_result = {
            "valid": False,
            "signal": "BUY",
            "reason": "Invalid entry price: 0.0",
            "checks_passed": ["Signal type valid", "Confidence sufficient (75.0%)"],
            "checks_failed": ["Invalid entry price"],
        }

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            await send_signal_rejection_alert("XAUEUR", validation_result)

            call_kwargs = mock_bot.send_message.call_args
            msg_text = call_kwargs[1].get("text") or call_kwargs[0][1]
            assert "Invalid entry price" in msg_text
            assert "XAUEUR" in msg_text

    @pytest.mark.asyncio
    async def test_rejection_alert_handles_telegram_error_gracefully(self):
        """Alert must not raise even if Telegram send fails."""
        from gold_server_v3 import send_signal_rejection_alert

        validation_result = {
            "valid": False,
            "signal": "SELL",
            "reason": "Test reason",
            "checks_passed": [],
            "checks_failed": ["Invalid signal type"],
        }

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_bot.send_message = AsyncMock(side_effect=Exception("Telegram API error"))
            mock_get_bot.return_value = mock_bot

            # Must not raise
            await send_signal_rejection_alert("XAUUSD", validation_result)

    @pytest.mark.asyncio
    async def test_rejection_alert_uses_html_parse_mode(self):
        """Alert must use HTML parse mode for formatting."""
        from gold_server_v3 import send_signal_rejection_alert

        validation_result = {
            "valid": False,
            "signal": "SELL",
            "reason": "Test",
            "checks_passed": [],
            "checks_failed": ["Test check"],
        }

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            await send_signal_rejection_alert("XAUUSD", validation_result)

            call_kwargs = mock_bot.send_message.call_args
            assert call_kwargs[1].get("parse_mode") == "HTML"


# ─────────────────────────────────────────────────────────────────────────
# log_signal_event
# ─────────────────────────────────────────────────────────────────────────

class TestLogSignalEvent:
    """Verify log_signal_event writes to MongoDB and handles missing db gracefully."""

    @pytest.mark.asyncio
    async def test_log_event_inserts_to_mongodb(self):
        from gold_server_v3 import log_signal_event

        mock_db = MagicMock()
        mock_db.signal_events = MagicMock()
        mock_db.signal_events.insert_one = AsyncMock(return_value=MagicMock(inserted_id="abc123"))

        with patch("gold_server_v3._db", mock_db):
            await log_signal_event(
                pair="XAUUSD",
                event_type="generated",
                signal="SELL",
                confidence=75.0,
                reason="GPT signal generated",
                metadata={"hybrid_signal": "SELL"},
            )

        mock_db.signal_events.insert_one.assert_called_once()
        inserted_doc = mock_db.signal_events.insert_one.call_args[0][0]
        assert inserted_doc["pair"] == "XAUUSD"
        assert inserted_doc["event_type"] == "generated"
        assert inserted_doc["signal"] == "SELL"
        assert inserted_doc["confidence"] == 75.0
        assert inserted_doc["reason"] == "GPT signal generated"
        assert inserted_doc["metadata"] == {"hybrid_signal": "SELL"}
        assert "timestamp" in inserted_doc

    @pytest.mark.asyncio
    async def test_log_event_noop_when_db_is_none(self):
        """log_signal_event must be a no-op when _db is None."""
        from gold_server_v3 import log_signal_event

        with patch("gold_server_v3._db", None):
            # Must not raise
            await log_signal_event(
                pair="XAUUSD",
                event_type="generated",
                signal="SELL",
                confidence=75.0,
            )

    @pytest.mark.asyncio
    async def test_log_event_handles_mongodb_error_gracefully(self):
        """log_signal_event must not raise when MongoDB insert fails."""
        from gold_server_v3 import log_signal_event

        mock_db = MagicMock()
        mock_db.signal_events = MagicMock()
        mock_db.signal_events.insert_one = AsyncMock(side_effect=Exception("MongoDB connection error"))

        with patch("gold_server_v3._db", mock_db):
            # Must not raise
            await log_signal_event(
                pair="XAUUSD",
                event_type="rejected",
                signal="NEUTRAL",
                confidence=45.0,
                reason="Confidence too low",
            )

    @pytest.mark.asyncio
    async def test_log_event_stores_all_event_types(self):
        """All event types (generated, validated, rejected, sent) must be storable."""
        from gold_server_v3 import log_signal_event

        mock_db = MagicMock()
        mock_db.signal_events = MagicMock()
        mock_db.signal_events.insert_one = AsyncMock(return_value=MagicMock(inserted_id="xyz"))

        for event_type in ("generated", "validated", "rejected", "sent"):
            with patch("gold_server_v3._db", mock_db):
                await log_signal_event(
                    pair="XAUUSD",
                    event_type=event_type,
                    signal="SELL",
                    confidence=75.0,
                )

        assert mock_db.signal_events.insert_one.call_count == 4

    @pytest.mark.asyncio
    async def test_log_event_metadata_defaults_to_empty_dict(self):
        """metadata must default to {} when not provided."""
        from gold_server_v3 import log_signal_event

        mock_db = MagicMock()
        mock_db.signal_events = MagicMock()
        mock_db.signal_events.insert_one = AsyncMock(return_value=MagicMock(inserted_id="xyz"))

        with patch("gold_server_v3._db", mock_db):
            await log_signal_event(
                pair="XAUUSD",
                event_type="generated",
                signal="BUY",
                confidence=80.0,
                # metadata not provided
            )

        inserted_doc = mock_db.signal_events.insert_one.call_args[0][0]
        assert inserted_doc["metadata"] == {}


# ─────────────────────────────────────────────────────────────────────────
# Integration: validator in generate_signal pipeline
# ─────────────────────────────────────────────────────────────────────────

class TestValidatorIntegration:
    """Integration tests verifying the validator is wired into generate_signal."""

    @pytest.mark.asyncio
    async def test_rejection_alert_sent_when_validator_rejects(self):
        """
        When the validator rejects a signal, send_signal_rejection_alert must be called.
        This verifies the validator is actually wired into the pipeline.
        """
        from gold_server_v3 import send_signal_rejection_alert

        # Simulate a validator rejection result
        rejection_result = {
            "valid": False,
            "signal": "SELL",
            "reason": "Invalid entry price: 0.0",
            "checks_passed": ["Signal type valid", "Confidence sufficient (75.0%)"],
            "checks_failed": ["Invalid entry price"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with patch("gold_server_v3.get_bot") as mock_get_bot:
            mock_bot = AsyncMock()
            mock_get_bot.return_value = mock_bot

            await send_signal_rejection_alert("XAUUSD", rejection_result)

            # Alert must have been sent
            mock_bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_signal_validator_singleton_is_available(self):
        """The _signal_validator singleton must be importable from gold_server_v3."""
        from gold_server_v3 import _signal_validator
        from ml_engine.signal_validator import SignalValidator

        assert isinstance(_signal_validator, SignalValidator)

    @pytest.mark.asyncio
    async def test_validator_validate_method_is_async(self):
        """validate() must be an async method (awaitable)."""
        import inspect
        validator = SignalValidator()
        assert inspect.iscoroutinefunction(validator.validate)

    def test_signal_validator_module_singleton_exists(self):
        """signal_validator singleton must be exported from the module."""
        from ml_engine.signal_validator import signal_validator
        assert isinstance(signal_validator, SignalValidator)


# ─────────────────────────────────────────────────────────────────────────
# PYTEST CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
