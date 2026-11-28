"""
Structured Markdown Table Status Engine (`python main.py --cycle status`).
Reads live from SQLite (`data/trading_state.db`), config JSONs, reports, and GitHub Actions
to emit 5 compact Markdown tables without verbose prose walls.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.council.readiness_gate import ReadinessGate


class StatusTableReporter:
    """Renders 5 compact Markdown tables capturing full autonomous bot telemetry."""

    REPO_SLUG = "manavmate53-jpg/Money"
    WORKFLOW_NAMES = (
        "1. Execution & Risk Desk (Hourly)",
        "2. Macro & Regime Desk (4-Hour)",
        "3. Investment Council & Vault (Daily)",
        "4. On-Demand Capital Advisor (Manual)",
    )

    def __init__(
        self,
        db_path: str = "data/trading_state.db",
        macro_bias_path: str = "config/macro_bias.json",
        active_filters_path: str = "config/active_filters.json",
        latest_advice_path: str = "reports/LATEST_ADVICE.md",
        dashboard_path: str = "reports/TRAINING_DASHBOARD.md",
        policy_path: str = "config/bankroll_policy.json",
        fetch_remote_ci: bool = True,
    ):
        self.db_path = db_path
        self.macro_bias_path = macro_bias_path
        self.active_filters_path = active_filters_path
        self.latest_advice_path = latest_advice_path
        self.dashboard_path = dashboard_path
        self.policy_path = policy_path
        self.fetch_remote_ci = fetch_remote_ci

        self.db = DatabaseManager(db_path)
        self.bankroll = BankrollManager(self.db, policy_path=policy_path)
        self.gate = ReadinessGate(self.db, dashboard_path=dashboard_path)

    @staticmethod
    def _clean_cell(val: Any, max_len: int = 96) -> str:
        text = str(val if val is not None else "-").replace("|", "/").replace("\n", " ").strip()
        if not text:
            return "-"
        return f"{text[:max_len - 3]}..." if len(text) > max_len else text

    @staticmethod
    def _fmt_price(val: Any) -> str:
        if val is None or val == "":
            return "-"
        try:
            f = float(val)
            return f"{f:.2f}" if abs(f) >= 10 else f"{f:.4f}"
        except Exception:
            return str(val)

    @staticmethod
    def _fmt_table(title: str, headers: List[str], rows: List[List[Any]]) -> str:
        lines = [
            f"### {title}",
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join([":---"] * len(headers)) + " |",
        ]
        for row in rows:
            cells = [StatusTableReporter._clean_cell(c) for c in row]
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines)

    @staticmethod
    def _parse_iso_dt(ts: Optional[str]) -> Optional[datetime]:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _load_json(self, path: str) -> Dict[str, Any]:
        if path and os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _build_table_1_scoreboard(self) -> str:
        b_stat = self.bankroll.get_status()
        r_stat = self.gate.evaluate_metrics(write_report=False)
        epoch_info = self.db.get_active_epoch_info()
        analogs_cnt = len(self.db.get_pattern_analogs())

        epoch_start_dt = self._parse_iso_dt(epoch_info.get("start_time"))
        epoch_start_str = epoch_start_dt.strftime("%Y-%m-%d %H:%M UTC") if epoch_start_dt else "N/A"
        breaker_active = bool(b_stat.get("drawdown_breaker_active", False))

        active_bankroll = float(b_stat.get("active_bankroll", 100.0))
        equity_floor = float(b_stat.get("hard_equity_floor", 60.0))
        pct_vs_100 = active_bankroll - 100.0
        floor_buffer = max(0.0, active_bankroll - equity_floor)
        vault_bal = float(b_stat.get("vault_balance", 0.0))

        resolved = self.db.get_resolved_trades(epoch_id=int(b_stat["epoch_number"]))
        r_vals = [float(t["rr_realized"]) for t in resolved if t.get("rr_realized") is not None]
        avg_r = sum(r_vals) / len(r_vals) if r_vals else 0.0

        day_num = r_stat.get("day_number", 1)
        gate_label = "LIVE_READY" if r_stat.get("is_live_ready") else "TRAINING_CALIBRATION"
        wins_n = int(r_stat.get("wins_count", 0))
        losses_n = int(r_stat.get("losses_count", 0))
        total_n = int(r_stat.get("total_trades", 0))
        wr_pct = float(r_stat.get("win_rate", 0.0)) * 100.0
        pf_val = float(r_stat.get("profit_factor", 0.0))
        dd_pct = float(r_stat.get("max_drawdown_percent", 0.0))
        mc_prob = float(r_stat.get("monte_carlo_pass_probability", 0.0)) * 100.0
        mc_dd = float(r_stat.get("monte_carlo_p95_dd_r", 0.0))

        rows = [
            ["Simulation Epoch", f"#{b_stat['epoch_number']} (since {epoch_start_str})", "Epoch >= 1", "PAUSED (BREAKER)" if breaker_active else "ACTIVE"],
            ["Calibration Window", f"Day {day_num} / 30 ({gate_label})", "30 Days / LIVE_READY", "✅ PASSED" if r_stat.get("is_live_ready") else "⏳ IN PROGRESS"],
            ["Active Trading Bankroll", f"${active_bankroll:.2f} ({pct_vs_100:+.2f}% vs $100)", f">= ${equity_floor:.2f} Floor", "🛑 BREAKER" if active_bankroll <= equity_floor else "✅ HEALTHY"],
            ["Locked Profit Vault", f"${vault_bal:.2f}", "50% sweep per +20% gain", "🏦 LOCKED"],
            ["Drawdown & $60 Floor Buffer", f"{dd_pct:.1f}% DD | ${floor_buffer:.2f} buffer", "<= 10.0% DD | $60.00 Floor", "🛑 BREAKER" if floor_buffer <= 0 else ("✅ PASSED" if dd_pct <= 10.0 else "⚠️ ELEVATED")],
            ["Resolved Trades (W / L)", f"{total_n} ({wins_n}W / {losses_n}L | {wr_pct:.1f}%)", ">= 30 Trades / > 50% WR", "✅ PASSED" if total_n >= 30 and wr_pct > 50 else "⏳ ACCUMULATING"],
            ["Profit Factor & Expectancy", f"PF {pf_val:.2f} | Avg {avg_r:+.2f}R", ">= 1.50 PF | > +0.15R", "✅ PASSED" if pf_val >= 1.50 else "❌ BELOW TARGET"],
            ["Monte Carlo Pass Prob", f"{mc_prob:.1f}% | P95 DD {mc_dd:.1f}R", ">= 65.0% | <= 10.0R", "✅ PASSED" if mc_prob >= 65.0 else ("⏳ UNRATED" if total_n == 0 else "⚠️ AT RISK")],
            ["k-NN Pattern Memory", f"{analogs_cnt:,} analogs", ">= 20 nearest (WR >= 42%)", "✅ ACTIVE" if analogs_cnt >= 20 else "⚠️ UNSEEDED"],
        ]
        return self._fmt_table(
            "1. Bankroll, Calibration & Risk Scoreboard",
            ["Metric", "Live Value", "Target / Limit", "Status"],
            rows,
        )

    def _build_table_2_orders(self) -> str:
        now_utc = datetime.now(timezone.utc)
        active = self.db.get_active_trades()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM paper_trades ORDER BY trade_id DESC LIMIT 5")
            recent_paper = [dict(r) for r in cursor.fetchall()]

        # Match active_trades trade_id by (symbol, opened_at[:19])
        active_by_key = {}
        for a in active:
            key = (a.get("symbol", ""), str(a.get("opened_at", ""))[:19])
            active_by_key[key] = a

        rows: List[List[Any]] = []
        seen_keys = set()

        for a in active:
            key = (a.get("symbol", ""), str(a.get("opened_at", ""))[:19])
            seen_keys.add(key)
            opened_dt = self._parse_iso_dt(a.get("opened_at"))
            age_h = (now_utc - opened_dt).total_seconds() / 3600.0 if opened_dt else 0.0
            e_zone = f"{self._fmt_price(a.get('entry_min'))}–{self._fmt_price(a.get('entry_max'))}"
            rows.append([
                a.get("trade_id", "-"),
                a.get("symbol", "-"),
                a.get("direction", "-"),
                a.get("status", "-"),
                a.get("lot_size", "-"),
                f"${float(a.get('dollar_risk', 0.0)):.2f}",
                e_zone,
                self._fmt_price(a.get("sl_price")),
                self._fmt_price(a.get("tp_price")),
                f"{age_h:.1f}h open",
            ])

        for p in recent_paper:
            key = (p.get("symbol", ""), str(p.get("created_at", ""))[:19])
            if key in seen_keys:
                continue
            seen_keys.add(key)

            status = str(p.get("status", "-"))
            created_dt = self._parse_iso_dt(p.get("created_at"))
            closed_dt = self._parse_iso_dt(p.get("closed_at"))
            if closed_dt and created_dt:
                dur_h = max(0.0, (closed_dt - created_dt).total_seconds() / 3600.0)
            elif created_dt:
                dur_h = max(0.0, (now_utc - created_dt).total_seconds() / 3600.0)
            else:
                dur_h = 0.0

            if status == "EXPIRED_STALE":
                outcome_str = f"EXPIRED ({dur_h:.1f}h)"
            elif p.get("realized_r") is not None and p.get("realized_pnl") is not None:
                outcome_str = f"{float(p['realized_r']):+.2f}R (${float(p['realized_pnl']):+.2f})"
            else:
                outcome_str = f"{dur_h:.1f}h ({status})"

            stamp = created_dt.strftime("%Y%m%d%H%M%S") if created_dt else str(p.get("trade_id", ""))
            tid_label = f"ORD-#{p['trade_id']} ({stamp[:8]})"
            e_zone = f"{self._fmt_price(p.get('entry_min'))}–{self._fmt_price(p.get('entry_max'))}"
            rows.append([
                tid_label,
                p.get("symbol", "-"),
                p.get("direction", "-"),
                status,
                p.get("lot_size", "-"),
                f"${float(p.get('dollar_risk', 0.0)):.2f}",
                e_zone,
                self._fmt_price(p.get("stop_loss")),
                self._fmt_price(p.get("take_profit_1")),
                outcome_str,
            ])

        if not rows:
            rows.append(["None", "-", "-", "IDLE", "-", "$0.00", "-", "-", "-", "No orders recorded"])

        return self._fmt_table(
            "2. Active & Recent Orders",
            ["Trade ID", "Symbol", "Bias", "Status", "Lot", "Risk ($)", "Entry Zone", "SL", "TP1", "Age / Outcome"],
            rows,
        )

    def _build_table_3_closed_ledgers(self) -> str:
        wins = self.db.get_profitable_trades()
        losses = self.db.get_losing_trades()
        rows: List[List[Any]] = []

        if not wins:
            rows.append(["None yet", "PROFIT", "-", "-", "-", "-", "-", "-", "-", "-", "-", "Awaiting first closed winner"])
        else:
            for w in wins:
                rows.append([
                    w.get("trade_id", "-"),
                    "PROFIT",
                    w.get("symbol", "-"),
                    w.get("direction", "-"),
                    w.get("strategy_framework", "-"),
                    w.get("lot_size", "-"),
                    f"${float(w.get('total_fund', 0.0)):.2f}",
                    f"${float(w.get('used_fund', 0.0)):.2f}",
                    f"+${float(w.get('net_pnl', 0.0)):.2f} / {float(w.get('rr_realized', 0.0)):+.2f}R",
                    f"${float(w.get('after_trade_fund', 0.0)):.2f}",
                    f"{float(w.get('hold_duration_hours', 0.0)):.1f}h",
                    self._clean_cell(w.get("what_improved", "-"), max_len=75),
                ])

        for l in losses:
            pnl = float(l.get("net_pnl", 0.0))
            rows.append([
                l.get("trade_id", "-"),
                "LOSS",
                l.get("symbol", "-"),
                l.get("direction", "-"),
                l.get("strategy_framework", "-"),
                l.get("lot_size", "-"),
                f"${float(l.get('total_fund', 0.0)):.2f}",
                f"${float(l.get('used_fund', 0.0)):.2f}",
                f"-${abs(pnl):.2f} / {float(l.get('rr_realized', 0.0)):+.2f}R",
                f"${float(l.get('after_trade_fund', 0.0)):.2f}",
                f"{float(l.get('hold_duration_hours', 0.0)):.1f}h",
                self._clean_cell(l.get("what_improved", "-"), max_len=75),
            ])

        return self._fmt_table(
            "3. Closed Trade Ledgers",
            [
                "Trade ID", "Ledger", "Symbol", "Bias", "Framework", "Lot",
                "Pre-Fund", "Used Fund", "Net PnL ($ / R)", "Post-Fund",
                "Hold (h)", "Council Lesson / Filter Update",
            ],
            rows,
        )

    def _build_table_4_macro(self) -> str:
        macro_json = self._load_json(self.macro_bias_path)
        instruments = macro_json.get("instruments", {})
        rows: List[List[Any]] = []

        for sym, info in instruments.items():
            conf = float(info.get("confidence_score", 0.0)) * 100.0
            viable = "✅ YES" if info.get("nano_lot_viable", False) else "❌ NO"
            rows.append([
                sym,
                info.get("macro_bias", "NEUTRAL"),
                f"{conf:.0f}%",
                info.get("primary_regime", "-"),
                viable,
                self._fmt_price(info.get("key_invalidation_level")),
                self._fmt_price(info.get("macro_target")),
            ])

        if not rows:
            rows.append(["NONE", "NEUTRAL", "0%", "-", "❌ NO", "-", "-"])

        return self._fmt_table(
            "4. 4H Macro Regime & $100 Account Viability",
            ["Symbol", "H4 Bias", "Conf.", "Regime", "$100 Nano-Lot Viable", "Invalidation", "Target"],
            rows,
        )

    def _parse_latest_advice_row(self) -> List[Any]:
        if not os.path.exists(self.latest_advice_path):
            return ["NONE", "NEUTRAL", "-", "-", "-", "-", "-", "-", "-", "-"]
        try:
            with open(self.latest_advice_path, "r") as f:
                txt = f.read()
        except Exception:
            return ["NONE", "NEUTRAL", "-", "-", "-", "-", "-", "-", "-", "-"]

        def grab(pattern: str, default: str = "-") -> str:
            m = re.search(pattern, txt)
            return m.group(1).strip() if m else default

        sym = grab(r"\*\*Instrument\*\*:\s*\*?\*?`([^`]+)`")
        bias = grab(r"\*\*Directional Bias\*\*:\s*\*\*([^*]+)\*\*")
        strat = grab(r"\*\*Primary Strategy\*\*:\s*`([^`]+)`")
        lot = grab(r"\|\s*\*\*Recommended Lot Size\*\*\s*\|\s*\*?\*?`([^`]+)`")
        risk = grab(r"\|\s*\*\*Dollar Risk \(\$\)\*\*\s*\|\s*\*?\*?`([^`]+)`")
        risk_pct = grab(r"\|\s*\*\*Dollar Risk \(\$\)\*\*[^|]*\|[^|]*\|\s*\*\*([^*]+)\*\*")
        entry = grab(r"\|\s*\*\*Entry Zone\*\*\s*\|\s*\*?\*?`([^`]+)`")
        sl = grab(r"\|\s*\*\*Stop Loss \(SL\)\*\*\s*\|\s*\*?\*?`([^`]+)`")
        tp1 = grab(r"\|\s*\*\*Take Profit 1 \(TP1\)\*\*\s*\|\s*\*?\*?`([^`]+)`")
        be = grab(r"\|\s*\*\*Cover-Risk-to-Zero\*\*\s*\|\s*\*?\*?`([^`]+)`")
        hold = grab(r"\|\s*\*\*Estimated Hold Time\*\*\s*\|\s*\*\*([^*]+)\*\*")

        risk_combined = f"{risk} ({risk_pct})" if risk_pct != "-" else risk
        return [sym, bias, strat, lot, risk_combined, entry, sl, tp1, be, hold]

    def _collect_github_actions_rows(self) -> List[List[Any]]:
        runs: List[Dict[str, Any]] = []

        # 1. Try gh CLI first (fast local subprocess with short timeout)
        try:
            proc = subprocess.run(
                ["gh", "run", "list", "--limit", "10", "--json", "workflowName,createdAt,conclusion,headSha"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                parsed = json.loads(proc.stdout)
                if isinstance(parsed, list):
                    runs = parsed
        except Exception:
            runs = []

        # 2. Fallback to GitHub REST API if permitted and online
        if not runs and self.fetch_remote_ci:
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{self.REPO_SLUG}/actions/runs?per_page=10",
                    headers={"User-Agent": "Money-Bot-Status/1.0"},
                )
                with urllib.request.urlopen(req, timeout=3) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                for r in payload.get("workflow_runs", []):
                    runs.append({
                        "workflowName": r.get("name", ""),
                        "createdAt": r.get("created_at", ""),
                        "conclusion": r.get("conclusion") or r.get("status") or "running",
                        "headSha": r.get("head_sha", ""),
                    })
            except Exception:
                runs = []

        # 3. Offline fallback via git commit messages
        if not runs:
            try:
                proc = subprocess.run(
                    ["git", "log", "-n", "12", "--format=%H|%cI|%s"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if proc.returncode == 0:
                    msg_map = {
                        "hourly execution cycle": "1. Execution & Risk Desk (Hourly)",
                        "4-hour macro regime update": "2. Macro & Regime Desk (4-Hour)",
                        "daily investment council": "3. Investment Council & Vault (Daily)",
                    }
                    for line in proc.stdout.strip().splitlines():
                        parts = line.split("|", 2)
                        if len(parts) != 3:
                            continue
                        sha, ts, subj = parts
                        low = subj.lower()
                        for needle, wf_name in msg_map.items():
                            if needle in low:
                                runs.append({
                                    "workflowName": wf_name,
                                    "createdAt": ts,
                                    "conclusion": "git_commit",
                                    "headSha": sha,
                                })
                                break
            except Exception:
                pass

        latest_by_wf: Dict[str, Dict[str, Any]] = {}
        for r in runs:
            wf = r.get("workflowName", "")
            if wf and wf not in latest_by_wf:
                latest_by_wf[wf] = r

        rows: List[List[Any]] = []
        for wf_name in self.WORKFLOW_NAMES:
            match = latest_by_wf.get(wf_name)
            if match:
                dt = self._parse_iso_dt(match.get("createdAt"))
                ts_str = dt.strftime("%Y-%m-%d %H:%M") if dt else str(match.get("createdAt", "-"))
                concl = str(match.get("conclusion", "-")).upper()
                sha = str(match.get("headSha", "-"))[:7]
                rows.append([wf_name, ts_str, concl, sha])
            else:
                rows.append([wf_name, "N/A", "MANUAL / NO_RUN", "-"])
        return rows

    def _build_table_5_advice_and_ci(self) -> str:
        advice_row = self._parse_latest_advice_row()
        t5a = self._fmt_table(
            "5A. Latest $100 Setup",
            [
                "Rank #1 Symbol", "Bias", "Strategy", "Lot Size", "Risk ($ / %)",
                "Entry Zone", "SL", "TP1 (1:2.5R)", "Break-Even (+1R)", "Est. Hold",
            ],
            [advice_row],
        )
        ci_rows = self._collect_github_actions_rows()
        t5b = self._fmt_table(
            "5B. GitHub Actions Desks",
            ["Workflow Desk", "Last Run (UTC)", "Conclusion", "Commit"],
            ci_rows,
        )
        return f"### 5. Latest $100 Capital Advice & GitHub Actions Health\n\n{t5a}\n\n{t5b}"

    def render(self) -> str:
        """Builds the complete 5-table Markdown status report."""
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        sections = [
            f"## Autonomous Deriv Bot Live Status ({now_str})",
            self._build_table_1_scoreboard(),
            self._build_table_2_orders(),
            self._build_table_3_closed_ledgers(),
            self._build_table_4_macro(),
            self._build_table_5_advice_and_ci(),
        ]
        return "\n\n".join(sections)


def render_status_tables(fetch_remote_ci: bool = True, **kwargs: Any) -> str:
    """Convenience helper returning the 5 Markdown status tables."""
    reporter = StatusTableReporter(fetch_remote_ci=fetch_remote_ci, **kwargs)
    return reporter.render()
