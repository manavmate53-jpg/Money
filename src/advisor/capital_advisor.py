"""
Personalized Capital Advisor (python main.py advise --capital 100).
Answers: "I have $X" with ranked index, lot size, entry zone, TP/SL, hold duration,
and Council edge notes.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
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

        report_md = self.generate_markdown_ticket(result)
        os.makedirs("reports", exist_ok=True)
        with open("reports/LATEST_ADVICE.md", "w") as f:
            f.write(report_md)

        return result

    def generate_markdown_ticket(self, data: Dict[str, Any]) -> str:
        """Generates clean GitHub-ready Markdown for reports/LATEST_ADVICE.md."""
        r = data["readiness"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        reasons_md = "\n".join(f"- {reason}" for reason in data["confluence_reasons"])
        disqualified_md = ""
        if data.get("disqualified_instruments"):
            disqualified_md = "\n### Why Other Indices Were Disqualified on $100 Capital\n"
            for dis in data["disqualified_instruments"]:
                disqualified_md += f"- **{dis['symbol']}**: {dis['reason']}\n"

        return rf"""# 🏛 CAPITAL ADVISOR — ${data['capital_analyzed']:.0f} LIVE EXECUTION BLUEPRINT
*Auto-updated every hour by the Execution & Risk Desk (`hourly_execution_desk.yml`)*  
*Last Refresh: **{now}***

---

## 📊 1. 30-DAY CALIBRATION & VAULT SCOREBOARD
- **Readiness State**: `{r['status']}`
- **Simulation Epoch**: `#{r['simulation_epochs']}`
- **Rolling Win Rate**: **{r['win_rate']*100:.1f}%**
- **Profit Factor**: **{r['profit_factor']:.2f}** (Target: $\\ge 1.50$)
- **Max Drawdown**: **{r['max_drawdown_percent']:.1f}%** (Target: $\\le 10.0\%$)
- **Active Bankroll**: **${r['active_bankroll']:.2f}**
- **Locked Profit Vault ("Profit Brought Home")**: **${r['total_vaulted_profit']:.2f}**

---

## 🎯 2. #1 RECOMMENDED INSTRUMENT
- **Instrument**: **`{data['recommended_instrument']}`** *(Ranked #1 for ${data['capital_analyzed']:.0f} capital)*
- **Directional Bias**: **{data['direction']}**
- **Market Regime**: `{data['market_regime']}`
- **Primary Strategy**: `{data['primary_strategy']}`
- **Champion Rule Alignment**: *{data['champions_rule'] or 'Auction Value Area Absorption'}*

---

## 🎫 3. EXACT EXECUTION TICKET
| Parameter | Exact Value | Notes |
| :--- | :--- | :--- |
| **Recommended Lot Size** | **`{data['recommended_lot_size']} lots`** | Granular nano-lot execution |
| **Dollar Risk ($)** | **`${data['dollar_risk']:.2f}`** | **{data['risk_percent']}%** of ${data['capital_analyzed']:.0f} equity |
| **Required Margin** | `${data['margin_required']:.2f}` | Leverage 1:1000 |
| **Entry Zone** | **`{data['entry_zone']}`** | Structural entry band |
| **Stop Loss (SL)** | **`{data['stop_loss']}`** | {data['sl_points']} points distance |
| **Take Profit 1 (TP1)** | **`{data['take_profit_1']}`** | Planned R:R: **{data['risk_reward_ratio']}** |
| **Take Profit 2 (TP2)** | **`{data['take_profit_2']}`** | Major liquidity pool |
| **Cover-Risk-to-Zero** | **`{data['break_even_trigger']}`** | Ratchet SL to Break-Even at $+1.0R$ |
| **Estimated Hold Time** | **{data['hold_time_estimate']}** | Based on $\\text{{ATR}}_{{14}}$ velocity |

---

## 🔍 4. CONFLUENCE SIGNALS
{reasons_md}

---

## ⚠️ 5. GEMINI COUNCIL WARNING / EDGE NOTE
> *{data['council_edge_note']}*

{disqualified_md}
"""
