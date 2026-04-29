from typing import List, Dict, Tuple


class CrossPairHeatmap:
    """
    Cross-pair USD exposure tracker.

    Prevents double-risking the same currency by counting how many
    active signals already carry USD exposure.  When the cap is reached,
    new USD-exposed signals are filtered out until an existing one closes.

    Example:
        Active signals: EURUSD SELL, GBPUSD SELL  → 2 USD-exposed signals
        New signal:     USDCAD BUY                → filtered (cap = 2)
    """

    @staticmethod
    def get_currency_exposure(symbol: str) -> Tuple[str, str]:
        """
        Extract base and quote currencies from a 6-character symbol.

        Returns:
            (base_currency, quote_currency) — e.g. ("EUR", "USD")
        """
        symbol = symbol.upper().strip()
        return symbol[:3], symbol[3:6]

    @staticmethod
    def check_usd_exposure(signals: List[Dict]) -> Dict[str, List[str]]:
        """
        Scan a list of signal dicts for USD-exposed pairs.

        Args:
            signals : list of dicts, each must have a "symbol" key

        Returns:
            dict mapping currency code → list of symbols carrying that exposure
            e.g. {"USD": ["EURUSD", "GBPUSD"]}
        """
        exposure: Dict[str, List[str]] = {}
        for signal in signals:
            symbol = signal.get("symbol") or signal.get("pair", "")
            if not symbol:
                continue
            base, quote = CrossPairHeatmap.get_currency_exposure(symbol)
            for currency in (base, quote):
                if currency == "USD":
                    exposure.setdefault("USD", [])
                    if symbol not in exposure["USD"]:
                        exposure["USD"].append(symbol)
        return exposure

    @staticmethod
    def should_filter_signal(
        symbol: str,
        active_signals: List[Dict],
        max_usd_exposure: int = 2,
    ) -> bool:
        """
        Return True if adding this signal would exceed the USD exposure cap.

        Args:
            symbol           : proposed new signal symbol (e.g. "USDCAD")
            active_signals   : currently open signals (list of dicts with "symbol"/"pair")
            max_usd_exposure : maximum allowed concurrent USD-exposed signals (default 2)

        Returns:
            True  → filter this signal (cap reached)
            False → signal is safe to proceed
        """
        base, quote = CrossPairHeatmap.get_currency_exposure(symbol)
        is_usd_exposed = base == "USD" or quote == "USD"

        if not is_usd_exposed:
            return False   # Cross pair — no USD exposure, always allow

        exposure = CrossPairHeatmap.check_usd_exposure(active_signals)
        usd_count = len(exposure.get("USD", []))
        return usd_count >= max_usd_exposure

    @staticmethod
    def get_exposure_summary(active_signals: List[Dict]) -> Dict:
        """
        Return a summary of current currency exposure across all active signals.
        Useful for Telegram info blocks and admin dashboards.

        Returns:
            {
                "usd_count"    : int,
                "usd_pairs"    : list[str],
                "total_signals": int,
                "at_cap"       : bool,
            }
        """
        exposure = CrossPairHeatmap.check_usd_exposure(active_signals)
        usd_pairs = exposure.get("USD", [])
        return {
            "usd_count":     len(usd_pairs),
            "usd_pairs":     usd_pairs,
            "total_signals": len(active_signals),
            "at_cap":        len(usd_pairs) >= 2,
        }
