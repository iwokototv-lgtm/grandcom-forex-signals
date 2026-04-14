# core/gatekeeper.py
# ============================================================
# FULL EXECUTION GATEKEEPER  — Production Merged Version
# ============================================================
# Single source of truth — imported by server.py AND gold_server.py
#
# Checks (in order):
#   1.  Signal age          — Gold: 30s | JPY: 6s | FX: 4s
#   2.  Future timestamp    — rejects clock-skewed signals
#   3.  Session filter      — Gold: 24/7 | Forex: London 07-16 + NY 12-21 UTC
#   4.  News filter         — placeholder (wire up ForexFactory API)
#   5.  Confidence          — min 70% (GK_MIN_CONFIDENCE)
#   6.  Max open trades     — hard cap (GK_MAX_OPEN_TRADES)
#   7.  Duplicate trade     — same symbol + side already open
#   8.  Price sanity        — entry / sl / tp must all be > 0
#   9.  Direction structure — BUY: tp>entry>sl | SELL: tp<entry<sl
#  10.  Trend filter        — BUY must be above EMA50, SELL below EMA50
#  11.  Risk / Reward       — Gold: min 1.8 via validate_gold_trade()
#                             FX/JPY: pip-based min GK_MIN_RR
#  12.  Slippage (price)    — abs(entry - current_price) per-asset threshold
#  13.  Slippage (pips)     — secondary pip-based check
#  14.  Spread              — per-asset pip limit
#  15.  EMA-50 proximity    — per-asset pip distance
#
# Env-var overrides (hot-reloadable via Railway):
#   GK_MIN_RR                (default 1.5)
#   GK_MAX_SIGNAL_AGE        (default 4s — Forex base)
#   GK_MAX_SIGNAL_AGE_GOLD   (default 30s)
#   GK_MAX_SIGNAL_AGE_JPY    (default 6s)
#   GK_MAX_OPEN_TRADES       (default 2)
#   GK_MIN_CONFIDENCE        (default 70)
#   GK_MAX_SPREAD            (default 3 pips)
#   GK_MAX_SLIPPAGE          (default 3 pips)
#   GK_RISK_PER_TRADE        (default 0.01 = 1%)
#   GK_PRICE_THRESHOLD_GOLD  (default 0.50)
#   GK_PRICE_THRESHOLD_JPY   (default 0.03)
#   GK_PRICE_THRESHOLD_FX    (default 0.0002)
#   GK_LOG_FILE              (default gatekeeper_trades.jsonl)
# ============================================================

import os
import json as _json
import logging
from datetime import datetime, timezone as _tz, timedelta

_gk_logger  = logging.getLogger("execution_gatekeeper")
_GK_LOG_FILE: str = os.getenv("GK_LOG_FILE", "gatekeeper_trades.jsonl")


def _gk_log(symbol: str, side: str, result: dict) -> None:
    """Append every gatekeeper decision to JSONL log + Python logger."""
    _gk_logger.info(
        "%s %s %s — %s",
        result.get("status"), symbol, side,
        result.get("reason", f"R:R={result.get('rr')}")
    )
    if _GK_LOG_FILE:
        try:
            entry = {
                "ts":          datetime.utcnow().isoformat(),
                "symbol":      symbol,
                "side":        side,
                "status":      result.get("status"),
                "reason":      result.get("reason", ""),
                "rr":          result.get("rr"),
                "symbol_type": result.get("symbol_type"),
            }
            with open(_GK_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry) + "\n")
        except OSError:
            pass


