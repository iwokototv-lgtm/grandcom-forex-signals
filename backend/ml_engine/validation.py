"""
Validation Engine
Validates all critical system values to catch bugs before they cause damage.
"""
import logging

logger = logging.getLogger(__name__)


class ValidationEngine:
    """Validates all critical system values."""

    @staticmethod
    def validate_account_balance(balance: float, starting_balance: float) -> dict:
        """Validate account balance is sane."""
        errors = []
        warnings = []

        # Check 1: Balance is positive
        if balance < 0:
            errors.append(f"Balance is negative: ${balance}")

        # Check 2: Balance is not unreasonably high
        if balance > starting_balance * 100:
            errors.append(
                f"Balance suspiciously high: ${balance} "
                f"(started with ${starting_balance})"
            )

        # Check 3: Balance is not zero
        if balance == 0:
            errors.append("Balance is zero")

        # Check 4: Balance is reasonable
        if starting_balance > 0 and balance < starting_balance * 0.01:
            warnings.append(
                f"Balance very low: ${balance} "
                f"({(balance / starting_balance) * 100:.1f}% of start)"
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def validate_peak_balance(peak: float, current: float, starting: float) -> dict:
        """Validate peak balance tracking."""
        errors = []
        warnings = []

        # Check 1: Peak >= current
        if peak < current:
            errors.append(f"Peak (${peak}) < Current (${current})")

        # Check 2: Peak >= starting
        if peak < starting:
            errors.append(f"Peak (${peak}) < Starting (${starting})")

        # Check 3: Peak is not hardcoded
        if peak == 100000 and starting != 100000:
            errors.append(
                f"Peak appears hardcoded to $100,000 (started with ${starting})"
            )

        # Check 4: Peak is not unreasonably high
        if starting > 0 and peak > starting * 100:
            warnings.append(
                f"Peak suspiciously high: ${peak} (started with ${starting})"
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }

    @staticmethod
    def validate_drawdown(drawdown_pct: float, peak: float, current: float) -> dict:
        """Validate drawdown calculation."""
        errors = []

        # Check 1: Drawdown is between 0-100%
        if drawdown_pct < 0 or drawdown_pct > 100:
            errors.append(f"Drawdown out of range: {drawdown_pct}%")

        # Check 2: Drawdown calculation is correct
        if peak > 0:
            calculated = ((peak - current) / peak) * 100
            if abs(calculated - drawdown_pct) > 0.1:
                errors.append(
                    f"Drawdown calculation wrong: {drawdown_pct}% "
                    f"vs calculated {calculated}%"
                )

        # Check 3: Drawdown matches peak/current relationship
        if drawdown_pct > 0 and current >= peak:
            errors.append("Drawdown > 0 but current >= peak")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    @staticmethod
    def validate_position_count(
        open_count: int, max_per_pair: int, num_pairs: int
    ) -> dict:
        """Validate position count."""
        errors = []

        max_total = max_per_pair * num_pairs

        if open_count > max_total:
            errors.append(
                f"Too many positions: {open_count} > {max_total}"
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }
