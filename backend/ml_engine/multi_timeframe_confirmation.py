"""
G2: Multi-Timeframe Confirmation Engine
1H, 4H, Daily, Weekly alignment analysis with 0-100% confluence scoring
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
import logging
import asyncio
import aiohttp
import os

logger = logging.getLogger(__name__)

TWELVE_DATA_API_KEY = os.environ.get("TWELVE_DATA_API_KEY", "")

SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAUEUR": "XAU/EUR",
    "BTCUSD": "BTC/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "USDCHF": "USD/CHF",
    "DXY": "DXY",
}

TIMEFRAME_MAP = {
    "1h": "1h",
    "4h": "4h",
    "1day": "1day",
    "1week": "1week",
}

# Weights for confluence scoring (must sum to 1.0)
TIMEFRAME_WEIGHTS = {
    "1h": 0.15,
    "4h": 0.35,
    "1day": 0.35,
    "1week": 0.15,
}


class MultiTimeframeConfirmation:
    """
    G2: Multi-Timeframe Confirmation Engine.

    Analyzes 1H, 4H, Daily, and Weekly timeframes and produces a
    0-100% alignment score. Higher score = stronger directional consensus.

    Each timeframe is analyzed for:
    - Trend direction (EMA alignment)
    - Momentum (RSI, MACD)
    - Volatility (ATR, Bollinger Bands)
    - Structure (swing highs/lows)
    """

    def __init__(self):
        self.timeframes = ["1h", "4h", "1day", "1week"]
        self.weights = TIMEFRAME_WEIGHTS
        self.version = "3.0.2"  # Updated version with volume fix
        self._cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Main Analysis
    # ------------------------------------------------------------------

    async def analyze(self, symbol: str) -> Dict[str, Any]:
        """
        Full multi-timeframe confirmation analysis.

        Returns:
            Dict with per-timeframe analysis and composite alignment score (0-100)
        """
        try:
            result: Dict[str, Any] = {
                "symbol": symbol,
                "timestamp": datetime.utcnow().isoformat(),
                "timeframes": {},
                "alignment_score": 0.0,
                "dominant_direction": "NEUTRAL",
                "valid": True,
                "version": self.version,
            }

            # Fetch all timeframes concurrently
            tasks = {
                tf: self._fetch_data(symbol, tf, outputsize=100)
                for tf in self.timeframes
            }
            dfs: Dict[str, Optional[pd.DataFrame]] = {}
            for tf, coro in tasks.items():
                try:
                    dfs[tf] = await coro
                    await asyncio.sleep(0.3)  # Rate limit
                except Exception as exc:
                    logger.warning(f"Failed to fetch {symbol} {tf}: {exc}")
                    dfs[tf] = None

            # Analyze each timeframe
            for tf in self.timeframes:
                df = dfs.get(tf)
                if df is not None and len(df) >= 30:
                    result["timeframes"][tf] = self._analyze_timeframe(df, tf)
                else:
                    result["timeframes"][tf] = {"valid": False, "direction": "NEUTRAL", "score": 0}

            # Compute composite alignment
            result["alignment_score"], result["dominant_direction"] = self._compute_alignment(
                result["timeframes"]
            )
            result["trade_recommendation"] = self._trade_recommendation(result)
            result["confluence_breakdown"] = self._confluence_breakdown(result["timeframes"])

            logger.info(
                f"MTF Confirmation [{symbol}]: alignment={result['alignment_score']:.1f}% "
                f"direction={result['dominant_direction']}"
            )
            return result

        except Exception as exc:
            logger.error(f"MTF Confirmation error [{symbol}]: {exc}", exc_info=True)
            return {
                "symbol": symbol,
                "error": str(exc),
                "valid": False,
                "alignment_score": 0.0,
                "dominant_direction": "NEUTRAL",
            }

    def analyze_sync(self, dfs: Dict[str, pd.DataFrame], symbol: str) -> Dict[str, Any]:
        """
        Synchronous analysis using pre-fetched DataFrames.
        Use when data is already available (avoids API calls).
        """
        result: Dict[str, Any] = {
            "symbol": symbol,
            "timestamp": datetime.utcnow().isoformat(),
            "timeframes": {},
            "alignment_score": 0.0,
            "dominant_direction": "NEUTRAL",
            "valid": True,
            "version": self.version,
        }

        for tf in self.timeframes:
            df = dfs.get(tf)
            if df is not None and len(df) >= 30:
                result["timeframes"][tf] = self._analyze_timeframe(df, tf)
            else:
                result["timeframes"][tf] = {"valid": False, "direction": "NEUTRAL", "score": 0}

        result["alignment_score"], result["dominant_direction"] = self._compute_alignment(
            result["timeframes"]
        )
        result["trade_recommendation"] = self._trade_recommendation(result)
        result["confluence_breakdown"] = self._confluence_breakdown(result["timeframes"])
        return result

    # ------------------------------------------------------------------
    # Data Fetching
    # ------------------------------------------------------------------

    async def _fetch_data(
        self, symbol: str, timeframe: str, outputsize: int = 100
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data from TwelveData API."""
        try:
            api_symbol = SYMBOL_MAP.get(symbol, symbol)
            interval = TIMEFRAME_MAP.get(timeframe, timeframe)

            url = "https://api.twelvedata.com/time_series"
            params = {
                "symbol": api_symbol,
                "interval": interval,
                "apikey": TWELVE_DATA_API_KEY,
                "outputsize": outputsize,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()

            if "values" not in data:
                logger.warning(f"No data for {symbol} {timeframe}: {data.get('message', 'Unknown')}")
                return None

            df = pd.DataFrame(data["values"])
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)
            
            # FIX v3.0.2: Ensure proper data type conversion before fillna
            # Convert OHLC columns to numeric
            for col in ["open", "high", "low", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            
            # FIX: Handle volume properly - check if column exists first
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            else:
                df["volume"] = 0
            
            # Remove any rows with NaN in OHLC
            df = df.dropna(subset=["open", "high", "low", "close"])
            
            return df if len(df) > 0 else None

        except Exception as exc:
            logger.error(f"Fetch error [{symbol}/{timeframe}]: {exc}")
            return None

    # ------------------------------------------------------------------
    # Per-Timeframe Analysis
    # ------------------------------------------------------------------

    def _analyze_timeframe(self, df: pd.DataFrame, timeframe: str) -> Dict[str, Any]:
        """Comprehensive single-timeframe analysis."""
        try:
            df = df.copy()
            close = df["close"]
            high = df["high"]
            low = df["low"]

            # EMAs
            df["ema_20"] = close.ewm(span=20, adjust=False).mean()
            df["ema_50"] = close.ewm(span=50, adjust=False).mean()
            df["ema_200"] = close.ewm(span=200, adjust=False).mean() if len(df) >= 200 else close.ewm(span=len(df), adjust=False).mean()

            # RSI
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            df["rsi"] = 100 - (100 / (1 + rs))

            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            df["macd"] = ema12 - ema26
            df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
            df["macd_hist"] = df["macd"] - df["macd_signal"]

            # ATR
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14).mean()

            # ADX
            df["adx"] = self._compute_adx(df)

            # Bollinger Bands
            bb_mid = close.rolling(20).mean()
            bb_std = close.rolling(20).std()
            df["bb_upper"] = bb_mid + 2 * bb_std
            df["bb_lower"] = bb_mid - 2 * bb_std
            df["bb_pct"] = (close - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)

            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest

            # Direction signals
            price = float(latest["close"])
            ema20 = float(latest["ema_20"])
            ema50 = float(latest["ema_50"])
            ema200 = float(latest["ema_200"])
            rsi = float(latest["rsi"])
            macd = float(latest["macd"])
            macd_sig = float(latest["macd_signal"])
            adx = float(latest["adx"]) if not np.isnan(float(latest["adx"])) else 20.0

            bull_signals = 0
            bear_signals = 0
            total_signals = 0

            # EMA alignment
            if price > ema20:
                bull_signals += 1
            else:
                bear_signals += 1
            total_signals += 1

            if ema20 > ema50:
                bull_signals += 1
            else:
                bear_signals += 1
            total_signals += 1

            if price > ema200:
                bull_signals += 1
            else:
                bear_signals += 1
            total_signals += 1

            # RSI
            if rsi > 50:
                bull_signals += 1
            else:
                bear_signals += 1
            total_signals += 1

            # MACD
            if macd > macd_sig:
                bull_signals += 1
            else:
                bear_signals += 1
            total_signals += 1

            # Determine direction
            bull_pct = bull_signals / total_signals
            bear_pct = bear_signals / total_signals

            if bull_pct >= 0.6:
                direction = "BULLISH"
                strength = bull_pct
            elif bear_pct >= 0.6:
                direction = "BEARISH"
                strength = bear_pct
            else:
                direction = "NEUTRAL"
                strength = 0.5

            # Trend strength from ADX
            trend_strength = "STRONG" if adx > 25 else ("MODERATE" if adx > 20 else "WEAK")

            return {
                "valid": True,
                "timeframe": timeframe,
                "direction": direction,
                "strength": round(strength, 3),
                "score": round(strength * 100, 1),
                "bull_signals": bull_signals,
                "bear_signals": bear_signals,
                "total_signals": total_signals,
                "indicators": {
                    "price": round(price, 5),
                    "ema_20": round(ema20, 5),
                    "ema_50": round(ema50, 5),
                    "ema_200": round(ema200, 5),
                    "rsi": round(rsi, 2),
                    "macd": round(macd, 6),
                    "macd_signal": round(macd_sig, 6),
                    "adx": round(adx, 2),
                    "atr": round(float(latest["atr"]), 5),
                    "bb_pct": round(float(latest["bb_pct"]), 3),
                },
                "trend_strength": trend_strength,
                "trending": adx > 25,
            }

        except Exception as exc:
            logger.error(f"Timeframe analysis error [{timeframe}]: {exc}")
            return {"valid": False, "direction": "NEUTRAL", "score": 0, "error": str(exc)}

    def _compute_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Compute ADX indicator."""
        try:
            high = df["high"]
            low = df["low"]
            close = df["close"]

            plus_dm = high.diff()
            minus_dm = -low.diff()
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm < 0] = 0

            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ], axis=1).max(axis=1)

            atr = tr.rolling(period).mean()
            plus_di = 100 * (plus_dm.rolling(period).mean() / atr.replace(0, np.nan))
            minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
            adx = dx.rolling(period).mean()
            return adx.fillna(20.0)
        except Exception:
            return pd.Series([20.0] * len(df), index=df.index)

    # ------------------------------------------------------------------
    # Alignment Scoring
    # ------------------------------------------------------------------

    def _compute_alignment(
        self, timeframes: Dict[str, Dict]
    ) -> Tuple[float, str]:
        """
        Compute weighted alignment score (0-100%) and dominant direction.

        Returns:
            (alignment_score, dominant_direction)
        """
        bull_weight = 0.0
        bear_weight = 0.0
        total_weight = 0.0

        for tf, analysis in timeframes.items():
            if not analysis.get("valid", False):
                continue
            weight = self.weights.get(tf, 0.25)
            direction = analysis.get("direction", "NEUTRAL")
            strength = analysis.get("strength", 0.5)

            if direction == "BULLISH":
                bull_weight += weight * strength
            elif direction == "BEARISH":
                bear_weight += weight * strength
            total_weight += weight

        if total_weight == 0:
            return 0.0, "NEUTRAL"

        bull_score = (bull_weight / total_weight) * 100
        bear_score = (bear_weight / total_weight) * 100

        if bull_score > bear_score and bull_score > 50:
            return round(bull_score, 1), "BULLISH"
        elif bear_score > bull_score and bear_score > 50:
            return round(bear_score, 1), "BEARISH"
        else:
            return round(max(bull_score, bear_score), 1), "NEUTRAL"

    def _trade_recommendation(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate trade recommendation based on alignment."""
        score = result["alignment_score"]
        direction = result["dominant_direction"]

        if score >= 80 and direction != "NEUTRAL":
            quality = "EXCELLENT"
            action = "BUY" if direction == "BULLISH" else "SELL"
        elif score >= 65 and direction != "NEUTRAL":
            quality = "GOOD"
            action = "BUY" if direction == "BULLISH" else "SELL"
        elif score >= 50 and direction != "NEUTRAL":
            quality = "FAIR"
            action = "BUY" if direction == "BULLISH" else "SELL"
        else:
            quality = "POOR"
            action = "WAIT"

        return {
            "action": action,
            "quality": quality,
            "alignment_score": score,
            "min_required": 60.0,
            "meets_threshold": score >= 60.0 and action != "WAIT",
        }

    def _confluence_breakdown(self, timeframes: Dict[str, Dict]) -> Dict[str, Any]:
        """Detailed breakdown of confluence per timeframe."""
        breakdown = {}
        for tf, analysis in timeframes.items():
            breakdown[tf] = {
                "direction": analysis.get("direction", "NEUTRAL"),
                "score": analysis.get("score", 0),
                "weight": self.weights.get(tf, 0.25),
                "weighted_contribution": round(
                    analysis.get("score", 0) * self.weights.get(tf, 0.25), 2
                ),
                "valid": analysis.get("valid", False),
            }
        return breakdown


# Global instance
mtf_confirmation = MultiTimeframeConfirmation()

