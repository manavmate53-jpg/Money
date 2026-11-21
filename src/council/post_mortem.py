"""
Post-Mortem Engine & Bayesian Strategy Weight Mutator.
Applies Bayesian / Exponential Moving Expectancy updates to strategy_weights.json.
"""

import json
import os
from typing import Dict, Any, Optional

from src.database.db_manager import DatabaseManager
from src.council.gemini_council import GeminiCouncil


class PostMortemManager:
    def __init__(
        self,
        db: DatabaseManager,
        council: Optional[GeminiCouncil] = None,
        weights_path: str = "config/strategy_weights.json"
    ):
        self.db = db
        self.council = council or GeminiCouncil()
        self.weights_path = weights_path

    def process_resolved_trade(self, trade_result: Dict[str, Any], screenshot_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Runs Council post-mortem and mutates strategy_weights.json based on empirical outcome.
        """
        # Run Gemini Council
        council_review = self.council.run_post_mortem(trade_result, screenshot_path)

        # Log to SQLite
        self.db.log_council_note(
            symbol=trade_result.get("symbol", "UNKNOWN"),
            outcome=trade_result.get("outcome", "LOSS"),
            diagnosis=council_review["structural_diagnosis"],
            criticism=council_review["risk_criticism"],
            mutation=council_review["actionable_rule_mutation"],
            trade_id=trade_result.get("trade_id"),
            weight_changes=council_review.get("weight_adjustment"),
            raw_response=council_review.get("raw_response")
        )

        # Mutate Bayesian weights
        self._mutate_weights(
            symbol=trade_result.get("symbol", "1HZ25V"),
            strategy=trade_result.get("strategy", "SMC_ICT"),
            outcome=trade_result.get("outcome", "LOSS"),
            r_multiple=trade_result.get("realized_r", 0.0)
        )

        return council_review

    def _mutate_weights(self, symbol: str, strategy: str, outcome: str, r_multiple: float):
        if not os.path.exists(self.weights_path):
            return

        try:
            with open(self.weights_path, "r") as f:
                data = json.load(f)

            instruments = data.setdefault("instruments", {})
            sym_dict = instruments.setdefault(symbol, {})

            # Update for each regime
            for regime in ["DIRECTIONAL_TREND", "ROTATIONAL_RANGE", "HIGH_VOLATILITY_EXPANSION"]:
                regime_data = sym_dict.setdefault(regime, {
                    "SMC_ICT": 0.40,
                    "SUPPLY_DEMAND": 0.35,
                    "PRICE_ACTION_AUCTION": 0.25,
                    "win_rate": 0.55,
                    "profit_factor": 1.70,
                    "sample_size": 10
                })

                # Bayesian EMA adjustment
                alpha = 0.05
                current_w = regime_data.get(strategy, 0.33)
                delta = alpha if outcome == "WIN" else (-alpha)

                new_w = max(0.10, min(0.70, current_w + delta))
                regime_data[strategy] = round(new_w, 3)

                # Re-normalize weights to sum to 1.0
                all_strats = ["SMC_ICT", "SUPPLY_DEMAND", "PRICE_ACTION_AUCTION"]
                total_w = sum(regime_data.get(s, 0.33) for s in all_strats)
                for s in all_strats:
                    regime_data[s] = round(regime_data.get(s, 0.33) / total_w, 3)

                # Update sample size and metrics
                regime_data["sample_size"] = regime_data.get("sample_size", 0) + 1

            with open(self.weights_path, "w") as f:
                json.dump(data, f, indent=2)

        except Exception:
            pass
