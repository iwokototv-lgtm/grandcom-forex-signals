"""
SMC/ICT Strategy Module — v3.0
Smart Money Concepts & Inner Circle Trader methodology.
Extends the existing SmartMoneyAnalyzer with strategy-level signal generation.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


class SMCICTStrategy:
    """
    Institutional SMC/ICT strategy layer.

    Builds on raw SMC analysis to produce actionable BUY/SELL/NEUTRAL signals
    with entry, TP, and SL levels derived from:
    - Order Block (OB) entries
    - Fair Value Gap (FVG) fills
    - Liquidity sweep reversals
    - Break of Structure (BOS) continuations
    - Change of Character (ChoCH) reversals
    - Premium / Discount zone bias
    """

    def __init__(self) -> None:
        self.swing_lookback: int = 10
        self.ob_lookback: int = 20
        self.fvg_min_pct: float = 0.0003  # 0.03% minimum FVG size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_signal(
        self, df: pd.DataFrame, symbol: str, regime: dict | None = None
    ) -> dict[str, Any]:
        """
        Generate an SMC/ICT trading signal from OHLCV data.

        Args:
            df:      OHLCV DataFrame (chronological, oldest first).
            symbol:  Trading pair identifier.
            regime:  Optional regime dict from RegimeDetector.

        Returns:
            Signal dict with keys: signal, confidence, entry, tp_levels,
            sl, analysis, strategy, smc_context.
        """
        try:
            if len(df) < 50:
                return self._neutral("Insufficient data", symbol)

            # Core SMC components
            swing_highs, swing_lows = self._find_swings(df)
            order_blocks = self._find_order_blocks(df, swing_highs, swing_lows)
            fvgs = self._find_fvgs(df)
            liquidity_levels = self._find_liquidity(df, swing_highs, swing_lows)
            bos_choch = self._detect_bos_choch(df, swing_highs, swing_lows)
            pd_zone = self._premium_discount_zone(df, swing_highs, swing_lows)

            # Aggregate bias
            signal, confidence, context = self._aggregate_bias(
                df, order_blocks, fvgs, liquidity_levels, bos_choch, pd_zone, regime
            )

            if signal == "NEUTRAL":
                return self._neutral("No SMC confluence", symbol)

            # Build levels
            entry, tp_levels, sl = self._build_levels(
                df, signal, order_blocks, fvgs, pd_zone
            )

            analysis = self._build_analysis(signal, context, pd_zone, bos_choch)

            return {
                "signal": signal,
                "confidence": round(confidence, 1),
                "entry": round(entry, 2),
                "tp_levels": [round(t, 2) for t in tp_levels],
                "sl": round(sl, 2),
                "analysis": analysis,
                "strategy": "SMC_ICT",
                "smc_context": context,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as exc:
            logger.error(f"[SMC/ICT] Signal generation error for {symbol}: {exc}")
            return self._neutral(f"Error: {exc}", symbol)

    # ------------------------------------------------------------------
    # Swing Points
    # ------------------------------------------------------------------

    def _find_swings(
        self, df: pd.DataFrame
    ) -> tuple[list[dict], list[dict]]:
        highs: list[dict] = []
        lows: list[dict] = []
        n = self.swing_lookback

        for i in range(n, len(df) - n):
            window_h = df["high"].iloc[i - n : i + n + 1]
            window_l = df["low"].iloc[i - n : i + n + 1]
            if df["high"].iloc[i] == window_h.max():
                highs.append({"index": i, "price": float(df["high"].iloc[i])})
            if df["low"].iloc[i] == window_l.min():
                lows.append({"index": i, "price": float(df["low"].iloc[i])})

        return highs, lows

    # ------------------------------------------------------------------
    # Order Blocks
    # ------------------------------------------------------------------

    def _find_order_blocks(
        self,
        df: pd.DataFrame,
        swing_highs: list[dict],
        swing_lows: list[dict],
    ) -> list[dict]:
        obs: list[dict] = []
        lookback = min(self.ob_lookback, len(df) - 1)

        for i in range(len(df) - lookback, len(df) - 1):
            candle = df.iloc[i]
            next_c = df.iloc[i + 1]

            body = abs(float(candle["close"]) - float(candle["open"]))
            total = float(candle["high"]) - float(candle["low"])
            if total == 0:
                continue
            body_ratio = body / total

            # Bearish OB: bullish candle followed by strong bearish move
            if (
                float(candle["close"]) > float(candle["open"])
                and float(next_c["close"]) < float(candle["low"])
                and body_ratio > 0.5
            ):
                obs.append(
                    {
                        "type": "BEARISH_OB",
                        "top": float(candle["high"]),
                        "bottom": float(candle["low"]),
                        "index": i,
                        "strength": body_ratio,
                    }
                )

            # Bullish OB: bearish candle followed by strong bullish move
            if (
                float(candle["close"]) < float(candle["open"])
                and float(next_c["close"]) > float(candle["high"])
                and body_ratio > 0.5
            ):
                obs.append(
                    {
                        "type": "BULLISH_OB",
                        "top": float(candle["high"]),
                        "bottom": float(candle["low"]),
                        "index": i,
                        "strength": body_ratio,
                    }
                )

        return obs[-10:]  # Keep most recent

    # ------------------------------------------------------------------
    # Fair Value Gaps
    # ------------------------------------------------------------------

    def _find_fvgs(self, df: pd.DataFrame) -> list[dict]:
        fvgs: list[dict] = []
        for i in range(1, len(df) - 1):
            prev_h = float(df["high"].iloc[i - 1])
            prev_l = float(df["low"].iloc[i - 1])
            next_h = float(df["high"].iloc[i + 1])
            next_l = float(df["low"].iloc[i + 1])
            mid = float(df["close"].iloc[i])

            # Bullish FVG: gap between prev high and next low
            if next_l > prev_h:
                gap_size = (next_l - prev_h) / mid
                if gap_size >= self.fvg_min_pct:
                    fvgs.append(
                        {
                            "type": "BULLISH_FVG",
                            "top": next_l,
                            "bottom": prev_h,
                            "index": i,
                            "size_pct": round(gap_size * 100, 4),
                        }
                    )

            # Bearish FVG: gap between prev low and next high
            if prev_l > next_h:
                gap_size = (prev_l - next_h) / mid
                if gap_size >= self.fvg_min_pct:
                    fvgs.append(
                        {
                            "type": "BEARISH_FVG",
                            "top": prev_l,
                            "bottom": next_h,
                            "index": i,
                            "size_pct": round(gap_size * 100, 4),
                        }
                    )

        return fvgs[-15:]

    # ------------------------------------------------------------------
    # Liquidity Levels
    # ------------------------------------------------------------------

    def _find_liquidity(
        self,
        df: pd.DataFrame,
        swing_highs: list[dict],
        swing_lows: list[dict],
    ) -> dict[str, Any]:
        current_price = float(df["close"].iloc[-1])

        buy_side: list[float] = [
            sh["price"] for sh in swing_highs if sh["price"] > current_price
        ]
        sell_side: list[float] = [
            sl["price"] for sl in swing_lows if sl["price"] < current_price
        ]

        nearest_buy = min(buy_side, default=None)
        nearest_sell = max(sell_side, default=None)

        swept_high = (
            nearest_buy is not None and current_price > nearest_buy * 0.999
        )
        swept_low = (
            nearest_sell is not None and current_price < nearest_sell * 1.001
        )

        return {
            "buy_side_liquidity": nearest_buy,
            "sell_side_liquidity": nearest_sell,
            "swept_high": swept_high,
            "swept_low": swept_low,
            "current_price": current_price,
        }

    # ------------------------------------------------------------------
    # BOS / ChoCH
    # ------------------------------------------------------------------

    def _detect_bos_choch(
        self,
        df: pd.DataFrame,
        swing_highs: list[dict],
        swing_lows: list[dict],
    ) -> dict[str, Any]:
        if not swing_highs or not swing_lows:
            return {"bos": "NONE", "choch": "NONE", "bias": "NEUTRAL"}

        current_price = float(df["close"].iloc[-1])
        last_high = swing_highs[-1]["price"] if swing_highs else current_price
        last_low = swing_lows[-1]["price"] if swing_lows else current_price

        # BOS: price breaks beyond last swing
        bos_bullish = current_price > last_high
        bos_bearish = current_price < last_low

        # ChoCH: price reverses through opposite swing
        choch_bullish = (
            len(swing_lows) >= 2
            and swing_lows[-1]["price"] > swing_lows[-2]["price"]
            and current_price > last_high
        )
        choch_bearish = (
            len(swing_highs) >= 2
            and swing_highs[-1]["price"] < swing_highs[-2]["price"]
            and current_price < last_low
        )

        bos = "BULLISH" if bos_bullish else ("BEARISH" if bos_bearish else "NONE")
        choch = (
            "BULLISH" if choch_bullish else ("BEARISH" if choch_bearish else "NONE")
        )

        bias = "NEUTRAL"
        if bos_bullish or choch_bullish:
            bias = "BULLISH"
        elif bos_bearish or choch_bearish:
            bias = "BEARISH"

        return {"bos": bos, "choch": choch, "bias": bias}

    # ------------------------------------------------------------------
    # Premium / Discount Zone
    # ------------------------------------------------------------------

    def _premium_discount_zone(
        self,
        df: pd.DataFrame,
        swing_highs: list[dict],
        swing_lows: list[dict],
    ) -> dict[str, Any]:
        if not swing_highs or not swing_lows:
            return {"zone": "NEUTRAL", "position": 0.5, "equilibrium": 0.0}

        recent_high = max(sh["price"] for sh in swing_highs[-5:])
        recent_low = min(sl["price"] for sl in swing_lows[-5:])
        current = float(df["close"].iloc[-1])
        rng = recent_high - recent_low

        if rng == 0:
            return {"zone": "NEUTRAL", "position": 0.5, "equilibrium": current}

        position = (current - recent_low) / rng
        equilibrium = (recent_high + recent_low) / 2

        if position > 0.618:
            zone = "PREMIUM"
        elif position < 0.382:
            zone = "DISCOUNT"
        else:
            zone = "EQUILIBRIUM"

        return {
            "zone": zone,
            "position": round(position, 3),
            "equilibrium": round(equilibrium, 2),
            "range_high": round(recent_high, 2),
            "range_low": round(recent_low, 2),
        }

    # ------------------------------------------------------------------
    # Bias Aggregation
    # ------------------------------------------------------------------

    def _aggregate_bias(
        self,
        df: pd.DataFrame,
        order_blocks: list[dict],
        fvgs: list[dict],
        liquidity: dict,
        bos_choch: dict,
        pd_zone: dict,
        regime: dict | None,
    ) -> tuple[str, float, dict]:
        current = float(df["close"].iloc[-1])
        bullish_score = 0.0
        bearish_score = 0.0
        context: dict[str, Any] = {}

        # BOS/ChoCH bias (weight: 2)
        if bos_choch["bias"] == "BULLISH":
            bullish_score += 2.0
        elif bos_choch["bias"] == "BEARISH":
            bearish_score += 2.0
        context["bos_choch"] = bos_choch

        # Liquidity sweep reversal (weight: 2)
        if liquidity["swept_high"]:
            bearish_score += 2.0  # Swept buy-side → expect reversal down
            context["liquidity_sweep"] = "BEARISH_REVERSAL"
        elif liquidity["swept_low"]:
            bullish_score += 2.0  # Swept sell-side → expect reversal up
            context["liquidity_sweep"] = "BULLISH_REVERSAL"
        else:
            context["liquidity_sweep"] = "NONE"

        # Order block proximity (weight: 1.5)
        for ob in order_blocks[-5:]:
            if ob["type"] == "BULLISH_OB" and ob["bottom"] <= current <= ob["top"] * 1.002:
                bullish_score += 1.5
                context["active_ob"] = ob
                break
            if ob["type"] == "BEARISH_OB" and ob["bottom"] * 0.998 <= current <= ob["top"]:
                bearish_score += 1.5
                context["active_ob"] = ob
                break

        # FVG fill (weight: 1)
        for fvg in fvgs[-5:]:
            if fvg["type"] == "BULLISH_FVG" and fvg["bottom"] <= current <= fvg["top"]:
                bullish_score += 1.0
                context["active_fvg"] = fvg
                break
            if fvg["type"] == "BEARISH_FVG" and fvg["bottom"] <= current <= fvg["top"]:
                bearish_score += 1.0
                context["active_fvg"] = fvg
                break

        # Premium/Discount zone (weight: 1)
        zone = pd_zone.get("zone", "NEUTRAL")
        if zone == "DISCOUNT":
            bullish_score += 1.0
        elif zone == "PREMIUM":
            bearish_score += 1.0
        context["pd_zone"] = zone

        # Regime gate
        if regime:
            active = regime.get("active_strategies", [])
            regime_name = regime.get("regime_name", "")
            if "breakout" not in active and "pullback" not in active:
                # SMC works best in trending/breakout regimes
                bullish_score *= 0.7
                bearish_score *= 0.7
            context["regime"] = regime_name

        total = bullish_score + bearish_score
        if total == 0:
            return "NEUTRAL", 50.0, context

        if bullish_score > bearish_score and bullish_score >= 3.0:
            confidence = min(50.0 + (bullish_score / total) * 50.0, 95.0)
            return "BUY", confidence, context
        if bearish_score > bullish_score and bearish_score >= 3.0:
            confidence = min(50.0 + (bearish_score / total) * 50.0, 95.0)
            return "SELL", confidence, context

        return "NEUTRAL", 50.0, context

    # ------------------------------------------------------------------
    # Level Construction
    # ------------------------------------------------------------------

    def _build_levels(
        self,
        df: pd.DataFrame,
        signal: str,
        order_blocks: list[dict],
        fvgs: list[dict],
        pd_zone: dict,
    ) -> tuple[float, list[float], float]:
        current = float(df["close"].iloc[-1])
        atr = self._calc_atr(df)

        entry = current

        # Try to use OB midpoint as entry
        for ob in reversed(order_blocks):
            mid = (ob["top"] + ob["bottom"]) / 2
            if signal == "BUY" and ob["type"] == "BULLISH_OB" and mid < current * 1.005:
                entry = mid
                break
            if signal == "SELL" and ob["type"] == "BEARISH_OB" and mid > current * 0.995:
                entry = mid
                break

        if signal == "BUY":
            sl = round(entry - atr * 1.5, 2)
            tp1 = round(entry + atr * 2.0, 2)
            tp2 = round(entry + atr * 3.5, 2)
            tp3 = round(entry + atr * 5.0, 2)
        else:
            sl = round(entry + atr * 1.5, 2)
            tp1 = round(entry - atr * 2.0, 2)
            tp2 = round(entry - atr * 3.5, 2)
            tp3 = round(entry - atr * 5.0, 2)

        return entry, [tp1, tp2, tp3], sl

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _calc_atr(self, df: pd.DataFrame, window: int = 14) -> float:
        try:
            atr_series = ta.volatility.AverageTrueRange(
                df["high"], df["low"], df["close"], window=window
            ).average_true_range()
            return float(atr_series.iloc[-1])
        except Exception:
            return float((df["high"] - df["low"]).tail(14).mean())

    def _build_analysis(
        self,
        signal: str,
        context: dict,
        pd_zone: dict,
        bos_choch: dict,
    ) -> str:
        parts = [f"SMC/ICT {signal} signal."]
        if bos_choch["bos"] != "NONE":
            parts.append(f"BOS {bos_choch['bos']}.")
        if bos_choch["choch"] != "NONE":
            parts.append(f"ChoCH {bos_choch['choch']}.")
        if context.get("liquidity_sweep") not in (None, "NONE"):
            parts.append(f"Liquidity sweep: {context['liquidity_sweep']}.")
        if "active_ob" in context:
            ob = context["active_ob"]
            parts.append(f"Active {ob['type']} @ {ob['bottom']:.2f}-{ob['top']:.2f}.")
        if "active_fvg" in context:
            fvg = context["active_fvg"]
            parts.append(f"FVG fill {fvg['type']} ({fvg['size_pct']:.3f}%).")
        parts.append(f"Price in {pd_zone.get('zone', 'NEUTRAL')} zone.")
        return " ".join(parts)[:200]

    @staticmethod
    def _neutral(reason: str, symbol: str) -> dict[str, Any]:
        return {
            "signal": "NEUTRAL",
            "confidence": 50.0,
            "entry": 0.0,
            "tp_levels": [],
            "sl": 0.0,
            "analysis": reason,
            "strategy": "SMC_ICT",
            "smc_context": {},
            "timestamp": datetime.utcnow().isoformat(),
        }


# Module-level singleton
smc_ict_strategy = SMCICTStrategy()
