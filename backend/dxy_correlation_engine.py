from pair_profiles import PairType, PairProfile, get_pair_profile


class DXYCorrelationEngine:
    """
    Institutional DXY correlation flip logic.

    USD-Led pairs  (USDJPY, USDCAD, USDCHF):
        Strong DXY (score > 60) → BUY_BOOST  (USD is the base, benefits from strength)
        Weak   DXY              → SELL_BOOST

    USD-Follow pairs (EURUSD, GBPUSD, AUDUSD, NZDUSD):
        Strong DXY → SELL_BOOST  (USD is the quote, pair falls when USD rises)
        Weak   DXY → BUY_BOOST

    Cross pairs (EURGBP, EURJPY, GBPJPY, etc.):
        NEUTRAL — DXY has no direct structural impact
    """

    DXY_BULLISH_THRESHOLD = 60.0   # Score above this = DXY bullish

    @staticmethod
    def is_dxy_bullish(dxy_score: float) -> bool:
        """Return True when DXY momentum score exceeds the bullish threshold."""
        return dxy_score > DXYCorrelationEngine.DXY_BULLISH_THRESHOLD

    @staticmethod
    def get_dxy_signal_direction(symbol: str, dxy_score: float) -> str:
        """
        Map DXY score + pair type to a directional boost signal.

        Returns one of:
            "BUY_BOOST"  — DXY alignment favours long
            "SELL_BOOST" — DXY alignment favours short
            "NEUTRAL"    — Cross pair or unknown symbol
        """
        profile = get_pair_profile(symbol)
        if not profile:
            return "NEUTRAL"

        is_bullish = DXYCorrelationEngine.is_dxy_bullish(dxy_score)

        if profile.pair_type == PairType.USD_LED:
            return "BUY_BOOST" if is_bullish else "SELL_BOOST"
        elif profile.pair_type == PairType.USD_FOLLOW:
            return "SELL_BOOST" if is_bullish else "BUY_BOOST"
        else:
            return "NEUTRAL"

    @staticmethod
    def apply_dxy_correlation_multiplier(
        signal_direction: str,
        dxy_signal: str,
        base_confidence: float,
        pair_profile: PairProfile,
    ) -> float:
        """
        Adjust base_confidence by ±15% × dxy_sensitivity when DXY aligns
        with or opposes the trade direction.

        Args:
            signal_direction : "BUY" or "SELL"
            dxy_signal       : "BUY_BOOST", "SELL_BOOST", or "NEUTRAL"
            base_confidence  : raw confidence score (0-100)
            pair_profile     : PairProfile for the traded symbol

        Returns:
            Adjusted confidence (float, not capped — caller should cap at 100).
        """
        if dxy_signal == "NEUTRAL":
            return base_confidence

        sensitivity = pair_profile.dxy_sensitivity
        boost_factor = 0.15 * sensitivity

        aligned = (
            (signal_direction == "BUY"  and dxy_signal == "BUY_BOOST") or
            (signal_direction == "SELL" and dxy_signal == "SELL_BOOST")
        )

        if aligned:
            return base_confidence * (1.0 + boost_factor)
        else:
            return base_confidence * (1.0 - boost_factor)

    @staticmethod
    def get_dxy_label(dxy_signal: str, symbol: str) -> str:
        """Return a human-readable DXY correlation label for Telegram messages."""
        profile = get_pair_profile(symbol)
        if not profile or dxy_signal == "NEUTRAL":
            return "⚪ DXY Neutral (cross pair)"

        pair_type_label = {
            PairType.USD_LED:    "USD-Led",
            PairType.USD_FOLLOW: "USD-Follow",
            PairType.CROSS:      "Cross",
        }.get(profile.pair_type, "")

        if dxy_signal == "BUY_BOOST":
            return f"🟢 DXY → BUY aligned ({pair_type_label})"
        elif dxy_signal == "SELL_BOOST":
            return f"🔴 DXY → SELL aligned ({pair_type_label})"
        return "⚪ DXY Neutral"
