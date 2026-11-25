"""
Readiness Gate & 30-Day Graduation Evaluator.
Reads resolved trades strictly from get_resolved_trades() (profitable_trades UNION ALL losing_trades),
tracks accurate Epoch start days, evaluates institutional quant gates, and always
regenerates reports/TRAINING_DASHBOARD.md cleanly.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional
from src.database.db_manager import DatabaseManager


class ReadinessGate:
    MIN_CALIBRATION_DAYS = 30
    MIN_RESOLVED_TRADES = 30
    MIN_PROFIT_FACTOR = 1.50
    MAX_ALLOWABLE_DRAWDOWN = 10.0  # 10% max DD

    def __init__(self, db: DatabaseManager, dashboard_path: str = "reports/TRAINING_DASHBOARD.md"):
        self.db = db
        self.dashboard_path = dashboard_path

    def _compute_days_elapsed(self, epoch_start_time: Optional[str], first_trade_time: Optional[str]) -> int:
        ref_time = epoch_start_time or first_trade_time
        if not ref_time:
            return 1
        try:
            start_dt = datetime.fromisoformat(ref_time)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            return max(1, (datetime.now(timezone.utc) - start_dt).days + 1)
        except Exception:
            return 1

    def evaluate_metrics(self, dashboard_path: Optional[str] = None, write_report: bool = True) -> Dict[str, Any]:
        """
        Computes system-wide calibration performance exclusively from profitable_trades & losing_trades
        for the active epoch and updates TRAINING_DASHBOARD.md.
        """
        target_path = dashboard_path or self.dashboard_path
        epoch_info = self.db.get_active_epoch_info()
        current_epoch = int(epoch_info.get("epoch_number", 1))
        epoch_start_time = epoch_info.get("start_time")
        starting_capital = float(epoch_info.get("starting_balance", 100.0) or 100.0)

        trades = self.db.get_resolved_trades(epoch_id=current_epoch)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_kv WHERE key = 'active_bankroll'")
            r_bank = cursor.fetchone()
            active_bankroll = float(r_bank["value"]) if r_bank else starting_capital

            cursor.execute("SELECT value FROM system_kv WHERE key = 'vault_balance'")
            r_vault = cursor.fetchone()
            vault_balance = float(r_vault["value"]) if r_vault else 0.0

        first_trade_time = trades[0].get("opened_at") if trades else None
        days_elapsed = self._compute_days_elapsed(epoch_start_time, first_trade_time)
        total_trades = len(trades)

        if total_trades == 0:
            res = {
                "day_number": days_elapsed,
                "days_remaining": max(0, self.MIN_CALIBRATION_DAYS - days_elapsed),
                "total_trades": 0,
                "wins_count": 0,
                "losses_count": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_percent": 0.0,
                "total_vaulted_profit": vault_balance,
                "active_bankroll": active_bankroll,
                "simulation_epochs": current_epoch,
                "net_real_return": round(vault_balance, 2),
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "monte_carlo_p95_dd_r": 0.0,
                "monte_carlo_pass_probability": 0.0,
                "status": f"TRAINING_CALIBRATION (Day {days_elapsed}/30)",
                "is_live_ready": False,
                "graduation_blockers": [
                    f"Day {days_elapsed}/{self.MIN_CALIBRATION_DAYS} calibration window",
                    f"0/{self.MIN_RESOLVED_TRADES} minimum resolved trades"
                ]
            }
            if write_report:
                self.update_dashboard_markdown(res, path=target_path)
            return res

        wins = [t for t in trades if float(t.get("net_pnl", 0.0)) > 0]
        losses = [t for t in trades if float(t.get("net_pnl", 0.0)) <= 0]

        win_rate = round(len(wins) / total_trades, 3)
        gross_profit = sum(float(t.get("net_pnl", 0.0)) for t in wins)
        gross_loss = abs(sum(float(t.get("net_pnl", 0.0)) for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        equity = starting_capital
        peak = starting_capital
        max_dd_pct = 0.0
        for t in trades:
            equity += float(t.get("net_pnl", 0.0))
            if equity > peak:
                peak = equity
            dd = ((peak - equity) / peak) * 100.0 if peak > 0 else 0.0
            if dd > max_dd_pct:
                max_dd_pct = dd

        from src.risk.quant_engine import QuantEngine
        quant = QuantEngine()

        r_multiples = [float(t["rr_realized"]) for t in trades if t.get("rr_realized") is not None]
        pnl_series = [float(t.get("net_pnl", 0.0)) for t in trades]
        mc_sim = quant.run_monte_carlo_permutation_sim(r_multiples, num_simulations=1000)
        sharpe = quant.calculate_sharpe_ratio(pnl_series)
        sortino = quant.calculate_sortino_ratio(pnl_series)
        net_real_return = quant.compute_net_real_return(vault_balance, max(0, current_epoch - 1), starting_capital=starting_capital)

        blockers = []
        if days_elapsed < self.MIN_CALIBRATION_DAYS:
            blockers.append(f"Day {days_elapsed}/{self.MIN_CALIBRATION_DAYS} calibration window")
        if total_trades < self.MIN_RESOLVED_TRADES:
            blockers.append(f"{total_trades}/{self.MIN_RESOLVED_TRADES} minimum trades")
        if profit_factor < self.MIN_PROFIT_FACTOR:
            blockers.append(f"Profit factor {profit_factor:.2f} < {self.MIN_PROFIT_FACTOR:.2f}")
        if max_dd_pct > self.MAX_ALLOWABLE_DRAWDOWN:
            blockers.append(f"Max DD {max_dd_pct:.1f}% > {self.MAX_ALLOWABLE_DRAWDOWN:.1f}%")
        if mc_sim["probability_hit_target_first"] < 0.65:
            blockers.append(f"Monte Carlo Target Pass Rate {mc_sim['probability_hit_target_first']*100:.1f}% < 65.0%")

        is_live_ready = len(blockers) == 0
        status = "LIVE_READY" if is_live_ready else f"TRAINING_CALIBRATION (Day {days_elapsed}/30)"

        res = {
            "day_number": days_elapsed,
            "days_remaining": max(0, self.MIN_CALIBRATION_DAYS - days_elapsed),
            "total_trades": total_trades,
            "wins_count": len(wins),
            "losses_count": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_percent": round(max_dd_pct, 2),
            "total_vaulted_profit": vault_balance,
            "active_bankroll": active_bankroll,
            "simulation_epochs": current_epoch,
            "net_real_return": net_real_return,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "monte_carlo_p95_dd_r": mc_sim["p95_worst_drawdown_r"],
            "monte_carlo_pass_probability": mc_sim["probability_hit_target_first"],
            "status": status,
            "is_live_ready": is_live_ready,
            "graduation_blockers": blockers
        }
        if write_report:
            self.update_dashboard_markdown(res, path=target_path)
        return res

    def update_dashboard_markdown(self, m: Dict[str, Any], path: Optional[str] = None):
        import os
        target_path = path or self.dashboard_path
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        blockers_str = "\n".join(f"- ⏳ {b}" for b in m["graduation_blockers"]) if m["graduation_blockers"] else "✅ All criteria fulfilled for live deployment!"

        content = rf"""# 📈 30-DAY CALIBRATION SCOREBOARD & VAULT TRACKER
*Auto-updated daily by the Investment Council Committee (`daily_council_committee.yml`)*  
*Last Refresh: **{now}***

---

## 🎯 1. GRADUATION GATE STATUS: `{m['status']}`
| Metric | Current Value | Required Live Gate | Status |
| :--- | :--- | :--- | :--- |
| **Calibration Window** | Day **{m['day_number']}** / 30 | 30 full calendar days | {'✅ PASSED' if m['day_number'] >= 30 else '⏳ IN PROGRESS'} |
| **Resolved Sample** | **{m['total_trades']}** trades (`{m.get('wins_count', 0)}W / {m.get('losses_count', 0)}L`) | $\ge 30$ resolved trades | {'✅ PASSED' if m['total_trades'] >= 30 else '⏳ IN PROGRESS'} |
| **Profit Factor** | **{m['profit_factor']:.2f}** | $\ge 1.50$ | {'✅ PASSED' if m['profit_factor'] >= 1.50 else '❌ BELOW THRESHOLD'} |
| **Max Drawdown** | **{m['max_drawdown_percent']:.1f}%** | $\le 10.0\%$ | {'✅ PASSED' if m['max_drawdown_percent'] <= 10.0 else '❌ EXCEEDS CAP'} |
| **Rolling Win Rate** | **{m['win_rate']*100:.1f}%** | Baseline $> 50.0\%$ | {'✅ HEALTHY' if m['win_rate'] >= 0.50 else '⚠️ MONITORING'} |
| **Monte Carlo Pass Prob** | **{m.get('monte_carlo_pass_probability', 0.0)*100:.1f}%** | $\ge 65.0\%$ (+20% before -15%) | {'✅ PASSED' if m.get('monte_carlo_pass_probability', 0.0) >= 0.65 else '❌ AT RISK'} |
| **Monte Carlo P95 Worst DD** | **{m.get('monte_carlo_p95_dd_r', 0.0):.1f}R** | $\le 10.0R$ | {'✅ RESILIENT' if m.get('monte_carlo_p95_dd_r', 0.0) <= 10.0 else '⚠️ ELEVATED'} |
| **Sharpe / Sortino Ratio** | **{m.get('sharpe_ratio', 0.0):.2f} / {m.get('sortino_ratio', 0.0):.2f}** | Sharpe $\ge 1.5$ / Sortino $\ge 2.0$ | {'✅ OPTIMAL' if m.get('sharpe_ratio', 0.0) >= 1.5 else '⏳ ACCUMULATING'} |
| **Net Real Return** | **${m.get('net_real_return', 0.0):+.2f}** | Payouts minus Resets | {'🟢 PROFITABLE' if m.get('net_real_return', 0.0) >= 0 else '🔴 DRAWDOWN'} |

### Remaining Graduation Gate Blockers:
{blockers_str}

---

## 🏦 2. CAPITAL & PROFIT-LOCK VAULT SCOREBOARD
- **Active Trading Bankroll**: **`${m['active_bankroll']:.2f}`**
- **Locked Profit Vault ("Profit Brought Home")**: **`${m['total_vaulted_profit']:.2f}`**
- **Total Combined Capital**: **`${m['active_bankroll'] + m['total_vaulted_profit']:.2f}`**
- **Current Simulation Epoch**: `#{m['simulation_epochs']}` (Auto-reset on $\le \$60.00$ equity / $\$40$ Max-Drawdown Shield)

---

## 🛡️ 3. INSTITUTIONAL RISK CONTROLS ACTIVE
- **Maximum Risk per Trade**: 1.5% (hard cap 2.0%)
- **Cover Risk to Zero**: Move SL to Break-Even at $+1.0R$ (Fabio Valentini)
- **Session Walk-Away**: Stop new entries upon $+3.0R$ realized gain (Freddy Sarento)
- **Small-Account Protection**: Automatically filters out unviable high-min-lot indices on $\$100$ balances to preserve capital
"""
        with open(target_path, "w") as f:
            f.write(content)
