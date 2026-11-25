"""
Post-Mortem Engine with Multimodal Vision, 5W Self-Improvement Synthesis,
and Machine-Enforced Rule Mutations (config/active_filters.json).
"""

import json
import os
from datetime import datetime, timezone
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
        weights_path: str = "config/strategy_weights.json",
        filters_path: str = "config/active_filters.json",
        reports_dir: str = "reports",
        filter_mutation_batch_size: int = 5,
        pattern_memory: Any = None,
    ):
        self.db = db
        self.council = council or GeminiCouncil()
        self.quant = quant_engine or QuantEngine()
        self.weights_path = weights_path
        self.filters_path = filters_path
        self.reports_dir = reports_dir
        self.filter_mutation_batch_size = max(1, int(filter_mutation_batch_size))
        self.pattern_memory = pattern_memory

    def process_resolved_trade(
        self,
        trade_result: Dict[str, Any],
        screenshot_path: Optional[str] = None,
        before_screenshots: Optional[Dict[str, str]] = None,
        after_screenshots: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Runs Council multimodal post-mortem comparing BEFORE and AFTER screenshots,
        updates 5W explanations and what_improved delta, writes executable rules to
        config/active_filters.json, mutates strategy_weights.json, runs Monte Carlo,
        and re-exports Markdown ledgers.
        """
        t_id = str(trade_result.get("trade_id", ""))
        symbol = trade_result.get("symbol", "UNKNOWN")
        outcome = trade_result.get("outcome", "LOSS")
        pnl = float(trade_result.get("realized_pnl", 0.0) or 0.0)
        r_mult = float(trade_result.get("realized_r", 0.0) or 0.0)
        strat = trade_result.get("strategy", trade_result.get("strategy_framework", "SMC_ICT"))

        before_h1 = before_screenshots.get("H1") if before_screenshots else None
        after_h1 = after_screenshots.get("H1") if after_screenshots else None

        img_fallback = after_h1 if (after_h1 and os.path.exists(after_h1)) else (
            screenshot_path if (screenshot_path and os.path.exists(screenshot_path)) else before_h1
        )

        # 1. Run Gemini Council Multimodal Audit (passing ALL 6 BEFORE/AFTER charts + PrimeFX Playbook)
        council_review = self.council.run_post_mortem(
            trade_result,
            image_path=img_fallback,
            before_image_path=before_h1,
            after_image_path=after_h1,
            before_screenshots=before_screenshots,
            after_screenshots=after_screenshots,
        )

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

        # 3. Update 5W fields & what_improved in split profit/loss table
        target_table = "profitable_trades" if pnl > 0 else "losing_trades"
        with self.db.get_connection() as conn:
            conn.execute(f"""
            UPDATE {target_table}
            SET what_improved = ?, trade_reason_summary = ?
            WHERE trade_id = ?
            """, (what_improved, f"{diagnosis} | {criticism}", t_id))
            conn.commit()

        # 4. Speed 2: Evaluate Batch Hard-Filter Mutation (mutates hard rules only at n >= 5 trades)
        hard_mutated = self._update_active_filters(symbol=symbol, trade_id=t_id, outcome=outcome, review=council_review)
        council_review["hard_filter_mutated"] = hard_mutated

        # 5. Speed 1: Gentle immediate Bayesian Strategy Weight Nudge (±0.02)
        self._mutate_weights(
            symbol=symbol,
            strategy=strat,
            outcome=outcome,
            r_multiple=r_mult,
            suggested_deltas=council_review.get("weight_adjustment")
        )
        council_review["weight_nudge_cap"] = 0.02

        # 5b. Continuously append closed trade vector into k-NN Pattern Memory (if tracked)
        if t_id and self.pattern_memory is not None:
            saved_feats = self.db.get_kv(f"trade_features:{t_id}")
            if isinstance(saved_feats, list) and len(saved_feats) == 8:
                self.pattern_memory.record_resolved_trade(
                    symbol=symbol,
                    direction=trade_result.get("direction", "BULLISH"),
                    feature_vector=saved_feats,
                    outcome_r=r_mult,
                )

        # 6. Re-run 1,000-Path Monte Carlo Permutation Check
        all_r: List[float] = []
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rr_realized FROM profitable_trades UNION ALL SELECT rr_realized FROM losing_trades")
            rows = cursor.fetchall()
            all_r = [float(r["rr_realized"]) for r in rows if r["rr_realized"] is not None]

        mc_res = self.quant.run_monte_carlo_permutation_sim(all_r, num_simulations=1000)
        council_review["monte_carlo_res"] = mc_res

        # 7. Re-export Markdown Ledgers & Visual Journal
        self.db.export_all_ledgers(reports_dir=self.reports_dir)

        return council_review

    def _update_active_filters(self, symbol: str, trade_id: str, outcome: str, review: Dict[str, Any]) -> bool:
        """
        Two-Speed Learning — Speed 2:
        Accumulates resolved trade telemetry per symbol and mutates hard entry filters in
        config/active_filters.json ONLY once `n >= filter_mutation_batch_size` (default 5) trades
        have accrued since the last filter update, preventing CSPRNG noise overfitting.
        """
        if not self.filters_path:
            return False
        os.makedirs(os.path.dirname(os.path.abspath(self.filters_path)), exist_ok=True)

        data: Dict[str, Any] = {
            "version": "2.0.0",
            "min_sweep_confirmation_candles": 2,
            "max_pending_hours": 6,
            "min_rr_ratio": 2.0,
            "atr_sl_buffer_multiplier": 0.5,
            "entry_zone_m15_atr_width": 0.25,
            "require_macro_bias_alignment": True,
            "blocked_regimes_by_symbol": {},
            "symbol_overrides": {},
            "filter_mutation_state": {},
            "recent_mutations": []
        }
        if os.path.exists(self.filters_path):
            try:
                with open(self.filters_path, "r") as f:
                    data = json.load(f)
            except Exception:
                pass

        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        overrides = data.setdefault("symbol_overrides", {})
        sym_cfg = overrides.setdefault(symbol, {})
        base_candles = int(data.get("min_sweep_confirmation_candles", 2))
        base_rr = float(data.get("min_rr_ratio", 2.0))
        sym_cfg.setdefault("min_sweep_confirmation_candles", base_candles)
        sym_cfg.setdefault("min_rr_ratio", base_rr)

        mutation_state = data.setdefault("filter_mutation_state", {})
        sym_state = mutation_state.setdefault(symbol, {
            "trades_since_mutation": 0,
            "losses_in_batch": 0,
            "pending_recommendations": [],
            "last_mutated_at": None,
        })
        sym_state["trades_since_mutation"] = int(sym_state.get("trades_since_mutation", 0)) + 1

        rec_candles = int(review.get("recommended_min_sweep_candles", 2))
        rec_rr = float(review.get("recommended_min_rr", 2.0))
        flaw = str(review.get("primefx_flaw_detected", "NONE")).upper()

        if outcome == "LOSS":
            sym_state["losses_in_batch"] = int(sym_state.get("losses_in_batch", 0)) + 1
            pending_recs = sym_state.setdefault("pending_recommendations", [])
            pending_recs.append({
                "recommended_min_sweep_candles": rec_candles,
                "recommended_min_rr": rec_rr,
                "flaw": flaw,
            })

        n_batch = sym_state["trades_since_mutation"]
        hard_mutated = False

        if n_batch >= self.filter_mutation_batch_size:
            pending_recs = sym_state.get("pending_recommendations", [])
            if sym_state.get("losses_in_batch", 0) > 0 and pending_recs:
                max_rec_candles = max(int(r.get("recommended_min_sweep_candles", base_candles)) for r in pending_recs)
                max_rec_rr = max(float(r.get("recommended_min_rr", base_rr)) for r in pending_recs)
                sym_cfg["min_sweep_confirmation_candles"] = max(2, min(4, max_rec_candles))
                sym_cfg["min_rr_ratio"] = round(max(2.0, min(3.5, max_rec_rr)), 2)

                blocked = data.setdefault("blocked_regimes_by_symbol", {})
                sym_blocked = blocked.setdefault(symbol, [])
                if "RANDOM_WALK_CHOP" not in sym_blocked:
                    sym_blocked.append("RANDOM_WALK_CHOP")

                batch_flaws = {str(r.get("flaw", "NONE")).upper() for r in pending_recs}
                if "PRE_INDUCEMENT_TRAP" in batch_flaws:
                    data["require_inducement_sweep"] = True
                if "REVENGE_REENTRY" in batch_flaws:
                    data["revenge_cooldown_hours"] = round(max(2.0, float(data.get("revenge_cooldown_hours", 2.0))), 1)
                if "JUMP_SPIKE_STOPOUT" in batch_flaws:
                    sym_cfg["atr_sl_buffer_multiplier"] = round(max(0.75, float(sym_cfg.get("atr_sl_buffer_multiplier", 0.5)) + 0.15), 2)
                hard_mutated = True

            # Reset Speed-2 counter for next batch window
            sym_state["trades_since_mutation"] = 0
            sym_state["losses_in_batch"] = 0
            sym_state["pending_recommendations"] = []
            sym_state["last_mutated_at"] = data["updated_at"]

        mutations = data.setdefault("recent_mutations", [])
        mutations.insert(0, {
            "timestamp": data["updated_at"],
            "trade_id": trade_id,
            "symbol": symbol,
            "outcome": outcome,
            "speed": "SPEED_2_HARD_FILTER_MUTATION" if hard_mutated else "SPEED_1_WEIGHT_NUDGE",
            "hard_filter_mutated": hard_mutated,
            "cluster_trade_count": n_batch,
            "edge_class": review.get("primefx_edge_classification", ""),
            "flaw_detected": flaw,
            "rule": review.get("actionable_rule_mutation", "")
        })
        data["recent_mutations"] = mutations[:15]

        with open(self.filters_path, "w") as f:
            json.dump(data, f, indent=2)
        return hard_mutated

    def _mutate_weights(
        self,
        symbol: str,
        strategy: str,
        outcome: str,
        r_multiple: float,
        suggested_deltas: Optional[Dict[str, float]] = None
    ):
        if not os.path.exists(self.weights_path):
            return

        try:
            with open(self.weights_path, "r") as f:
                data = json.load(f)

            instruments = data.setdefault("instruments", {})
            sym_dict = instruments.setdefault(symbol, {})
            for regime in ["DIRECTIONAL_TREND", "ROTATIONAL_RANGE", "HIGH_VOLATILITY_EXPANSION"]:
                regime_data = sym_dict.setdefault(regime, {
                    "SMC_ICT": 0.45,
                    "SUPPLY_DEMAND": 0.30,
                    "PRICE_ACTION": 0.25,
                    "win_rate": 0.55,
                    "profit_factor": 1.70,
                    "sample_size": 10
                })

                # Dynamically preserve key naming (e.g. PRICE_ACTION vs legacy PRICE_ACTION_AUCTION)
                pa_key = "PRICE_ACTION_AUCTION" if "PRICE_ACTION_AUCTION" in regime_data else "PRICE_ACTION"
                all_strats = ["SMC_ICT", "SUPPLY_DEMAND", pa_key]

                # Speed 1: Gentle immediate ±0.02 nudge so single CSPRNG variance never overreacts
                outcome_alpha = 0.02 if outcome == "WIN" else -0.02
                for s_name in all_strats:
                    cur_w = regime_data.get(s_name, 0.33)
                    ai_delta = float((suggested_deltas or {}).get(s_name, 0.0))
                    if s_name == pa_key and ai_delta == 0.0:
                        ai_delta = float((suggested_deltas or {}).get("PRICE_ACTION", 0.0))
                    ai_delta = max(-0.02, min(0.02, ai_delta))
                    eff_delta = max(-0.02, min(0.02, (outcome_alpha if s_name == strategy else 0.0) + (ai_delta * 0.5)))
                    regime_data[s_name] = round(max(0.10, min(0.70, cur_w + eff_delta)), 3)

                total_w = sum(regime_data.get(s, 0.33) for s in all_strats)
                for s in all_strats:
                    regime_data[s] = round(regime_data.get(s, 0.33) / total_w, 3)

                regime_data["sample_size"] = regime_data.get("sample_size", 0) + 1

            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            with open(self.weights_path, "w") as f:
                json.dump(data, f, indent=2)

        except Exception:
            pass
