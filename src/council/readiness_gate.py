"""
Readiness Gate & 30-Day Graduation Evaluator.
Tracks Day X / 30, resolved paper trades, Win Rate, Profit Factor,
and Max Drawdown to govern the transition from TRAINING_CALIBRATION to LIVE_READY.
"""

from datetime import datetime, timezone
from typing import Dict, Any
from src.database.db_manager import DatabaseManager


class ReadinessGate:
    MIN_CALIBRATION_DAYS = 30
    MIN_RESOLVED_TRADES = 30
    MIN_PROFIT_FACTOR = 1.50
    MAX_ALLOWABLE_DRAWDOWN = 10.0 # 10% max DD

    def __init__(self, db: DatabaseManager):
        self.db = db

    def evaluate_metrics(self) -> Dict[str, Any]:
        """
        Computes system-wide calibration performance and readiness status.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()

            # Fetch all closed trades
            cursor.execute("""
            SELECT realized_pnl, realized_r, status, created_at, closed_at
            FROM paper_trades
            WHERE status IN ('CLOSED_TP', 'CLOSED_SL')
            ORDER BY closed_at ASC
            """)
            trades = [dict(r) for r in cursor.fetchall()]

            # Fetch simulation start date
            cursor.execute("SELECT MIN(created_at) as first_trade FROM paper_trades")
            first_row = cursor.fetchone()
            first_trade_time = first_row["first_trade"] if first_row and first_row["first_trade"] else None

            # Fetch current bankroll and vault
            cursor.execute("SELECT value FROM system_kv WHERE key = 'active_bankroll'")
            r_bank = cursor.fetchone()
            active_bankroll = float(r_bank["value"]) if r_bank else 100.0

            cursor.execute("SELECT value FROM system_kv WHERE key = 'vault_balance'")
            r_vault = cursor.fetchone()
            vault_balance = float(r_vault["value"]) if r_vault else 0.0

            cursor.execute("SELECT MAX(epoch_number) as max_epoch FROM simulation_epochs")
            r_epoch = cursor.fetchone()
            total_epochs = int(r_epoch["max_epoch"]) if r_epoch and r_epoch["max_epoch"] else 1

        total_trades = len(trades)
        if total_trades == 0:
            return {
                "day_number": 1,
                "days_remaining": 29,
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_percent": 0.0,
                "total_vaulted_profit": vault_balance,
                "active_bankroll": active_bankroll,
                "simulation_epochs": total_epochs,
                "status": "TRAINING_CALIBRATION",
                "is_live_ready": False,
                "graduation_blockers": ["0/30 resolved trades", "0/30 calibration days"]
            }

        wins = [t for t in trades if t["realized_pnl"] > 0]
        losses = [t for t in trades if t["realized_pnl"] <= 0]

        win_rate = round(len(wins) / total_trades, 3)

        gross_profit = sum(t["realized_pnl"] for t in wins)
        gross_loss = abs(sum(t["realized_pnl"] for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 1.0)

        # Max Drawdown calculation across closed trades
        equity = 100.0
        peak = 100.0
        max_dd_pct = 0.0

        for t in trades:
            equity += t["realized_pnl"]
            if equity > peak:
                peak = equity
            dd = ((peak - equity) / peak) * 100.0 if peak > 0 else 0.0
            if dd > max_dd_pct:
                max_dd_pct = dd

        # Compute days elapsed
        days_elapsed = 1
        if first_trade_time:
            try:
                first_dt = datetime.fromisoformat(first_trade_time)
                days_elapsed = max(1, (datetime.now(timezone.utc) - first_dt.replace(tzinfo=timezone.utc if first_dt.tzinfo is None else first_dt.tzinfo)).days + 1)
            except Exception:
                days_elapsed = 1

        blockers = []
        if days_elapsed < self.MIN_CALIBRATION_DAYS:
            blockers.append(f"Day {days_elapsed}/{self.MIN_CALIBRATION_DAYS} calibration window")
        if total_trades < self.MIN_RESOLVED_TRADES:
            blockers.append(f"{total_trades}/{self.MIN_RESOLVED_TRADES} minimum trades")
        if profit_factor < self.MIN_PROFIT_FACTOR:
            blockers.append(f"Profit factor {profit_factor:.2f} < {self.MIN_PROFIT_FACTOR:.2f}")
        if max_dd_pct > self.MAX_ALLOWABLE_DRAWDOWN:
            blockers.append(f"Max DD {max_dd_pct:.1f}% > {self.MAX_ALLOWABLE_DRAWDOWN:.1f}%")

        is_live_ready = len(blockers) == 0
        status = "LIVE_READY" if is_live_ready else f"TRAINING_CALIBRATION (Day {days_elapsed}/30)"

        return {
            "day_number": days_elapsed,
            "days_remaining": max(0, self.MIN_CALIBRATION_DAYS - days_elapsed),
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_percent": round(max_dd_pct, 2),
            "total_vaulted_profit": vault_balance,
            "active_bankroll": active_bankroll,
            "simulation_epochs": total_epochs,
            "status": status,
            "is_live_ready": is_live_ready,
            "graduation_blockers": blockers
        }
