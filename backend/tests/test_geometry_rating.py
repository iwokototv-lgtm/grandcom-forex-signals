"""
Tests for Trade Geometry Rating — unit tests (no server required)
and integration tests against the /api/manager/geometry/* endpoints.

Unit tests run without any external dependencies.
Integration tests are skipped if the manager login fails.
"""
import pytest
import requests
import os

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://gold-signal-debug.preview.emergentagent.com",
).rstrip("/")

MANAGER_EMAIL    = os.environ.get("MANAGER_EMAIL",    "admin@forexsignals.com")
MANAGER_PASSWORD = os.environ.get("MANAGER_PASSWORD", "Admin@2024!Forex")


# ─────────────────────────────────────────────────────────────
# UNIT TESTS — pure Python, no HTTP
# ─────────────────────────────────────────────────────────────

class TestGeometryRaterUnit:
    """Unit tests for TradeGeometryRater (no server required)."""

    @pytest.fixture(autouse=True)
    def import_rater(self):
        """Import the rater; skip if backend deps are unavailable."""
        try:
            from ml_engine.trade_geometry_rater import (
                TradeGeometryRater,
                rate_entry_price,
                rate_stop_loss,
                rate_risk_reward,
                rate_take_profit,
                APPROVE_THRESHOLD,
                ADJUST_THRESHOLD,
            )
            self.TradeGeometryRater = TradeGeometryRater
            self.rate_entry_price   = rate_entry_price
            self.rate_stop_loss     = rate_stop_loss
            self.rate_risk_reward   = rate_risk_reward
            self.rate_take_profit   = rate_take_profit
            self.APPROVE_THRESHOLD  = APPROVE_THRESHOLD
            self.ADJUST_THRESHOLD   = ADJUST_THRESHOLD
        except ImportError:
            pytest.skip("ml_engine not importable in this environment")

    # ── Entry price ───────────────────────────────────────────

    def test_entry_score_in_range(self):
        result = self.rate_entry_price(
            "BUY", 1900.0, 1880.0, [1920.0, 1940.0, 1960.0]
        )
        assert 1.0 <= result["score"] <= 10.0
        assert result["label"] in ("EXCELLENT", "GOOD", "FAIR", "POOR")
        assert isinstance(result["rationale"], str)

    def test_entry_score_sell(self):
        result = self.rate_entry_price(
            "SELL", 1900.0, 1920.0, [1880.0, 1860.0]
        )
        assert 1.0 <= result["score"] <= 10.0

    def test_entry_score_with_structure(self):
        # Entry very close to recent low → should score well for BUY
        result = self.rate_entry_price(
            "BUY", 1900.0, 1880.0, [1930.0],
            recent_high=1950.0, recent_low=1899.0,
        )
        assert result["score"] >= 7.0

    def test_entry_score_with_atr_penalty(self):
        # SL distance > 2×ATR → penalty
        result = self.rate_entry_price(
            "BUY", 1900.0, 1800.0, [1950.0],
            atr=20.0,  # SL dist = 100, 2×ATR = 40 → penalty
        )
        assert result["score"] < 8.0

    # ── Stop loss ─────────────────────────────────────────────

    def test_sl_score_in_range(self):
        result = self.rate_stop_loss("BUY", 1900.0, 1880.0)
        assert 1.0 <= result["score"] <= 10.0
        assert "sl_distance_pct" in result

    def test_sl_tight_scores_low(self):
        # SL only 0.1% away — very tight
        result = self.rate_stop_loss("BUY", 1900.0, 1898.1)
        assert result["score"] < 6.0

    def test_sl_ideal_range_scores_high(self):
        # SL ~1% away — good
        result = self.rate_stop_loss("BUY", 1900.0, 1881.0)
        assert result["score"] >= 7.0

    def test_sl_very_wide_scores_low(self):
        # SL 8% away — very wide
        result = self.rate_stop_loss("BUY", 1900.0, 1748.0)
        assert result["score"] < 5.0

    def test_sl_with_atr_ideal(self):
        # SL at 1×ATR — ideal
        result = self.rate_stop_loss("BUY", 1900.0, 1880.0, atr=20.0)
        assert result["score"] >= 7.0
        assert result["sl_distance_atr"] == pytest.approx(1.0, abs=0.1)

    # ── Risk/Reward ───────────────────────────────────────────

    def test_rr_score_in_range(self):
        result = self.rate_risk_reward("BUY", 1900.0, 1880.0, [1940.0])
        assert 1.0 <= result["score"] <= 10.0
        assert "rr_tp1" in result

    def test_rr_2to1_scores_good(self):
        # Risk = 20, Reward = 40 → R:R = 2.0
        result = self.rate_risk_reward("BUY", 1900.0, 1880.0, [1940.0])
        assert result["rr_tp1"] == pytest.approx(2.0, abs=0.01)
        assert result["score"] >= 6.0

    def test_rr_3to1_scores_excellent(self):
        # Risk = 20, Reward = 60 → R:R = 3.0
        result = self.rate_risk_reward("BUY", 1900.0, 1880.0, [1960.0])
        assert result["rr_tp1"] == pytest.approx(3.0, abs=0.01)
        assert result["score"] >= 8.0

    def test_rr_below_1to1_scores_poor(self):
        # Risk = 20, Reward = 10 → R:R = 0.5
        result = self.rate_risk_reward("BUY", 1900.0, 1880.0, [1910.0])
        assert result["score"] <= 2.0

    def test_rr_blended_multiple_tps(self):
        result = self.rate_risk_reward(
            "BUY", 1900.0, 1880.0, [1940.0, 1960.0, 1980.0]
        )
        assert result["rr_blended"] is not None
        assert result["rr_blended"] > result["rr_tp1"]

    def test_rr_no_tp_levels(self):
        result = self.rate_risk_reward("BUY", 1900.0, 1880.0, [])
        assert result["score"] == 1.0

    # ── Take profit ───────────────────────────────────────────

    def test_tp_score_in_range(self):
        result = self.rate_take_profit("BUY", 1900.0, 1880.0, [1940.0])
        assert 1.0 <= result["score"] <= 10.0
        assert result["tp_count"] == 1

    def test_tp_single_level_scores_fair(self):
        result = self.rate_take_profit("BUY", 1900.0, 1880.0, [1940.0])
        assert result["score"] >= 5.0
        assert result["tp_count"] == 1

    def test_tp_three_levels_scores_high(self):
        result = self.rate_take_profit(
            "BUY", 1900.0, 1880.0, [1930.0, 1950.0, 1970.0]
        )
        assert result["score"] >= 8.0
        assert result["tp_count"] == 3

    def test_tp_no_levels_scores_poor(self):
        result = self.rate_take_profit("BUY", 1900.0, 1880.0, [])
        assert result["score"] == 1.0

    # ── Overall rater ─────────────────────────────────────────

    def test_overall_score_in_range(self):
        rater  = self.TradeGeometryRater()
        signal = {
            "type":        "BUY",
            "entry_price": 1900.0,
            "sl_price":    1880.0,
            "tp_levels":   [1940.0, 1960.0, 1980.0],
            "pair":        "XAUUSD",
        }
        result = rater.rate(signal)
        assert 1.0 <= result["overall_score"] <= 10.0

    def test_overall_recommendation_approve(self):
        rater  = self.TradeGeometryRater()
        # Good R:R, 3 TPs, reasonable SL
        signal = {
            "type":        "BUY",
            "entry_price": 1900.0,
            "sl_price":    1885.0,
            "tp_levels":   [1930.0, 1950.0, 1975.0],
        }
        result = rater.rate(signal)
        assert result["recommendation"] in ("APPROVE", "ADJUST")

    def test_overall_recommendation_reject(self):
        rater  = self.TradeGeometryRater()
        # Terrible R:R (below 1:1), no TPs
        signal = {
            "type":        "BUY",
            "entry_price": 1900.0,
            "sl_price":    1800.0,   # 100 pip risk
            "tp_levels":   [1910.0], # 10 pip reward → R:R = 0.1
        }
        result = rater.rate(signal)
        assert result["recommendation"] == "REJECT"

    def test_overall_has_all_components(self):
        rater  = self.TradeGeometryRater()
        signal = {
            "type":        "SELL",
            "entry_price": 1900.0,
            "sl_price":    1920.0,
            "tp_levels":   [1870.0, 1850.0],
        }
        result = rater.rate(signal)
        assert "components"         in result
        assert "entry"              in result["components"]
        assert "sl"                 in result["components"]
        assert "rr"                 in result["components"]
        assert "tp"                 in result["components"]
        assert "weights"            in result
        assert "improvement_hints"  in result

    def test_overall_with_market_context(self):
        rater  = self.TradeGeometryRater()
        signal = {
            "type":        "BUY",
            "entry_price": 1900.0,
            "sl_price":    1882.0,
            "tp_levels":   [1935.0, 1955.0],
        }
        ctx = {"recent_high": 1960.0, "recent_low": 1880.0, "atr": 18.0}
        result = rater.rate(signal, ctx)
        assert 1.0 <= result["overall_score"] <= 10.0

    def test_batch_rating(self):
        rater   = self.TradeGeometryRater()
        signals = [
            {"type": "BUY",  "entry_price": 1900.0, "sl_price": 1880.0, "tp_levels": [1940.0]},
            {"type": "SELL", "entry_price": 1900.0, "sl_price": 1920.0, "tp_levels": [1860.0]},
            {"type": "BUY",  "entry_price": 2000.0, "sl_price": 1980.0, "tp_levels": [2060.0, 2080.0]},
        ]
        results = rater.rate_batch(signals)
        assert len(results) == 3
        for r in results:
            assert 1.0 <= r["overall_score"] <= 10.0

    def test_error_handling_missing_fields(self):
        rater  = self.TradeGeometryRater()
        # Missing required fields — should return error result, not raise
        result = rater.rate({"type": "BUY"})
        assert result["recommendation"] == "REJECT"
        assert "error" in result


