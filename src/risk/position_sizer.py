"""
Position Sizer & Small-Account ($100) Viability Filter.
Protects small accounts from Deriv contract size, minimum lot, and margin violations.
"""

from typing import Dict, Any, Optional, Tuple
from src.risk.pip_calculator import PipCalculator


class PositionSizer:
    def __init__(self, pip_calc: PipCalculator = None, max_risk_percent: float = 1.5, hard_cap_percent: float = 2.0):
        self.pip_calc = pip_calc or PipCalculator()
        self.max_risk_percent = max_risk_percent
        self.hard_cap_percent = hard_cap_percent

    def is_viable_for_account(
        self,
        symbol: str,
        account_equity: float,
        entry_price: float,
        stop_loss_price: float,
        risk_percent: float = 1.5
    ) -> Tuple[bool, Optional[str]]:
        """
        Strict boolean viability gate enforced before opening paper/live trades.
        Rejects any setup where minimum lot risk exceeds hard cap (<= 2.0% of equity)
        or required margin exceeds 25% of free equity.
        """
        if account_equity <= 0:
            return False, f"Insufficient equity (${account_equity:.2f})"

        spec = self.pip_calc.get_spec(symbol)
        min_lot = spec.get("min_lot", 0.005)
        points = self.pip_calc.calculate_point_distance(entry_price, stop_loss_price)

        if points <= 0:
            return False, f"Invalid zero stop-loss distance on {symbol}"

        hard_cap_dollar_risk = account_equity * (self.hard_cap_percent / 100.0)
        min_lot_dollar_risk = self.pip_calc.calculate_dollar_risk(
            entry_price, stop_loss_price, min_lot, symbol
        )
        margin_min_lot = self.pip_calc.calculate_required_margin(entry_price, min_lot, symbol)

        if min_lot_dollar_risk > hard_cap_dollar_risk:
            reason = (
                f"DISQUALIFIED_ON_${account_equity:.0f}: Minimum lot {min_lot} on {symbol} "
                f"with {points:.2f} pt SL risks ${min_lot_dollar_risk:.2f} "
                f"({(min_lot_dollar_risk / account_equity) * 100:.1f}%), exceeding ${hard_cap_dollar_risk:.2f} hard cap."
            )
            return False, reason

        if margin_min_lot > (account_equity * 0.25):
            reason = (
                f"DISQUALIFIED_MARGIN_${account_equity:.0f}: Margin ${margin_min_lot:.2f} "
                f"on {symbol} ({min_lot} lot) exceeds 25% equity threshold."
            )
            return False, reason

        return True, None

    def evaluate_instrument_viability(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        account_equity: float = 100.0,
        risk_percent: float = 1.5
    ) -> Dict[str, Any]:
        """
        Checks if an instrument setup is viable on the specified capital and computes stepped lot size.
        """
        spec = self.pip_calc.get_spec(symbol)
        min_lot = spec.get("min_lot", 0.005)
        points = self.pip_calc.calculate_point_distance(entry_price, stop_loss_price)

        effective_risk_pct = min(risk_percent, self.hard_cap_percent)
        target_dollar_risk = account_equity * (effective_risk_pct / 100.0)
        min_lot_dollar_risk = self.pip_calc.calculate_dollar_risk(
            entry_price, stop_loss_price, min_lot, symbol
        )

        is_viable, rejection_reason = self.is_viable_for_account(
            symbol=symbol,
            account_equity=account_equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            risk_percent=risk_percent
        )

        if not is_viable:
            return {
                "viable": False,
                "symbol": symbol,
                "rejection_reason": rejection_reason,
                "min_lot": min_lot,
                "min_lot_dollar_risk": round(min_lot_dollar_risk, 2),
                "target_dollar_risk": round(target_dollar_risk, 2),
                "sl_points": round(points, 2),
                "recommended_lot": 0.0,
                "margin_required": 0.0,
                "alternative_symbols": ["STEPINDEX", "1HZ25V", "1HZ50V"]
            }

        raw_lot = self.pip_calc.calculate_raw_lot_size(target_dollar_risk, entry_price, stop_loss_price, symbol)
        final_lot = self.pip_calc.round_to_step(raw_lot, symbol)
        actual_dollar_risk = self.pip_calc.calculate_dollar_risk(entry_price, stop_loss_price, final_lot, symbol)

        # Ensure rounding never nudges dollar risk above hard cap
        hard_cap_dollar_risk = account_equity * (self.hard_cap_percent / 100.0)
        if actual_dollar_risk > hard_cap_dollar_risk:
            final_lot = min_lot
            actual_dollar_risk = min_lot_dollar_risk

        margin_req = self.pip_calc.calculate_required_margin(entry_price, final_lot, symbol)

        return {
            "viable": True,
            "symbol": symbol,
            "rejection_reason": None,
            "min_lot": min_lot,
            "recommended_lot": final_lot,
            "sl_points": round(points, 2),
            "target_dollar_risk": round(target_dollar_risk, 2),
            "actual_dollar_risk": round(actual_dollar_risk, 2),
            "risk_percent": round((actual_dollar_risk / account_equity) * 100.0, 2) if account_equity > 0 else 0.0,
            "margin_required": round(margin_req, 2),
            "free_margin_buffer_ratio": round((account_equity - margin_req) / margin_req, 1) if margin_req > 0 else 999.0
        }
