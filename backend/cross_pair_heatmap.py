"""
cross_pair_heatmap.py — USD exposure guard for concurrent Forex signals.

Prevents double-risking USD by capping the number of concurrent signals
that have a USD leg (either as base or quote currency).

Provides:
  - CrossPairHeatmap.count_usd_exposure(active_signals) → int
  - CrossPairHeatmap.get_usd_pairs(active_signals) → list[str]
  - CrossPairHeatmap.get_exposure_summary(active_signals) → dict
  - CrossPairHeatmap.should_filter_signal(pair, active_signals, max_usd_exposure) → bool
"""

from __future__ import annotations
from typing import List, Dict, Any


def _has_usd_leg(symbol: str) -> bool:
    """Return True if *symbol* contains USD (either base or quote)."""
    return "USD" in symbol.upper()


class CrossPairHeatmap:
    """
    Stateless utility class for cross-pair USD exposure management.

    Active signals are passed as a list of dicts with at least a "symbol" key:
        [{"symbol": "EURUSD"}, {"symbol": "GBPUSD"}, ...]

    The cap prevents scenarios like:
        EURUSD SELL + GBPUSD SELL + USDCAD BUY
        → all three are effectively "short USD" — triple exposure to a single
          macro factor (DXY move), which violates institutional risk rules.
    """

    @staticmethod
    def get_usd_pairs(active_signals: List[Dict[str, Any]]) -> List[str]:
        """
        Return a list of symbols from *active_signals* that have a USD leg.

        Args:
            active_signals : List of dicts with a "symbol" key.

        Returns:
            List of USD-exposed pair symbols (e.g. ["EURUSD", "USDCAD"])
        """
        usd_pairs = []
        for sig in active_signals:
            symbol = sig.get("symbol", "")
            if _has_usd_leg(symbol):
                usd_pairs.append(symbol.upper())
        return usd_pairs

    @staticmethod
    def count_usd_exposure(active_signals: List[Dict[str, Any]]) -> int:
        """
        Return the number of active signals that have a USD leg.

        Args:
            active_signals : List of dicts with a "symbol" key.

        Returns:
            Integer count of USD-exposed active signals.
        """
        return len(CrossPairHeatmap.get_usd_pairs(active_signals))

    @staticmethod
    def get_exposure_summary(active_signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Return a summary dict of current USD exposure.

        Returns:
            {
                "usd_count":  int,        # number of USD-exposed active signals
                "usd_pairs":  list[str],  # which pairs are USD-exposed
                "at_cap":     bool,       # True if at or above default cap (2)
            }
        """
        usd_pairs = CrossPairHeatmap.get_usd_pairs(active_signals)
        return {
            "usd_count": len(usd_pairs),
            "usd_pairs": usd_pairs,
            "at_cap":    len(usd_pairs) >= 2,
        }

    @staticmethod
    def should_filter_signal(
        pair:             str,
        active_signals:   List[Dict[str, Any]],
        max_usd_exposure: int = 2,
    ) -> bool:
        """
        Return True if the new signal for *pair* should be filtered out
        because the USD exposure cap has been reached.

        A signal is filtered only if:
          1. The new pair has a USD leg, AND
          2. The number of existing USD-exposed active signals is already
             at or above *max_usd_exposure*.

        Cross pairs (no USD leg) are never filtered by this guard.

        Args:
            pair             : The pair being evaluated (e.g. "EURUSD")
            active_signals   : List of currently active signal dicts
            max_usd_exposure : Maximum allowed concurrent USD-exposed signals

        Returns:
            True  → filter this signal (cap reached)
            False → allow this signal
        """
        if not _has_usd_leg(pair):
            return False  # Cross pair — not subject to USD cap
        current_count = CrossPairHeatmap.count_usd_exposure(active_signals)
        return current_count >= max_usd_exposure
