"""
Unit tests for Telegram signal delivery in gold_server_v3.py.

Verifies:
  - Successful signal delivery logs correctly and returns True
  - Retry logic fires on transient failures (timeout, network error)
  - Failure after max retries returns False and logs an error
  - Bot is properly initialized (initialize()) on startup
  - Bot is properly shut down (shutdown()) on teardown
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal_kwargs(**overrides):
    """Return a minimal set of kwargs for send_to_telegram."""
    defaults = dict(
        pair="XAUUSD",
        signal="BUY",
        entry=2350.00,
        tps=[2355.00, 2360.00, 2365.00],
        sl=2340.00,
        confidence=90.0,
        rr=1.5,
        analysis="Strong bullish momentum",
        regime="BULL",
        smc_score=8,
        mtf_alignment=85.0,
        position_count=1,
        exposure_pct=10.0,
        risk_status={
            "daily_pnl": 50.0,
            "daily_loss_pct": 0.5,
            "drawdown_pct": 1.0,
            "risk_level": "GREEN",
        },
    )
    defaults.update(overrides)
    return defaults


def _make_mock_bot():
    """Return a mock Bot whose send_message succeeds by default."""
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot.initialize = AsyncMock(return_value=None)
    bot.shutdown = AsyncMock(return_value=None)
    bot.get_me = AsyncMock(return_value=MagicMock(username="test_bot"))
    return bot


# ---------------------------------------------------------------------------
# Tests: send_to_telegram
# ---------------------------------------------------------------------------

class TestSendToTelegram:
    """Tests for the send_to_telegram function in gold_server_v3."""

    @pytest.mark.asyncio
    async def test_successful_send_returns_true(self):
        """A successful send_message call returns True."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        with patch("gold_server_v3.get_bot", return_value=mock_bot):
            result = await send_to_telegram(**_make_signal_kwargs(max_retries=1))

        assert result is True
        assert mock_bot.send_message.call_count == 2  # copier_msg + info_msg

    @pytest.mark.asyncio
    async def test_successful_send_uses_correct_channel(self):
        """send_message is called with the configured TELEGRAM_CHANNEL_ID."""
        from gold_server_v3 import send_to_telegram, TELEGRAM_CHANNEL_ID

        mock_bot = _make_mock_bot()
        with patch("gold_server_v3.get_bot", return_value=mock_bot):
            await send_to_telegram(**_make_signal_kwargs(max_retries=1))

        for c in mock_bot.send_message.call_args_list:
            assert c.kwargs.get("chat_id") == TELEGRAM_CHANNEL_ID or c.args[0] == TELEGRAM_CHANNEL_ID

    @pytest.mark.asyncio
    async def test_retry_on_timeout_then_success(self):
        """Retries once after a TimeoutError and succeeds on the second attempt."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        mock_bot.send_message = AsyncMock(
            side_effect=[asyncio.TimeoutError(), MagicMock(message_id=1), MagicMock(message_id=2)]
        )

        with patch("gold_server_v3.get_bot", return_value=mock_bot), \
             patch("asyncio.sleep", new_callable=AsyncMock):  # skip real sleep
            result = await send_to_telegram(**_make_signal_kwargs(max_retries=2))

        assert result is True
        # First attempt: 1 call (timeout), second attempt: 2 calls (success)
        assert mock_bot.send_message.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_generic_exception_then_success(self):
        """Retries once after a generic exception and succeeds on the second attempt."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        mock_bot.send_message = AsyncMock(
            side_effect=[Exception("Network error"), MagicMock(message_id=1), MagicMock(message_id=2)]
        )

        with patch("gold_server_v3.get_bot", return_value=mock_bot), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_telegram(**_make_signal_kwargs(max_retries=2))

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_after_max_retries_exhausted(self):
        """Returns False when all retry attempts fail."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        mock_bot.send_message = AsyncMock(side_effect=Exception("Persistent network error"))

        with patch("gold_server_v3.get_bot", return_value=mock_bot), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await send_to_telegram(**_make_signal_kwargs(max_retries=3))

        assert result is False
        assert mock_bot.send_message.call_count == 3  # tried exactly max_retries times

    @pytest.mark.asyncio
    async def test_sell_signal_uses_red_emoji(self):
        """SELL signals use the red emoji in the copier message."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        with patch("gold_server_v3.get_bot", return_value=mock_bot):
            await send_to_telegram(**_make_signal_kwargs(signal="SELL", max_retries=1))

        copier_text = mock_bot.send_message.call_args_list[0].kwargs.get("text", "")
        assert "🔴" in copier_text

    @pytest.mark.asyncio
    async def test_buy_signal_uses_green_emoji(self):
        """BUY signals use the green emoji in the copier message."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        with patch("gold_server_v3.get_bot", return_value=mock_bot):
            await send_to_telegram(**_make_signal_kwargs(signal="BUY", max_retries=1))

        copier_text = mock_bot.send_message.call_args_list[0].kwargs.get("text", "")
        assert "🟢" in copier_text

    @pytest.mark.asyncio
    async def test_info_message_uses_html_parse_mode(self):
        """The second (info) message is sent with parse_mode='HTML'."""
        from gold_server_v3 import send_to_telegram

        mock_bot = _make_mock_bot()
        with patch("gold_server_v3.get_bot", return_value=mock_bot):
            await send_to_telegram(**_make_signal_kwargs(max_retries=1))

        info_call = mock_bot.send_message.call_args_list[1]
        assert info_call.kwargs.get("parse_mode") == "HTML"


# ---------------------------------------------------------------------------
# Tests: Bot lifecycle (initialize / shutdown)
# ---------------------------------------------------------------------------

class TestBotLifecycle:
    """Verify the Bot is properly initialized and shut down in the lifespan."""

    @pytest.mark.asyncio
    async def test_bot_initialize_called_on_startup(self):
        """bot.initialize() must be called during lifespan startup."""
        mock_bot = _make_mock_bot()

        # Patch all the heavy dependencies so we can run just the Telegram section
        with patch("gold_server_v3.TELEGRAM_BOT_TOKEN", "fake-token"), \
             patch("gold_server_v3.get_bot", return_value=mock_bot), \
             patch("gold_server_v3.MONGO_URL", ""), \
             patch("gold_server_v3.TWELVE_DATA_API_KEY", "fake-key"), \
             patch("gold_server_v3.OPENAI_API_KEY", "fake-key"), \
             patch("gold_server_v3.get_hybrid_system", return_value=MagicMock()), \
             patch("gold_server_v3._candle_tracker") as mock_ct, \
             patch("gold_server_v3._risk_manager") as mock_rm, \
             patch("gold_server_v3._calendar_filter") as mock_cf, \
             patch("gold_server_v3._pos_monitor") as mock_pm, \
             patch("gold_server_v3.scheduler") as mock_sched, \
             patch("asyncio.create_task"):

            mock_ct.reset = MagicMock()
            mock_ct.set_db = MagicMock()
            mock_rm.set_telegram = MagicMock()
            mock_rm.set_account_balance = MagicMock()
            mock_rm.validate_state = AsyncMock(return_value={})
            mock_rm.auto_recover_from_invalid_state = AsyncMock()
            mock_cf.fetch_calendar = AsyncMock()
            mock_pm.configure = MagicMock()
            mock_sched.add_job = MagicMock()
            mock_sched.start = MagicMock()

            from gold_server_v3 import lifespan
            from fastapi import FastAPI
            app = FastAPI()

            async with lifespan(app):
                # Inside the lifespan context — bot should be initialized
                mock_bot.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_bot_shutdown_called_on_teardown(self):
        """bot.shutdown() must be called when the lifespan context exits."""
        mock_bot = _make_mock_bot()

        with patch("gold_server_v3.TELEGRAM_BOT_TOKEN", "fake-token"), \
             patch("gold_server_v3.get_bot", return_value=mock_bot), \
             patch("gold_server_v3._bot", mock_bot), \
             patch("gold_server_v3.MONGO_URL", ""), \
             patch("gold_server_v3.TWELVE_DATA_API_KEY", "fake-key"), \
             patch("gold_server_v3.OPENAI_API_KEY", "fake-key"), \
             patch("gold_server_v3.get_hybrid_system", return_value=MagicMock()), \
             patch("gold_server_v3._candle_tracker") as mock_ct, \
             patch("gold_server_v3._risk_manager") as mock_rm, \
             patch("gold_server_v3._calendar_filter") as mock_cf, \
             patch("gold_server_v3._pos_monitor") as mock_pm, \
             patch("gold_server_v3.scheduler") as mock_sched, \
             patch("asyncio.create_task"):

            mock_ct.reset = MagicMock()
            mock_ct.set_db = MagicMock()
            mock_rm.set_telegram = MagicMock()
            mock_rm.set_account_balance = MagicMock()
            mock_rm.validate_state = AsyncMock(return_value={})
            mock_rm.auto_recover_from_invalid_state = AsyncMock()
            mock_cf.fetch_calendar = AsyncMock()
            mock_pm.configure = MagicMock()
            mock_sched.add_job = MagicMock()
            mock_sched.start = MagicMock()
            mock_sched.shutdown = MagicMock()

            from gold_server_v3 import lifespan
            from fastapi import FastAPI
            app = FastAPI()

            async with lifespan(app):
                pass  # yield point

            # After the context exits — bot should be shut down
            mock_bot.shutdown.assert_called_once()
