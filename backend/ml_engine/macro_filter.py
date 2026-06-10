"""
Macro Filter Engine
===================
Evaluates macro-economic context for gold (XAUUSD / XAUEUR) signals.

Three factors are scored independently:

  1. USD Strength (DXY)
       DXY > EMA20  → USD strong  → bearish for gold  (score = -1)
       DXY < EMA20  → USD weak    → bullish for gold   (score = +1)
       DXY ≈ EMA20  → neutral                          (score =  0)

  2. Real Rates Proxy (US 10Y yield direction)
       Yields rising  → real rates rising  → bearish for gold  (score = -1)
       Yields falling → real rates falling → bullish for gold   (score = +1)
       Flat           → neutral                                  (score =  0)

  3. Inflation Expectations (breakeven inflation direction)
       Inflation expectations rising  → bullish for gold   (score = +1)
       Inflation expectations falling → bearish for gold   (score = -1)
       Flat                           → neutral             (score =  0)

Composite macro score = sum(factors) / 3   ∈ [-1, +1]

Usage in signal generation:
  - macro_score > 0  → macro is bullish for gold
  - macro_score < 0  → macro is bearish for gold
  - If macro opposes the signal direction → reduce confidence by 20%
  - If macro aligns with the signal direction → no change (already baked in)

Data sources:
  - DXY: fetched from TwelveData (requires api_key) or from pre-fetched
    corr_dfs dict passed by the backtest engine.
  - US 10Y yield / breakeven: fetched from TwelveData symbols TNX / T10YIE
    when an api_key is available; otherwise falls back to neutral (0).
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"

# TwelveData symbol strings
_TD_SYMBOLS: Dict[str, str] = {
    "DXY":      "DXY",
    "US10Y":    "TNX",       # CBOE 10-Year Treasury Note Yield Index
    "BREAKEVEN": "T10YIE",   # 10-Year Breakeven Inflation Rate
}

# Neutral threshold: if DXY is within this % of EMA20, treat as neutral
_DXY_NEUTRAL_BAND_PCT = 0.002   # 0.2%

# Yield change threshold: if the 5-period change is within this band, treat as flat
_YIELD_FLAT_BAND = 0.05         # 5 basis points


class MacroFilter:
    """
    Macro-economic context filter for gold signals.

    Can operate in two modes:
      1. **Live mode** (api_key provided): fetches DXY, TNX, T10YIE from
         TwelveData at analysis time.
      2. **Backtest mode** (corr_dfs provided): uses pre-fetched DataFrames
         passed in from the backtest engine to avoid redundant API calls.

    Parameters
    ----------
    api_key : str, optional
        TwelveData API key.  If not provided, falls back to the
        TWELVEDATA_API_KEY environment variable.
    ema_period : int
        EMA period for DXY trend detection (default 20).
    yield_lookback : int
        Number of periods to look back when measuring yield direction
        (default 5).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        ema_period: int = 20,
        yield_lookback: int = 5,
    ) -> None:
        self.api_key       = api_key or os.environ.get("TWELVEDATA_API_KEY", "")
        self.ema_period    = ema_period
        self.yield_lookback = yield_lookback
        self.version       = "1.0.0"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        signal_vote: str,
        corr_dfs: Optional[Dict[str, Optional[pd.DataFrame]]] = None,
        fetch_live: bool = False,
    ) -> Dict[str, Any]:
        """
        Evaluate macro context and return an adjusted confidence modifier.

        Parameters
        ----------
        signal_vote : str
            The directional vote from the primary signal engine
            ("BUY", "SELL", or "NEUTRAL").
        corr_dfs : dict, optional
            Pre-fetched DataFrames keyed by short symbol name.  Expected
            keys: "DXY".  When provided, DXY analysis uses this data
            instead of making a live API call.
        fetch_live : bool
            If True and api_key is available, fetch TNX and T10YIE live.
            Defaults to False to keep the backtest fast.

        Returns
        -------
        Dict with keys:
          macro_score        : float ∈ [-1, +1]
          macro_bias         : "BULLISH_GOLD" | "BEARISH_GOLD" | "NEUTRAL"
          confidence_modifier: float (e.g. -0.20 if macro opposes signal)
          factors            : dict of individual factor scores
          valid              : bool
        """
        try:
            factors: Dict[str, int] = {}

            # ── Factor 1: USD Strength (DXY) ─────────────────────────
            dxy_df = self._get_dxy_df(corr_dfs, fetch_live)
            factors["dxy_strength"] = self._score_dxy(dxy_df)

            # ── Factor 2: Real Rates Proxy (US 10Y yield) ─────────────
            if fetch_live and self.api_key:
                yield_df = self._fetch_series("US10Y", outputsize=50)
            else:
                yield_df = None
            factors["real_rates"] = self._score_yield(yield_df)

            # ── Factor 3: Inflation Expectations ─────────────────────
            if fetch_live and self.api_key:
                breakeven_df = self._fetch_series("BREAKEVEN", outputsize=50)
            else:
                breakeven_df = None
            factors["inflation_expectations"] = self._score_breakeven(breakeven_df)

            # ── Composite macro score ─────────────────────────────────
            macro_score = sum(factors.values()) / 3.0

            if macro_score > 0.15:
                macro_bias = "BULLISH_GOLD"
            elif macro_score < -0.15:
                macro_bias = "BEARISH_GOLD"
            else:
                macro_bias = "NEUTRAL"

            # ── Confidence modifier ───────────────────────────────────
            # Reduce confidence by 20% if macro opposes the signal
            confidence_modifier = 0.0
            if signal_vote == "BUY"  and macro_bias == "BEARISH_GOLD":
                confidence_modifier = -0.20
            elif signal_vote == "SELL" and macro_bias == "BULLISH_GOLD":
                confidence_modifier = -0.20

            result = {
                "macro_score":         round(macro_score, 4),
                "macro_bias":          macro_bias,
                "confidence_modifier": confidence_modifier,
                "factors":             factors,
                "valid":               True,
                "dxy_available":       dxy_df is not None,
                "yield_available":     yield_df is not None,
                "breakeven_available": breakeven_df is not None,
            }

            logger.debug(
                f"MacroFilter: score={macro_score:.3f} bias={macro_bias} "
                f"modifier={confidence_modifier:+.2f} factors={factors}"
            )
            return result

        except Exception as exc:
            logger.error(f"MacroFilter error: {exc}", exc_info=True)
            return {
                "macro_score":         0.0,
                "macro_bias":          "NEUTRAL",
                "confidence_modifier": 0.0,
                "factors":             {},
                "valid":               False,
                "error":               str(exc),
            }

    # ------------------------------------------------------------------
    # Factor scoring
    # ------------------------------------------------------------------

    def _score_dxy(self, dxy_df: Optional[pd.DataFrame]) -> int:
        """
        Score DXY strength.

        Returns +1 (USD weak → bullish gold), -1 (USD strong → bearish gold),
        or 0 (neutral).
        """
        if dxy_df is None or len(dxy_df) < self.ema_period + 2:
            return 0
        try:
            close  = dxy_df["close"].astype(float)
            ema20  = float(close.ewm(span=self.ema_period, adjust=False).mean().iloc[-1])
            price  = float(close.iloc[-1])
            band   = ema20 * _DXY_NEUTRAL_BAND_PCT
            if price > ema20 + band:
                return -1   # USD strong → bearish gold
            elif price < ema20 - band:
                return +1   # USD weak   → bullish gold
            return 0
        except Exception:
            return 0

    def _score_yield(self, yield_df: Optional[pd.DataFrame]) -> int:
        """
        Score US 10Y yield direction.

        Returns -1 (yields rising → bearish gold), +1 (yields falling →
        bullish gold), or 0 (flat).
        """
        if yield_df is None or len(yield_df) < self.yield_lookback + 2:
            return 0
        try:
            close   = yield_df["close"].astype(float)
            current = float(close.iloc[-1])
            past    = float(close.iloc[-self.yield_lookback - 1])
            change  = current - past
            if change > _YIELD_FLAT_BAND:
                return -1   # yields rising  → bearish gold
            elif change < -_YIELD_FLAT_BAND:
                return +1   # yields falling → bullish gold
            return 0
        except Exception:
            return 0

    def _score_breakeven(self, breakeven_df: Optional[pd.DataFrame]) -> int:
        """
        Score 10Y breakeven inflation direction.

        Returns +1 (inflation expectations rising → bullish gold),
        -1 (falling → bearish gold), or 0 (flat).
        """
        if breakeven_df is None or len(breakeven_df) < self.yield_lookback + 2:
            return 0
        try:
            close   = breakeven_df["close"].astype(float)
            current = float(close.iloc[-1])
            past    = float(close.iloc[-self.yield_lookback - 1])
            change  = current - past
            if change > _YIELD_FLAT_BAND:
                return +1   # inflation expectations rising → bullish gold
            elif change < -_YIELD_FLAT_BAND:
                return -1   # inflation expectations falling → bearish gold
            return 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    def _get_dxy_df(
        self,
        corr_dfs: Optional[Dict[str, Optional[pd.DataFrame]]],
        fetch_live: bool,
    ) -> Optional[pd.DataFrame]:
        """Return a DXY DataFrame from corr_dfs or via live fetch."""
        # Prefer pre-fetched data (backtest mode)
        if corr_dfs and corr_dfs.get("DXY") is not None:
            return corr_dfs["DXY"]
        # Fall back to live fetch if requested
        if fetch_live and self.api_key:
            return self._fetch_series("DXY", outputsize=100)
        return None

    def _fetch_series(
        self,
        key: str,
        outputsize: int = 100,
        interval: str = "1day",
    ) -> Optional[pd.DataFrame]:
        """
        Fetch a time series from TwelveData.

        Parameters
        ----------
        key        : Key in _TD_SYMBOLS dict (e.g. "DXY", "US10Y").
        outputsize : Number of candles to fetch.
        interval   : TwelveData interval string (default "1day").
        """
        symbol = _TD_SYMBOLS.get(key)
        if not symbol or not self.api_key:
            return None
        try:
            params = urllib.parse.urlencode({
                "symbol":     symbol,
                "interval":   interval,
                "outputsize": outputsize,
                "apikey":     self.api_key,
                "format":     "JSON",
            })
            url = f"{TWELVEDATA_BASE_URL}?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "GoldMacroFilter/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if "values" not in data:
                logger.warning(f"MacroFilter: no values for {symbol}: {data.get('message', '')}")
                return None
            rows = []
            for v in reversed(data["values"]):
                try:
                    rows.append({
                        "datetime": v["datetime"],
                        "close":    float(v["close"]),
                    })
                except (KeyError, ValueError):
                    continue
            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception as exc:
            logger.warning(f"MacroFilter fetch error [{key}]: {exc}")
            return None


# Module-level singleton (api_key read from environment at import time)
macro_filter = MacroFilter()
