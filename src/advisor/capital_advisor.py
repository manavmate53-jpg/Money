"""
Personalized Capital Advisor (python main.py --cycle advise --capital 100).
Ranks safe nano-lot Deriv indices aligned with config/macro_bias.json, computes exact
ATR-calibrated entry zones, SL/TP levels, margin, and emits reports/LATEST_ADVICE.md.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from src.database.db_manager import DatabaseManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.risk.bankroll_manager import BankrollManager, get_asset_bucket
from src.technicals.confluence_engine import ConfluenceEngine, TradingTip
from src.technicals.pattern_memory import PatternMemoryEngine
from src.browser.ws_interceptor import DerivWsInterceptor
from src.council.readiness_gate import ReadinessGate


class CapitalAdvisor:
    # Candidate Deriv indices (nano-lot safe VIX + Step + Jump Indices prioritized cleanly)
    SYNTHETIC_INSTRUMENTS = [
        "STEPINDEX",
        "JD75",
        "JD100",
        "1HZ25V",
        "1HZ50V",
        "1HZ10V",
        "1HZ75V",
        "1HZ100V",
        "JD10",
        "JD25",
        "JD50",
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
        readiness_gate: Optional[ReadinessGate] = None,
        macro_bias_path: str = "config/macro_bias.json",
    ):
        self.db = db
        self.pip_calc = pip_calc or PipCalculator()
        self.position_sizer = position_sizer or PositionSizer(self.pip_calc)
        self.pattern_memory = PatternMemoryEngine(db=self.db)
        self.confluence = confluence_engine or ConfluenceEngine(pattern_memory=self.pattern_memory)
        self.ws = ws_interceptor or DerivWsInterceptor(self.db)
        self.gate = readiness_gate or ReadinessGate(self.db)
        self.macro_bias_path = macro_bias_path

    def _load_macro_bias_map(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(self.macro_bias_path):
            try:
                with open(self.macro_bias_path, "r") as f:
                    instruments = json.load(f).get("instruments", {})
                return instruments
            except Exception:
                pass
        return {}

    def advise(self, capital: float = 100.0, report_path: str = "reports/LATEST_ADVICE.md") -> Dict[str, Any]:
        """
        Scans instruments across H1 + M15 candles against H4 macro bias, applies strict
        small-account viability gates & Jump-Index gap penalty, keeps live recommendations
        locked to 1 primary trade (plus 1 uncorrelated backup setup), and writes reports/LATEST_ADVICE.md.
        """
        readiness = self.gate.evaluate_metrics(write_report=False)
        bankroll = BankrollManager(self.db)
        b_stat = bankroll.get_status()
        equity_floor = float(b_stat.get("hard_equity_floor", 60.0))
        active_bal = float(b_stat.get("active_bankroll", capital))
        breaker_active = bool(b_stat.get("drawdown_breaker_active", False))

        # Enforce hard $60.00 ($40 max drawdown) equity floor & circuit breaker in LATEST_ADVICE.md
        if breaker_active or active_bal <= equity_floor or capital <= equity_floor:
            halt_reason = (
                f"HARD_EQUITY_FLOOR_HALT: Active bankroll ${active_bal:.2f} / capital ${capital:.2f} "
                f"at or below ${equity_floor:.2f} ($40 max-drawdown shield). Live recommendations paused."
            )
            halt_tip = TradingTip(
                symbol="STEPINDEX",
                direction="NEUTRAL_NO_TRADE",
                entry_mode="NONE",
                confidence_score=0.0,
                primary_strategy="NONE",
                market_regime="HALTED_EQUITY_SHIELD",
                current_price=0.0,
                entry_min=0.0,
                entry_max=0.0,
                stop_loss=0.0,
                take_profit_1=0.0,
                take_profit_2=0.0,
                sl_points=0.0,
                tp1_points=0.0,
                risk_reward_ratio=0.0,
                break_even_trigger_price=0.0,
                estimated_hold_hours=0.0,
                estimated_hold_candles=0,
                confluence_reasons=[halt_reason],
            )
            halt_res = {
                "capital_analyzed": capital,
                "readiness": readiness,
                "recommended_instrument": "STEPINDEX",
                "asset_bucket": get_asset_bucket("STEPINDEX"),
                "entry_mode": "NONE",
                "direction": "NEUTRAL_NO_TRADE",
                "market_regime": "HALTED_EQUITY_SHIELD",
                "primary_strategy": "NONE",
                "champions_rule": "Hard $60.00 ($40 Max-Drawdown) Equity Shield",
                "recommended_lot_size": 0.0,
                "dollar_risk": 0.0,
                "risk_percent": 0.0,
                "margin_required": 0.0,
                "entry_zone": f"HALTED (Equity ${active_bal:.2f} <= ${equity_floor:.2f} floor)",
                "stop_loss": "N/A (Halted)",
                "sl_points": "0.00",
                "take_profit_1": "N/A (Halted)",
                "take_profit_2": "N/A (Halted)",
                "risk_reward_ratio": "N/A (Equity Floor Active)",
                "break_even_trigger": "N/A (Halted)",
                "hold_time_estimate": "N/A (Halted)",
                "confluence_reasons": halt_tip.confluence_reasons,
                "council_edge_note": halt_reason,
                "backup_setup": None,
                "disqualified_instruments": [{"symbol": "ALL", "reason": halt_reason, "min_lot_risk": 0.0}],
            }
            if report_path:
                os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
                with open(report_path, "w") as f:
                    f.write(self.generate_markdown_ticket(halt_res))
            return halt_res

        max_live_risk_dollars = round(capital * 0.015, 2)
        macro_map = self._load_macro_bias_map()
        candidates: List[Dict[str, Any]] = []
        disqualified: List[Dict[str, Any]] = []

        for symbol in self.SYNTHETIC_INSTRUMENTS:
            df_h1 = self.ws.get_candles(symbol, timeframe="H1", count=150)
            if df_h1 is None or len(df_h1) < 30:
                continue
            df_m15 = self.ws.get_candles(symbol, timeframe="M15", count=150)
            df_h4 = self.ws.get_candles(symbol, timeframe="H4", count=100)
            hours_loss = self.db.get_hours_since_last_loss(symbol)

            macro_info = macro_map.get(symbol, {})
            macro_bias = macro_info.get("macro_bias") if isinstance(macro_info, dict) else macro_info
            macro_conf = macro_info.get("confidence_score") if isinstance(macro_info, dict) else None
            tip = self.confluence.generate_tip(
                symbol,
                df_h1,
                m15_df=df_m15,
                h4_df=df_h4,
                macro_bias=macro_bias,
                macro_confidence=macro_conf,
                hours_since_last_loss=hours_loss,
            )
            if tip.direction == "NEUTRAL_NO_TRADE":
                continue

            entry_price = (
                tip.current_price
                if tip.entry_mode == "MARKET_CONFIRMATION"
                else (tip.entry_max if tip.direction == "BULLISH" else tip.entry_min)
            )
            viability = self.position_sizer.evaluate_instrument_viability(
                symbol=symbol,
                entry_price=entry_price,
                stop_loss_price=tip.stop_loss,
                account_equity=capital,
                risk_percent=1.5
            )

            actual_or_min_risk = float(
                viability.get("min_lot_dollar_risk", viability.get("actual_dollar_risk", 0.0))
            )
            if not viability["viable"] or float(viability.get("actual_dollar_risk", actual_or_min_risk)) > (max_live_risk_dollars + 1e-6):
                reason_msg = viability["rejection_reason"] or (
                    f"DISQUALIFIED_ON_${capital:.0f}: Risk ${float(viability.get('actual_dollar_risk', actual_or_min_risk)):.2f} "
                    f"exceeds live advisor ${max_live_risk_dollars:.2f} (1.5%) ceiling"
                )
                disqualified.append({
                    "symbol": symbol,
                    "reason": reason_msg,
                    "min_lot_risk": actual_or_min_risk,
                })
            else:
                score = (tip.confidence_score * 0.6) + (min(3.5, tip.risk_reward_ratio) / 3.5 * 0.4)
                # Fix 4: Apply -0.12 Jump-Index ranking penalty unless high-conf (>=82%) & >=3.0R
                is_jump = symbol.upper().startswith("JD")
                if is_jump and not (tip.confidence_score >= 0.82 and tip.risk_reward_ratio >= 3.0):
                    score -= 0.12
                candidates.append({
                    "symbol": symbol,
                    "bucket": get_asset_bucket(symbol),
                    "tip": tip,
                    "viability": viability,
                    "score": round(score, 3)
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)

        latest_council_note = ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT actionable_rule_mutation, symbol
            FROM council_journal
            ORDER BY note_id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                latest_council_note = f"[{row['symbol']}] {row['actionable_rule_mutation']}"

        if not latest_council_note:
            latest_council_note = "Enforce 2-candle sweep confirmation and break-even ratchet (+1.0R) upon displacement."

        if not candidates:
            symbol = "STEPINDEX"
            df_h1 = self.ws.get_candles(symbol, timeframe="H1", count=150)
            df_m15 = self.ws.get_candles(symbol, timeframe="M15", count=150)
            df_h4 = self.ws.get_candles(symbol, timeframe="H4", count=100)
            probe_tip = self.confluence.generate_tip(
                symbol, df_h1, m15_df=df_m15, h4_df=df_h4,
                hours_since_last_loss=self.db.get_hours_since_last_loss(symbol)
            )
            standby_tip = probe_tip.model_copy(update={
                "direction": "NEUTRAL_NO_TRADE",
                "entry_mode": "NONE",
                "primary_strategy": "NONE",
                "risk_reward_ratio": 0.0,
            })
            top_candidate = {
                "symbol": symbol,
                "tip": standby_tip,
                "viability": {"viable": False, "recommended_lot": 0.0, "actual_dollar_risk": 0.0, "risk_percent": 0.0, "margin_required": 0.0},
                "score": 0.0
            }
        else:
            top_candidate = candidates[0]

        top_bucket = get_asset_bucket(top_candidate["symbol"])
        backup_cand = next((c for c in candidates if c.get("bucket", get_asset_bucket(c["symbol"])) != top_bucket), None)
        backup_setup = None
        if backup_cand:
            b_tip: TradingTip = backup_cand["tip"]
            b_viab: Dict[str, Any] = backup_cand["viability"]
            backup_setup = {
                "symbol": b_tip.symbol,
                "bucket": backup_cand["bucket"],
                "direction": b_tip.direction,
                "entry_mode": b_tip.entry_mode,
                "lot_size": b_viab.get("recommended_lot", b_viab.get("min_lot", 0.1)),
                "dollar_risk": b_viab.get("actual_dollar_risk", 1.50),
                "entry_zone": f"{min(b_tip.entry_min, b_tip.entry_max):.2f} - {max(b_tip.entry_min, b_tip.entry_max):.2f}",
                "stop_loss": f"{b_tip.stop_loss:.2f}",
                "take_profit_1": f"{b_tip.take_profit_1:.2f}",
                "risk_reward_ratio": f"1:{b_tip.risk_reward_ratio:.1f}",
            }

        best_tip: TradingTip = top_candidate["tip"]
        best_viab: Dict[str, Any] = top_candidate["viability"]

        is_standby = best_tip.direction == "NEUTRAL_NO_TRADE"
        entry_ref = best_tip.entry_max if best_tip.direction == "BULLISH" else best_tip.entry_min
        sl_dist = abs(entry_ref - best_tip.stop_loss)
        be_price = (entry_ref + sl_dist) if best_tip.direction == "BULLISH" else (entry_ref - sl_dist)
        hold_time_str = f"{best_tip.estimated_hold_hours:.1f} hours ({best_tip.estimated_hold_candles} bars)"

        result = {
            "capital_analyzed": capital,
            "readiness": readiness,
            "recommended_instrument": best_tip.symbol,
            "asset_bucket": top_bucket,
            "entry_mode": best_tip.entry_mode,
            "direction": best_tip.direction,
            "market_regime": best_tip.market_regime,
            "primary_strategy": best_tip.primary_strategy,
            "champions_rule": best_tip.champions_rule_match,
            "recommended_lot_size": 0.0 if is_standby else best_viab.get("recommended_lot", best_viab.get("min_lot", 0.1)),
            "dollar_risk": 0.0 if is_standby else best_viab.get("actual_dollar_risk", round(capital * 0.015, 2)),
            "risk_percent": 0.0 if is_standby else best_viab.get("risk_percent", 1.5),
            "margin_required": 0.0 if is_standby else best_viab.get("margin_required", 0.0),
            "entry_zone": f"STANDBY @ {best_tip.current_price:.2f} (Awaiting H4 Confluence)" if is_standby else f"{min(best_tip.entry_min, best_tip.entry_max):.2f} - {max(best_tip.entry_min, best_tip.entry_max):.2f}",
            "stop_loss": "N/A (Standby)" if is_standby else f"{best_tip.stop_loss:.2f}",
            "sl_points": "0.00" if is_standby else f"{best_tip.sl_points if best_tip.sl_points > 0 else sl_dist:.2f}",
            "take_profit_1": "N/A (Standby)" if is_standby else f"{best_tip.take_profit_1:.2f}",
            "take_profit_2": "N/A (Standby)" if is_standby else f"{best_tip.take_profit_2:.2f}",
            "risk_reward_ratio": f"1:{best_tip.risk_reward_ratio:.1f}" if (not is_standby and best_tip.risk_reward_ratio > 0) else "N/A (No Active Setup)",
            "break_even_trigger": "N/A (Standby)" if is_standby else f"{be_price:.2f} (+1.0R displacement)",
            "hold_time_estimate": "N/A (Awaiting Valid Entry)" if is_standby else hold_time_str,
            "confluence_reasons": best_tip.confluence_reasons or ["Awaiting H4 macro alignment & 2-candle confirmation"],
            "council_edge_note": latest_council_note,
            "backup_setup": backup_setup,
            "disqualified_instruments": disqualified,
        }

        if report_path:
            report_md = self.generate_markdown_ticket(result)
            os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
            with open(report_path, "w") as f:
                f.write(report_md)

        return result

    def generate_markdown_ticket(self, data: Dict[str, Any]) -> str:
        """Generates clean GitHub-ready Markdown for reports/LATEST_ADVICE.md."""
        r = data["readiness"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        reasons_md = "\n".join(f"- {reason}" for reason in data["confluence_reasons"])
        disqualified_md = ""
        if data.get("disqualified_instruments"):
            disqualified_md = f"\n### Why Other Indices Were Disqualified on ${data['capital_analyzed']:.0f} Capital\n"
            for dis in data["disqualified_instruments"]:
                disqualified_md += f"- **{dis['symbol']}**: {dis['reason']}\n"

        backup_md = ""
        if data.get("backup_setup"):
            b = data["backup_setup"]
            backup_md = (
                f"\n### Next Best Backup Setup (Different Bucket — `{b['bucket']}`)\n"
                f"| Symbol | Bias | Entry Mode | Lot | Risk | Entry Zone | SL | TP1 | R:R |\n"
                f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
                f"| `{b['symbol']}` | {b['direction']} | `{b['entry_mode']}` | {b['lot_size']} | ${b['dollar_risk']:.2f} | {b['entry_zone']} | {b['stop_loss']} | {b['take_profit_1']} | {b['risk_reward_ratio']} |\n"
            )

        return rf"""# 🏛 CAPITAL ADVISOR — ${data['capital_analyzed']:.0f} LIVE EXECUTION BLUEPRINT
