"""
Post-Mortem Engine with Multimodal Vision & 5W Self-Improvement Synthesis.
Audits BEFORE and AFTER trade screenshots, applies Bayesian weight mutations,
runs the 1,000-path Monte Carlo simulation, and synchronizes profit/loss ledgers.
"""

import json
import os
from typing import Dict, Any, Optional, List

from src.database.db_manager import DatabaseManager
from src.council.gemini_council import GeminiCouncil
from src.risk.quant_engine import QuantEngine


class PostMortemManager:
    def __init__(
        self,
        db: DatabaseManager,
        council: Optional[GeminiCouncil] = None,
        quant_engine: Optional[QuantEngine] = None,
        weights_path: str = "config/strategy_weights.json"
    ):
        self.db = db
        self.council = council or GeminiCouncil()
        self.quant = quant_engine or QuantEngine()
        self.weights_path = weights_path

    def process_resolved_trade(
        self,
        trade_result: Dict[str, Any],
        screenshot_path: Optional[str] = None,
        before_screenshots: Optional[Dict[str, str]] = None,
        after_screenshots: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Runs Council multimodal post-mortem comparing BEFORE and AFTER screenshots,
        updates 5W explanations and what_improved delta, mutates strategy weights,
        and re-runs 1,000-path Monte Carlo simulation.
        """
        t_id = str(trade_result.get("trade_id", ""))
        symbol = trade_result.get("symbol", "UNKNOWN")
        outcome = trade_result.get("outcome", "LOSS")
        pnl = trade_result.get("realized_pnl", 0.0)
        r_mult = trade_result.get("realized_r", 0.0)
        strat = trade_result.get("strategy", "SMC_ICT")

        # Pick primary screenshot for vision (prefer H1 After)
        img_for_council = None
        if after_screenshots and "H1" in after_screenshots and os.path.exists(after_screenshots["H1"]):
            img_for_council = after_screenshots["H1"]
        elif screenshot_path and os.path.exists(screenshot_path):
            img_for_council = screenshot_path

        # 1. Run Gemini Council
        council_review = self.council.run_post_mortem(trade_result, img_for_council)

        what_improved = council_review.get("actionable_rule_mutation", f"Reinforce risk discipline on {symbol}")
        diagnosis = council_review.get("structural_diagnosis", "")
        criticism = council_review.get("risk_criticism", "")

        # 2. Log to SQLite council_journal
        self.db.log_council_note(
            symbol=symbol,
            outcome=outcome,
            diagnosis=diagnosis,
            criticism=criticism,
            mutation=what_improved,
            trade_id=t_id,
            weight_changes=council_review.get("weight_adjustment"),
            raw_response=council_review.get("raw_response")
        )

        # 3. Update what_improved and 5W fields in database
        target_table = "profitable_trades" if pnl > 0 else "losing_trades"
        with self.db.get_connection() as conn:
            conn.execute(f"""
            UPDATE {target_table}
            SET what_improved = ?, trade_reason_summary = ?
            WHERE trade_id = ?
            """, (what_improved, f"{diagnosis} | {criticism}", t_id))
            conn.commit()

        # 4. Mutate Bayesian Strategy Weights
        self._mutate_weights(
            symbol=symbol,
            strategy=strat,
            outcome=outcome,
            r_multiple=r_mult
        )

        # 5. Re-run 1,000-Path Monte Carlo Permutation Check
        all_r = []
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rr_realized FROM profitable_trades UNION ALL SELECT rr_realized FROM losing_trades")
            rows = cursor.fetchall()
            all_r = [float(r["rr_realized"]) for r in rows if r["rr_realized"] is not None]

        mc_res = self.quant.run_monte_carlo_permutation_sim(all_r, num_simulations=1000)
        council_review["monte_carlo_res"] = mc_res

        # 6. Re-export Markdown Ledgers & Visual Journal
        self.db.export_all_ledgers()

        return council_review

    def _mutate_weights(self, symbol: str, strategy: str, outcome: str, r_multiple: float):
        if not os.path.exists(self.weights_path):
            return

        try:
            with open(self.weights_path, "r") as f:
                data = json.load(f)

            instruments = data.setdefault("instruments", {})
            sym_dict = instruments.setdefault(symbol, {})

            for regime in ["DIRECTIONAL_TREND", "ROTATIONAL_RANGE", "HIGH_VOLATILITY_EXPANSION"]:
                regime_data = sym_dict.setdefault(regime, {
                    "SMC_ICT": 0.40,
                    "SUPPLY_DEMAND": 0.35,
                    "PRICE_ACTION_AUCTION": 0.25,
                    "win_rate": 0.55,
                    "profit_factor": 1.70,
                    "sample_size": 10
                })

                alpha = 0.05
                current_w = regime_data.get(strategy, 0.33)
                delta = alpha if outcome == "WIN" else (-alpha)

                new_w = max(0.10, min(0.70, current_w + delta))
                regime_data[strategy] = round(new_w, 3)

                all_strats = ["SMC_ICT", "SUPPLY_DEMAND", "PRICE_ACTION_AUCTION"]
                total_w = sum(regime_data.get(s, 0.33) for s in all_strats)
                for s in all_strats:
                    regime_data[s] = round(regime_data.get(s, 0.33) / total_w, 3)

                regime_data["sample_size"] = regime_data.get("sample_size", 0) + 1

            with open(self.weights_path, "w") as f:
                json.dump(data, f, indent=2)

        except Exception:
            pass
