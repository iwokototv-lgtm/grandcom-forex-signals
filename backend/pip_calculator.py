"""
pip_calculator.py — Pip-aware spread guard and freeze-level check.

Provides:
  - PipCalculator.get_pip_size(symbol) → float
  - PipCalculator.get_pip_label(symbol) → str
  - PipCalculator.price_to_pips(price_diff, symbol) → float
  - PipCalculator.check_freeze_level(symbol, entry_price, tp1_price, freeze_level_pips) → bool
"""

from __future__ import annotations
from pair_profiles import get_pip_size as _profile_pip_size, get_decimal_places


class PipCalculator:
    """
    Stateless utility class for pip-based calculations.

    Pip size conventions:
        Standard FX (EURUSD, GBPUSD, …) : 0.0001  (4th decimal)
        JPY pairs   (USDJPY, EURJPY, …) : 0.01    (2nd decimal)
        Gold        (XAUUSD, XAUEUR)    : 0.01    (2nd decimal — $0.01 move)
    """

    @staticmethod
    def get_pip_size(symbol: str) -> float:
        """Return the pip size for *symbol*."""
        return _profile_pip_size(symbol)

    @staticmethod
    def get_pip_label(symbol: str) -> str:
        """
        Return a human-readable pip size label for Telegram display.

        Examples:
            EURUSD → "0.0001 (Standard FX)"
            USDJPY → "0.01 (JPY)"
            XAUUSD → "0.01 (Gold)"
        """
        s = symbol.upper()
        pip = _profile_pip_size(symbol)
        if "XAU" in s:
            return f"{pip} (Gold)"
        if "JPY" in s:
            return f"{pip} (JPY)"
        return f"{pip} (Standard FX)"

    @staticmethod
    def price_to_pips(price_diff: float, symbol: str) -> float:
        """
        Convert a raw price difference to pips (always positive).

        Args:
            price_diff : Raw price difference (e.g. entry - sl)
            symbol     : Currency pair symbol

        Returns:
            Number of pips (float, always ≥ 0)
        """
        pip_size = _profile_pip_size(symbol)
        if pip_size <= 0:
            return 0.0
        return round(abs(price_diff) / pip_size, 2)

    @staticmethod
    def check_freeze_level(
        symbol:            str,
        entry_price:       float,
        tp1_price:         float,
        freeze_level_pips: float = 2.0,
    ) -> bool:
        """
        Return True if TP1 is far enough from entry to be worth taking.

        A "frozen" signal is one where TP1 is so close to entry that the
        spread would eat the entire profit — typically caused by stale
        price data or a miscalculated TP.

        Args:
            symbol            : Currency pair symbol
            entry_price       : Signal entry price
            tp1_price         : First take-profit level
            freeze_level_pips : Minimum distance in pips (default 2.0)

        Returns:
            True  → TP1 is far enough — signal is valid
            False → TP1 is too close  — signal should be skipped ("frozen")
        """
        pip_size = _profile_pip_size(symbol)
        if pip_size <= 0:
            return True  # Can't calculate — allow by default
        distance_pips = abs(tp1_price - entry_price) / pip_size
        return distance_pips >= freeze_level_pips
