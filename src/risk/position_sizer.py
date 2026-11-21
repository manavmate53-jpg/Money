"""
Position Sizer & Small-Account ($100) Viability Filter.
Protects small accounts from Deriv contract size and minimum lot violations.
"""

from typing import Dict, Any, Tuple
from src.risk.pip_calculator import PipCalculator


class PositionSizer:
    def __init__(self, pip_calc: PipCalculator = None, max_risk_percent: float = 1.5, hard_cap_percent: float = 2.0):
        self.pip_calc = pip_calc or PipCalculator()
        self.max_risk_percent = max_risk_percent
        self.hard_cap_percent = hard_cap_percent

    def evaluate_instrument_viability(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        account_equity: float = 100.0,
        risk_percent: float = 1.5
    ) -> Dict[str, Any]:
        """
        Checks if an instrument setup is viable on the specified capital.
        Disqualifies instruments where minimum lot size exceeds hard cap dollar risk.
        """
        spec = self.pip_calc.get_spec(symbol)
        min_lot = spec.get("min_lot", 0.005)
        points = self.pip_calc.calculate_point_distance(entry_price, stop_loss_price)

        effective_risk_pct = min(risk_percent, self.hard_cap_percent)
        target_dollar_risk = account_equity * (effective_risk_pct / 100.0)
        hard_cap_dollar_risk = account_equity * (self.hard_cap_percent / 100.0)

        # Dollar risk if forced to trade minimum lot
        min_lot_dollar_risk = self.pip_calc.calculate_dollar_risk(
            entry_price, stop_loss_price, min_lot, symbol
        )

        is_viable = min_lot_dollar_risk <= hard_cap_dollar_risk

        if not is_viable:
            rejection_reason = (
                f"DISQUALIFIED_ON_${account_equity:.0f}: Minimum lot {min_lot} on {symbol} "
                f"with {points:.2f} pt SL risks ${min_lot_dollar_risk:.2f} "
                f"({(min_lot_dollar_risk / account_equity) * 100:.1f}%), exceeding ${hard_cap_dollar_risk:.2f} hard cap."
            )
            return {
                "viable": False,
                "symbol": symbol,
                "rejection_reason": rejection_reason,
                "min_lot": min_lot,
                "min_lot_dollar_risk": min_lot_dollar_risk,
                "target_dollar_risk": target_dollar_risk,
                "recommended_lot": 0.0,
                "margin_required": 0.0,
                "alternative_symbols": ["1HZ25V", "1HZ50V", "STEPINDEX"]
            }

        # Calculate exact lot size to match target risk
        raw_lot = self.pip_calc.calculate_raw_lot_size(target_dollar_risk, entry_price, stop_loss_price, symbol)
        final_lot = self.pip_calc.round_to_step(raw_lot, symbol)
        actual_dollar_risk = self.pip_calc.calculate_dollar_risk(entry_price, stop_loss_price, final_lot, symbol)
        margin_req = self.pip_calc.calculate_required_margin(entry_price, final_lot, symbol)

        return {
            "viable": True,
            "symbol": symbol,
            "rejection_reason": None,
            "min_lot": min_lot,
            "recommended_lot": final_lot,
            "target_dollar_risk": round(target_dollar_risk, 2),
            "actual_dollar_risk": round(actual_dollar_risk, 2),
            "risk_percent": round((actual_dollar_risk / account_equity) * 100.0, 2),
            "margin_required": round(margin_req, 2),
            "free_margin_buffer_ratio": round((account_equity - margin_req) / margin_req, 1) if margin_req > 0 else 999.0
        }