*Auto-updated every hour by the Execution & Risk Desk (`hourly_execution_desk.yml`)*  
*Last Refresh: **{now}***

---

## 📊 1. 30-DAY CALIBRATION & VAULT SCOREBOARD
- **Readiness State**: `{r['status']}`
- **Simulation Epoch**: `#{r['simulation_epochs']}`
- **Resolved Sample**: `{r['total_trades']}` trades (`{r.get('wins_count', 0)}W / {r.get('losses_count', 0)}L`)
- **Rolling Win Rate**: **{r['win_rate']*100:.1f}%**
- **Profit Factor**: **{r['profit_factor']:.2f}** (Target: $\ge 1.50$)
- **Max Drawdown**: **{r['max_drawdown_percent']:.1f}%** (Target: $\le 10.0\%$)
- **Active Bankroll**: **${r['active_bankroll']:.2f}**
- **Locked Profit Vault ("Profit Brought Home")**: **${r['total_vaulted_profit']:.2f}**

---

## 🎯 2. #1 RECOMMENDED INSTRUMENT
- **Instrument**: **`{data['recommended_instrument']}`** *(Ranked #1 for ${data['capital_analyzed']:.0f} capital)*
- **Directional Bias**: **{data['direction']}**
- **Market Regime**: `{data['market_regime']}`
- **Primary Strategy**: `{data['primary_strategy']}`
- **Champion Rule Alignment**: *{data['champions_rule'] or 'PrimeFX Pure Price-Action + Liquidity Playbook'}*

---

## 🎫 3. EXACT EXECUTION TICKET
| Parameter | Exact Value | Notes |
| :--- | :--- | :--- |
| **Recommended Lot Size** | **`{data['recommended_lot_size']} lots`** | Granular nano-lot execution |
| **Dollar Risk ($)** | **`${data['dollar_risk']:.2f}`** | **{data['risk_percent']}%** of ${data['capital_analyzed']:.0f} equity |
| **Required Margin** | `${data['margin_required']:.2f}` | Leverage 1:1000 |
| **Entry Zone** | **`{data['entry_zone']}`** | Structural ATR-calibrated entry band |
| **Stop Loss (SL)** | **`{data['stop_loss']}`** | {data['sl_points']} points distance |
| **Take Profit 1 (TP1)** | **`{data['take_profit_1']}`** | Planned R:R: **{data['risk_reward_ratio']}** |
| **Take Profit 2 (TP2)** | **`{data['take_profit_2']}`** | Major liquidity pool |
| **Cover-Risk-to-Zero** | **`{data['break_even_trigger']}`** | Ratchet SL to Break-Even at $+1.0R$ |
| **Estimated Hold Time** | **{data['hold_time_estimate']}** | Based on $\text{{ATR}}_{{14}}$ velocity |

---

## 🔍 4. CONFLUENCE SIGNALS
{reasons_md}

---

## ⚠️ 5. GEMINI COUNCIL WARNING / EDGE NOTE
> *{data['council_edge_note']}*
{backup_md}
{disqualified_md}
"""