# ─────────────────────────────────────────────────────────────
# INTEGRATION TESTS — require running server
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def manager_token(api_client):
    resp = api_client.post(
        f"{BASE_URL}/api/manager/auth/login",
        json={"email": MANAGER_EMAIL, "password": MANAGER_PASSWORD},
    )
    if resp.status_code != 200:
        pytest.skip(f"Manager login failed: {resp.text}")
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def mgr_client(api_client, manager_token):
    api_client.headers.update({"Authorization": f"Bearer {manager_token}"})
    return api_client


class TestGeometryRatingAPI:
    """Integration tests for /api/manager/geometry/* endpoints."""

    def test_thresholds_endpoint(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/geometry/thresholds")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "weights"         in data
        assert "recommendations" in data
        assert "scale"           in data

    def test_thresholds_weights_sum_to_one(self, mgr_client):
        resp = mgr_client.get(f"{BASE_URL}/api/manager/geometry/thresholds")
        data = resp.json()
        weights = data["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_rate_buy_signal(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate",
            json={
                "signal_type": "BUY",
                "entry_price": 1900.0,
                "sl_price":    1882.0,
                "tp_levels":   [1930.0, 1950.0, 1975.0],
                "pair":        "XAUUSD",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "rating" in data
        rating = data["rating"]
        assert 1.0 <= rating["overall_score"] <= 10.0
        assert rating["recommendation"] in ("APPROVE", "ADJUST", "REJECT")

    def test_rate_sell_signal(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate",
            json={
                "signal_type": "SELL",
                "entry_price": 1900.0,
                "sl_price":    1918.0,
                "tp_levels":   [1870.0, 1850.0],
                "pair":        "XAUUSD",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_rate_with_market_context(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate",
            json={
                "signal_type": "BUY",
                "entry_price": 1900.0,
                "sl_price":    1882.0,
                "tp_levels":   [1935.0, 1955.0],
                "market_context": {
                    "recent_high": 1960.0,
                    "recent_low":  1880.0,
                    "atr":         18.0,
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_rate_requires_auth(self, api_client):
        resp = requests.post(
            f"{BASE_URL}/api/manager/geometry/rate",
            json={
                "signal_type": "BUY",
                "entry_price": 1900.0,
                "sl_price":    1880.0,
                "tp_levels":   [1940.0],
            },
        )
        assert resp.status_code in (401, 403)

    def test_rate_invalid_signal_type(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate",
            json={
                "signal_type": "HOLD",
                "entry_price": 1900.0,
                "sl_price":    1880.0,
                "tp_levels":   [1940.0],
            },
        )
        assert resp.status_code == 422

    def test_rate_negative_price(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate",
            json={
                "signal_type": "BUY",
                "entry_price": -100.0,
                "sl_price":    1880.0,
                "tp_levels":   [1940.0],
            },
        )
        assert resp.status_code == 422

    def test_rate_batch(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate-batch",
            json={
                "signals": [
                    {
                        "signal_type": "BUY",
                        "entry_price": 1900.0,
                        "sl_price":    1882.0,
                        "tp_levels":   [1930.0, 1950.0],
                    },
                    {
                        "signal_type": "SELL",
                        "entry_price": 1900.0,
                        "sl_price":    1918.0,
                        "tp_levels":   [1870.0],
                    },
                ]
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert "summary" in data
        assert "ratings" in data
        assert len(data["ratings"]) == 2

    def test_rate_batch_summary_counts(self, mgr_client):
        resp = mgr_client.post(
            f"{BASE_URL}/api/manager/geometry/rate-batch",
            json={
                "signals": [
                    {
                        "signal_type": "BUY",
                        "entry_price": 1900.0,
                        "sl_price":    1882.0,
                        "tp_levels":   [1930.0, 1950.0, 1975.0],
                    },
                ]
            },
        )
        data = resp.json()
        summary = data["summary"]
        total = summary["approve"] + summary["adjust"] + summary["reject"]
        assert total == 1
        assert "avg_score" in summary
