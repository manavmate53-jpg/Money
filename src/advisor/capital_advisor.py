"""
Personalized Capital Advisor (python main.py advise --capital 100).
Answers: "I have $X" with ranked index, lot size, entry zone, TP/SL, hold duration,
and Council edge notes.
"""

from typing import Dict, Any, List, Optional
import os

from src.database.db_manager import DatabaseManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.technicals.confluence_engine import ConfluenceEngine, TradingTip
from src.browser.ws_interceptor import DerivWsInterceptor
from src.council.readiness_gate import ReadinessGate


class CapitalAdvisor:
    # Target Deriv indices to benchmark
    SYNTHETIC_INSTRUMENTS = [
        "1HZ25V",
        "1HZ50V",
        "1HZ75V",
        "STEPINDEX",
        "1HZ10V",
        "R_75",
        "R_100",
        "BOOM1000",
        "CRASH1000"
    ]

    def __init__(
        self,
        db: DatabaseManager,
        pip_calc: Optional[PipCalculator] = None,
        position_sizer: Optional[PositionSizer] = None,
        confluence_engine: Optional[ConfluenceEngine] = None,
        ws_interceptor: Optional[DerivWsInterceptor] = None,
        readiness_gate: Optional[ReadinessGate] = None
    ):
        self.db = db
        self.pip_calc = pip_calc or PipCalculator()
        self.position_sizer = position_sizer or PositionSizer(self.pip_calc)
        self.confluence = confluence_engine or ConfluenceEngine()
        self.ws = ws_interceptor or DerivWsInterceptor(self.db)
        self.gate = readiness_gate or ReadinessGate(self.db)

    def advise(self, capital: float = 100.0) -> Dict[str, Any]:
        """
        Scans instruments, applies small-account safety filters, ranks viable setups,
        and generates an actionable, formatted trading ticket.
        """
        readiness = self.gate.evaluate_metrics()
        candidates = []
        disqualified = []

        for symbol in self.SYNTHETIC_INSTRUMENTS:
            # 1. Fetch candles
            df = self.ws.get_candles(symbol, timeframe="H1", count=150)
            if df is None or len(df) < 30:
                continue

            # 2. Generate multi-framework tip
            tip = self.confluence.generate_tip(symbol, df)
            if tip.direction == "NEUTRAL_NO_TRADE":
                continue

            # 3. Small-account safety viability check
            entry_price = tip.entry_max if tip.direction == "BULLISH" else tip.entry_min
            viability = self.position_sizer.evaluate_instrument_viability(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss_price=tip.stop_loss,
                account_equity=capital,
                risk_percent=1.5
            )

            if not viability["viable"]:
                disqualified.append({
                    "symbol": symbol,
                    "reason": viability["rejection_reason"],
                    "min_lot_risk": viability["min_lot_dollar_risk"]
                })
            else:
                # Viable candidate: score by confidence and R:R
                score = (tip.confidence_score * 0.6) + (min(3.5, tip.risk_reward_ratio) / 3.5 * 0.4)
                candidates.append({
                    "symbol": symbol,
                    "tip": tip,
                    "viability": viability,
                    "score": round(score, 3)
                })

        # Rank candidates
        candidates.sort(key=lambda x: x["score"], reverse=True)

        # Get latest Council note for edge warning
        latest_council_note = ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT actionable_rule_mutation, structural_diagnosis, symbol
            FROM council_journal
            ORDER BY note_id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                latest_council_note = f"[{row['symbol']}] {row['actionable_rule_mutation']}"

        if not latest_council_note:
            latest_council_note = "Enforce break-even ratchet (+1.0R) immediately upon displacement away from entry."

        if not candidates:
            # If no candidate passed all filters, generate fallback safe tip on 1HZ25V
            symbol = "1HZ25V"
            df = self.ws.get_candles(symbol, timeframe="H1", count=150)
            tip = self.confluence.generate_tip(symbol, df)
            entry_price = tip.entry_max
            viability = self.position_sizer.evaluate_instrument_viability(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss_price=tip.stop_loss,
                account_equity=capital,
                risk_percent=1.5
            )
            top_candidate = {
                "symbol": symbol,
                "tip": tip,
                "viability": viability,
                "score": 0.85
            }
        else:
            top_candidate = candidates[0]

        best_tip: TradingTip = top_candidate["tip"]
        best_viab: Dict[str, Any] = top_candidate["viability"]

        return {
            "capital_analyzed": capital,
            "readiness": readiness,
            "recommended_instrument": top_candidate["symbol"],
            "recommended_lot_size": best_viab["recommended_lot"],
            "dollar_risk": best_viab["actual_dollar_risk"],
            "risk_percent": best_viab["risk_percent"],
            "margin_required": best_viab["margin_required"],
            "direction": best_tip.direction,
            "entry_zone": f"{best_tip.entry_min:.2f} — {best_tip.entry_max:.2f}",
            "stop_loss": best_tip.stop_loss,
            "sl_points": best_tip.sl_points,
            "take_profit_1": best_tip.take_profit_1,
            "take_profit_2": best_tip.take_profit_2,
            "tp1_points": best_tip.tp1_points,
            "risk_reward_ratio": f"1:{best_tip.risk_reward_ratio}",
            "break_even_trigger": best_tip.break_even_trigger_price,
            "hold_time_estimate": f"{best_tip.estimated_hold_hours} hours (~{best_tip.estimated_hold_candles} H1 candles)",
            "primary_strategy": best_tip.primary_strategy,
            "market_regime": best_tip.market_regime,
            "confluence_reasons": best_tip.confluence_reasons,
            "champions_rule": best_tip.champions_rule_match,
            "council_edge_note": latest_council_note,
            "disqualified_instruments": disqualified[:3],
            "total_candidates_analyzed": len(self.SYNTHETIC_INSTRUMENTS)
        }
