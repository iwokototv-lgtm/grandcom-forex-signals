from pair_profiles import get_pair_profile, get_pip_size


class PipCalculator:
    """
    Pair-aware pip calculation utilities.

    Handles the JPY exception automatically:
        JPY pairs  → pip_size = 0.01  (3 decimal places)
        Other pairs → pip_size = 0.0001 (5 decimal places)
    """

    @staticmethod
    def calculate_pips(symbol: str, price_difference: float) -> float:
        """Convert a raw price difference to pips for the given symbol."""
        pip_size = get_pip_size(symbol)
        return price_difference / pip_size

    @staticmethod
    def pips_to_price(symbol: str, pips: float) -> float:
        """Convert a pip count to a raw price distance for the given symbol."""
        pip_size = get_pip_size(symbol)
        return pips * pip_size

    @staticmethod
    def calculate_tp_levels(
        symbol: str,
        entry_price: float,
        direction: str,
        atr_m1: float,
        atr_m15: float,
        atr_h1: float,
        spread: float,
    ) -> dict:
        """
        Calculate three TP levels using pair-profile multipliers.

        TP1 : (2 × spread + 0.5) pips  — quick scalp target
        TP2 : 0.5 × ATR(M15)           — intraday swing
        TP3 : 1.0 × ATR(H1)            — full swing target

        Args:
            symbol      : e.g. "EURUSD"
            entry_price : trade entry price
            direction   : "BUY" or "SELL"
            atr_m1      : ATR on 1-minute chart (used for SL sizing)
            atr_m15     : ATR on 15-minute chart
            atr_h1      : ATR on 1-hour chart
            spread      : current spread in pips

        Returns:
            dict with keys "TP1", "TP2", "TP3", each containing
            {"price": float, "pips": float}
        """
        profile = get_pair_profile(symbol)
        if not profile:
            return {}

        pip_size = profile.pip_size
        is_buy = direction.upper() == "BUY"
        sign = 1 if is_buy else -1

        # TP1: spread-based quick target
        tp1_pips = (profile.tp1_spread_multiplier * spread) + 0.5
        tp1_price = round(entry_price + sign * tp1_pips * pip_size, profile.decimal_places)

        # TP2: ATR(M15) based
        tp2_pips = profile.tp2_atr_multiplier * atr_m15 / pip_size
        tp2_price = round(entry_price + sign * tp2_pips * pip_size, profile.decimal_places)

        # TP3: ATR(H1) based
        tp3_pips = profile.tp3_atr_multiplier * atr_h1 / pip_size
        tp3_price = round(entry_price + sign * tp3_pips * pip_size, profile.decimal_places)

        return {
            "TP1": {"price": tp1_price, "pips": round(tp1_pips, 2)},
            "TP2": {"price": tp2_price, "pips": round(tp2_pips, 2)},
            "TP3": {"price": tp3_price, "pips": round(tp3_pips, 2)},
        }

    @staticmethod
    def calculate_sl(
        symbol: str,
        entry_price: float,
        direction: str,
        atr_m1: float,
    ) -> dict:
        """
        Calculate stop-loss using 1.2 × ATR(M1).

        Returns:
            dict with key "SL" containing {"price": float, "pips": float}
        """
        profile = get_pair_profile(symbol)
        if not profile:
            return {}

        pip_size = profile.pip_size
        is_buy = direction.upper() == "BUY"
        sign = 1 if is_buy else -1

        sl_pips = profile.sl_multiplier * atr_m1 / pip_size
        # SL is placed opposite to trade direction
        sl_price = round(entry_price - sign * sl_pips * pip_size, profile.decimal_places)

        return {"SL": {"price": sl_price, "pips": round(sl_pips, 2)}}

    @staticmethod
    def check_freeze_level(
        symbol: str,
        entry_price: float,
        tp1_price: float,
        freeze_level_pips: float = 2.0,
    ) -> bool:
        """
        Return True if the distance from entry to TP1 is at least
        freeze_level_pips — i.e., the trade has enough room to breathe.

        A False result means the spread guard would freeze this trade
        (TP1 is too close to entry to be worth taking).
        """
        pip_size = get_pip_size(symbol)
        distance_pips = abs(entry_price - tp1_price) / pip_size
        return distance_pips >= freeze_level_pips

    @staticmethod
    def get_pip_label(symbol: str) -> str:
        """Return a human-readable pip size label for Telegram messages."""
        pip_size = get_pip_size(symbol)
        if pip_size == 0.01:
            return "0.01 pip (JPY)"
        return "0.0001 pip"