class ExecutionGatekeeper:
    """
    Production-grade, symbol-aware trade validator.
    Import and instantiate once — module-level singleton.
    """

    REQUIRED_KEYS = (
        "symbol", "side", "entry", "sl", "tp",
        "current_price", "spread", "timestamp", "confidence",
    )

    def __init__(
        self,
        min_rr:             float = float(os.getenv("GK_MIN_RR",            "1.5")),
        max_signal_age_sec: float = float(os.getenv("GK_MAX_SIGNAL_AGE",    "4")),
        max_open_trades:    int   = int(  os.getenv("GK_MAX_OPEN_TRADES",   "2")),
        min_confidence:     float = float(os.getenv("GK_MIN_CONFIDENCE",    "70")),
        max_spread_pips:    float = float(os.getenv("GK_MAX_SPREAD",        "3")),
        max_slippage_pips:  float = float(os.getenv("GK_MAX_SLIPPAGE",      "3")),
        risk_per_trade:     float = float(os.getenv("GK_RISK_PER_TRADE",    "0.01")),
    ):
        self.min_rr           = min_rr
        self.max_signal_age   = max_signal_age_sec
        self.max_open_trades  = max_open_trades
        self.min_confidence   = min_confidence
        self.max_spread       = max_spread_pips
        self.max_slippage     = max_slippage_pips
        self.risk_per_trade   = risk_per_trade

    # ================================================================
    # SYMBOL HELPERS
    # ================================================================

    def get_symbol_type(self, symbol: str) -> str:
        """Classify symbol → GOLD | JPY | FOREX."""
        s = symbol.upper()
        if "XAU" in s: return "GOLD"
        if "JPY" in s: return "JPY"
        return "FOREX"

    def get_pip_multiplier(self, symbol: str) -> float:
        """
        Pip multiplier:
          FOREX : 10,000  (EURUSD  0.0001 = 1 pip)
          JPY   :    100  (USDJPY  0.01   = 1 pip)
          GOLD  :    100  (XAUUSD  0.01   = 1 pip)
        """
        t = self.get_symbol_type(symbol)
        return 100.0 if t in ("JPY", "GOLD") else 10_000.0

    def get_thresholds(self, symbol: str) -> dict:
        """
        Per-asset thresholds (pips / price units).
        """
        t = self.get_symbol_type(symbol)
        if t == "GOLD":
            return {
                "slippage":        10,
                "spread":          30,
                "ema_distance":    50,
                "price_threshold": float(os.getenv("GK_PRICE_THRESHOLD_GOLD", "0.50")),
            }
        elif t == "JPY":
            return {
                "slippage":        3,
                "spread":          3,
                "ema_distance":    15,
                "price_threshold": float(os.getenv("GK_PRICE_THRESHOLD_JPY",  "0.03")),
            }
        else:
            return {
                "slippage":        2,
                "spread":          2,
                "ema_distance":    10,
                "price_threshold": float(os.getenv("GK_PRICE_THRESHOLD_FX",   "0.0002")),
            }

    # ================================================================
    # CORE CALCULATIONS
    # ================================================================

    def price_to_pips(self, price_diff: float, symbol: str) -> float:
        """Convert a raw price difference to pips (always positive)."""
        return abs(price_diff) * self.get_pip_multiplier(symbol)

    def calculate_rr(self, entry: float, sl: float, tp: float, symbol: str) -> float:
        """R:R using pip-based distances. Returns 0.0 if risk is zero."""
        risk   = self.price_to_pips(entry - sl, symbol)
        reward = self.price_to_pips(tp - entry, symbol)
        return round(reward / risk, 4) if risk > 0 else 0.0

    def is_valid_entry(self, entry: float, ema50: float, symbol: str) -> bool:
        """Return True if entry is within EMA-50 proximity threshold."""
        thresholds = self.get_thresholds(symbol)
        distance   = self.price_to_pips(entry - ema50, symbol)
        return distance <= thresholds["ema_distance"]

    # ================================================================
    # ATR-BASED TP/SL CALCULATION
    # ================================================================

    def calculate_tp_sl(
        self, entry: float, atr: float, side: str,
        sl_mult: float = 1.5, tp_mult: float = 3.0
    ) -> tuple[float, float]:
        """
        Calculate SL and TP from ATR multipliers.
        Returns (sl_price, tp_price).
        Used by EA to verify signal levels are ATR-consistent.
        """
        if side == "BUY":
            sl = round(entry - atr * sl_mult, 5)
            tp = round(entry + atr * tp_mult, 5)
        else:
            sl = round(entry + atr * sl_mult, 5)
            tp = round(entry - atr * tp_mult, 5)
        return sl, tp

    # ================================================================
    # POSITION SIZING
    # ================================================================

    def position_size(
        self, balance: float, entry: float, sl: float, symbol: str,
        risk_pct: float | None = None,
    ) -> float:
        """
        Risk-based position sizing.
        Returns lot size rounded to 2 decimal places.
        risk_pct defaults to self.risk_per_trade (env GK_RISK_PER_TRADE = 1%).
        """
        if risk_pct is None:
            risk_pct = self.risk_per_trade
        risk_amount = balance * risk_pct
        pip_risk    = self.price_to_pips(entry - sl, symbol)
        if pip_risk == 0:
            return 0.0
        return round(risk_amount / pip_risk, 2)

    # ================================================================
    # DUPLICATE TRADE PROTECTION
    # ================================================================

    def is_duplicate_trade(self, signal: dict, open_trades: list) -> bool:
        """Return True if same symbol+side is already open."""
        sym  = signal.get("symbol", "")
        side = signal.get("side", "")
        for trade in open_trades:
            if not isinstance(trade, dict):
                continue
            if trade.get("symbol") == sym and trade.get("side") == side:
                return True
        return False

    # ================================================================
    # SESSION FILTER
    # ================================================================

    def is_valid_session(self, current_time: datetime, symbol: str = "") -> bool:
        """
        Gold (XAU): 24/7 — always allowed.
        Forex/JPY:  London 07-16 UTC  or  New York 12-21 UTC.
        """
        if "XAU" in symbol.upper():
            return True
        hour    = current_time.hour
        london  = 7  <= hour < 16
        newyork = 12 <= hour < 21
        return london or newyork

    # ================================================================
    # NEWS FILTER  (placeholder)
    # ================================================================

    def is_high_impact_news_near(self, current_time: datetime) -> bool:
        """
        Wire up ForexFactory / Investing.com API here.
        Return True when high-impact news is within ±15 min.
        """
        return False

    def is_confident_signal(self, confidence: float) -> bool:
        return confidence >= self.min_confidence

    # ================================================================
    # GOLD-SPECIFIC VALIDATION
    # ================================================================

    def validate_gold_trade(
        self, entry: float, sl: float, tp: float
    ) -> tuple[bool, float | str]:
        """
        Gold validator using RAW PRICE distances (not pips).

        Rules:
          TP distance ≥ 3.0   — no noise trades
          SL distance ≥ 3.0   — no hairline stops
          SL distance ≤ 150.0 — cap maximum risk
          R:R ≥ 1.8

        Returns:
          (True,  rr: float)   on approval
          (False, reason: str) on rejection
        """
        tp_dist = abs(tp - entry)
        sl_dist = abs(entry - sl)

        if tp_dist < 3.0 or sl_dist < 3.0:
            return False, f"Gold TP/SL too small (TP={tp_dist:.2f}, SL={sl_dist:.2f}, min=3.0)"
        if sl_dist > 150.0:
            return False, f"Gold SL too wide: {sl_dist:.2f} (max 150.0)"
        rr = tp_dist / sl_dist
        if rr < 1.8:
            return False, f"Gold R:R too low: {rr:.2f} (min 1.8)"
        return True, round(rr, 4)

    # ================================================================
    # REJECT HELPER
    # ================================================================

    def reject(self, reason: str) -> dict:
        return {"status": "REJECT", "reason": reason}

    # ================================================================
    # MAIN VALIDATE()
    # ================================================================

    def validate(self, signal: dict, open_trades: list = None) -> dict:
        """
        Run all production checks against a signal dict.

        Required signal keys:
            symbol, side (BUY/SELL), entry, sl, tp,
            current_price, spread, timestamp (ISO-8601), confidence

        Optional:
            ema50      (float, defaults to entry — skips EMA proximity check)

        Returns:
            {"status": "EXECUTE", "rr": float, "confidence": float, "symbol_type": str}
            {"status": "REJECT",  "reason": str}
        """
        if open_trades is None:
            open_trades = []

        try:
            # ── Required keys ────────────────────────────────────
            for key in self.REQUIRED_KEYS:
                if key not in signal:
                    return self.reject(f"Missing required field: '{key}'")

            symbol        = str(signal["symbol"]).upper()
            side          = str(signal["side"]).upper()
            entry         = float(signal["entry"])
            sl            = float(signal["sl"])
            tp            = float(signal["tp"])
            current_price = float(signal["current_price"])
            spread        = float(signal["spread"])
            timestamp     = signal["timestamp"]
            ema50         = float(signal.get("ema50", entry))
            confidence    = float(signal["confidence"])

            thresholds = self.get_thresholds(symbol)
            _stype     = self.get_symbol_type(symbol)

            # ── 1. Timestamp parse ────────────────────────────────
            signal_time = datetime.fromisoformat(timestamp)
            if signal_time.tzinfo is None:
                signal_time = signal_time.replace(tzinfo=_tz.utc)
            signal_time = signal_time.astimezone(_tz.utc)
            now         = datetime.now(_tz.utc)
            age         = (now - signal_time).total_seconds()

            # ── 2. Future timestamp guard ─────────────────────────
            if age < 0:
                return self.reject(
                    f"Signal in the future by {abs(age):.1f}s — clock skew or bad data"
                )

            # ── 3. Signal age per-asset ───────────────────────────
            if _stype == "GOLD":
                _max_age = float(os.getenv("GK_MAX_SIGNAL_AGE_GOLD", "30"))
            elif _stype == "JPY":
                _max_age = float(os.getenv("GK_MAX_SIGNAL_AGE_JPY",  "6"))
            else:
                _max_age = self.max_signal_age
            if age > _max_age:
                return self.reject(f"Signal too old: {age:.1f}s (max {_max_age}s for {_stype})")

            # ── 4. Session filter ─────────────────────────────────
            if not self.is_valid_session(signal_time, symbol):
                return self.reject(
                    f"Outside session (London 07-16 / NY 12-21 UTC) "
                    f"— hour: {signal_time.hour} UTC"
                )

            # ── 5. News filter ────────────────────────────────────
            if self.is_high_impact_news_near(signal_time):
                return self.reject("High-impact news nearby — blocked")

            # ── 6. Confidence ─────────────────────────────────────
            if not self.is_confident_signal(confidence):
                return self.reject(
                    f"Low confidence: {confidence:.1f}% (min {self.min_confidence}%)"
                )

            # ── 7. Max open trades ────────────────────────────────
            if len(open_trades) >= self.max_open_trades:
                return self.reject(
                    f"Max open trades: {len(open_trades)}/{self.max_open_trades}"
                )

            # ── 8. Duplicate trade ────────────────────────────────
            if self.is_duplicate_trade(signal, open_trades):
                return self.reject(f"Duplicate: {symbol} {side} already open")

            # ── 9. Price sanity ───────────────────────────────────
            for label, val in (
                ("entry", entry), ("sl", sl), ("tp", tp), ("current_price", current_price)
            ):
                if val <= 0:
                    return self.reject(f"Invalid price: {label}={val} — must be > 0")

            # ── 10. Direction structure ───────────────────────────
            if side == "BUY":
                if not (tp > entry > sl):
                    return self.reject(
                        f"Invalid BUY: entry={entry}, TP={tp}, SL={sl}"
                    )
            elif side == "SELL":
                if not (tp < entry < sl):
                    return self.reject(
                        f"Invalid SELL: entry={entry}, TP={tp}, SL={sl}"
                    )
            else:
                return self.reject(f"Unknown side: {side!r}")

            # ── 11. Trend filter (EMA50) ──────────────────────────
            # BUY must be above EMA50 | SELL must be below EMA50
            # Skip when ema50 not provided (ema50 == entry)
            if ema50 != entry:
                if side == "BUY" and current_price < ema50:
                    return self.reject(
                        f"BUY against trend — price {current_price} < EMA50 {ema50}"
                    )
                if side == "SELL" and current_price > ema50:
                    return self.reject(
                        f"SELL against trend — price {current_price} > EMA50 {ema50}"
                    )

            # ── 12. Risk / Reward ─────────────────────────────────
            if _stype == "GOLD":
                gold_valid, gold_result = self.validate_gold_trade(entry, sl, tp)
                if not gold_valid:
                    return self.reject(gold_result)
                rr = gold_result
            else:
                rr = self.calculate_rr(entry, sl, tp, symbol)
                if rr < self.min_rr:
                    return self.reject(f"R:R too low: {rr:.2f} (min {self.min_rr})")

            # ── 13. Slippage — price units ────────────────────────
            price_dist = abs(entry - current_price)
            if price_dist > thresholds["price_threshold"]:
                return self.reject(
                    f"Price moved too far: {price_dist:.5f} "
                    f"(max {thresholds['price_threshold']} for {_stype})"
                )

            # ── 14. Slippage — pips ───────────────────────────────
            slip_pips = self.price_to_pips(current_price - entry, symbol)
            if slip_pips > thresholds["slippage"]:
                return self.reject(
                    f"High slippage: {slip_pips:.2f} pips "
                    f"(max {thresholds['slippage']} for {_stype})"
                )

            # ── 15. Spread ────────────────────────────────────────
            if spread <= 0:
                return self.reject("Invalid spread: must be > 0")
            if spread > thresholds["spread"]:
                return self.reject(
                    f"High spread: {spread:.2f} pips "
                    f"(max {thresholds['spread']} for {_stype})"
                )

            # ── 16. EMA-50 proximity (Gold skipped) ───────────────
            if ema50 != entry and _stype != "GOLD":
                if not self.is_valid_entry(entry, ema50, symbol):
                    dist = self.price_to_pips(entry - ema50, symbol)
                    return self.reject(
                        f"Entry too far from EMA50: {dist:.1f} pips "
                        f"(max {thresholds['ema_distance']} for {_stype})"
                    )

            # ✅ All checks passed ──────────────────────────────────
            return {
                "status":      "EXECUTE",
                "rr":          round(rr, 2),
                "confidence":  round(confidence, 1),
                "symbol_type": _stype,
            }

        except (ValueError, TypeError) as e:
            return self.reject(f"Invalid signal data — {type(e).__name__}: {e}")
        except Exception as e:
            return self.reject(f"Gatekeeper exception [{type(e).__name__}]: {e}")

    # ================================================================
    # FULL RUN HELPER  (validate + position size in one call)
    # ================================================================

    def run(
        self,
        signal:      dict,
        open_trades: list  = None,
        balance:     float = 10000.0,
        ema50:       float | None = None,
    ) -> tuple[bool, str, str, float | None]:
        """
        Convenience wrapper for FastAPI endpoints and MT5 EA.
        Injects ema50 into signal dict if provided.
        Returns (approved, code, reason, lot_size).
        lot_size is None on rejection.
        """
        if open_trades is None:
            open_trades = []
        if ema50 is not None:
            signal = {**signal, "ema50": ema50}

        result = self.validate(signal, open_trades)
        if result["status"] != "EXECUTE":
            return False, "REJECTED", result.get("reason", ""), None

        lot = self.position_size(
            balance=balance,
            entry=float(signal.get("entry", 0)),
            sl=float(signal.get("sl", 0)),
            symbol=str(signal.get("symbol", "")),
        )
        approved_reason = (
            f"Approved — R:R={result['rr']} "
            f"conf={result['confidence']}% "
            f"({result['symbol_type']}) lot={lot}"
        )
        return True, "OK", approved_reason, lot


# ================================================================
# MODULE-LEVEL SINGLETON — import this in server.py / gold_server.py
# ================================================================

_gatekeeper = ExecutionGatekeeper()


def run_execution_gatekeeper(
    pair:          str,
    signal_type:   str,
    entry_price:   float,
    tp1:           float,
    sl_price:      float,
    current_price: float,
    spread_pips:   float,
    ema50:         float,
    signal_ts_iso: str,
    open_trades:   list,
    confidence:    float = 0.0,
    balance:       float = 10000.0,
) -> tuple[bool, str, str]:
    """
    Thin wrapper around ExecutionGatekeeper.validate().
    Returns (approved: bool, reason_code: str, reason: str).
    Used directly by server.py and gold_server.py signal pipelines.
    """
    signal = {
        "symbol":        pair,
        "side":          signal_type,
        "entry":         entry_price,
        "sl":            sl_price,
        "tp":            tp1,
        "current_price": current_price,
        "spread":        spread_pips,
        "timestamp":     signal_ts_iso,
        "ema50":         ema50,
        "confidence":    confidence,
    }

    result = _gatekeeper.validate(signal, open_trades)
    _gk_log(pair, signal_type, result)

    if result["status"] == "EXECUTE":
        lot = _gatekeeper.position_size(balance, entry_price, sl_price, pair)
        return (
            True, "OK",
            f"Approved — R:R={result['rr']} "
            f"conf={result['confidence']}% "
            f"({result['symbol_type']}) lot={lot}"
        )
    return False, "REJECTED", result.get("reason", "Unknown rejection")
