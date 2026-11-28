"""
Bankroll Manager & Two-Bucket Profit-Lock Vault ("Profit Comes Home With Us").
Manages 30-day simulation auto-resets, profit sweeping, and anti-greed protections.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from src.database.db_manager import DatabaseManager


ASSET_BUCKETS: Dict[str, str] = {
    "JD10": "JUMP_FAMILY",
    "JD25": "JUMP_FAMILY",
    "JD50": "JUMP_FAMILY",
    "JD75": "JUMP_FAMILY",
    "JD100": "JUMP_FAMILY",
    "1HZ10V": "VOL_1S_FAMILY",
    "1HZ25V": "VOL_1S_FAMILY",
    "1HZ50V": "VOL_1S_FAMILY",
    "1HZ75V": "VOL_1S_FAMILY",
    "1HZ100V": "VOL_1S_FAMILY",
    "STEPINDEX": "STEP_CLASSIC_FAMILY",
    "STPRNG": "STEP_CLASSIC_FAMILY",
    "R_10": "STEP_CLASSIC_FAMILY",
    "R_25": "STEP_CLASSIC_FAMILY",
    "R_50": "STEP_CLASSIC_FAMILY",
    "R_75": "STEP_CLASSIC_FAMILY",
    "R_100": "STEP_CLASSIC_FAMILY",
    "BOOM1000": "STEP_CLASSIC_FAMILY",
    "CRASH1000": "STEP_CLASSIC_FAMILY",
}


def get_asset_bucket(symbol: str) -> str:
    """Maps a Deriv instrument to its uncorrelated asset-family training bucket."""
    sym = str(symbol or "").upper()
    if sym in ASSET_BUCKETS:
        return ASSET_BUCKETS[sym]
    if sym.startswith("JD"):
        return "JUMP_FAMILY"
    if sym.startswith("1HZ"):
        return "VOL_1S_FAMILY"
    return "STEP_CLASSIC_FAMILY"


class BankrollManager:
    def __init__(self, db: DatabaseManager, policy_path: str = "config/bankroll_policy.json"):
        self.db = db
        self.policy_path = policy_path
        self.policy = self._load_policy()
        self._sync_state()

    def _load_policy(self) -> Dict[str, Any]:
        if os.path.exists(self.policy_path):
            with open(self.policy_path, "r") as f:
                return json.load(f)
        return {
            "sandbox_starting_capital": 100.0,
            "max_drawdown_dollars": 40.0,
            "hard_equity_floor": 60.0,
            "sandbox_bankruptcy_threshold": 15.0,
            "sandbox_auto_reset": True,
            "max_concurrent_positions_small_account": 1,
            "profit_lock_vault": {
                "enabled": True,
                "growth_trigger_percent": 20.0,
                "vault_take_home_percent": 50.0,
                "compound_reinvest_percent": 50.0
            }
        }

    def get_equity_floor(self) -> float:
        """Returns the hard $60.00 ($40 max-drawdown on $100 start) equity protection floor."""
        starting_cap = float(self.policy.get("sandbox_starting_capital", 100.0))
        max_dd = float(self.policy.get("max_drawdown_dollars", 40.0))
        return float(self.policy.get("hard_equity_floor", max(0.0, starting_cap - max_dd)))

    def is_drawdown_breaker_active(self) -> bool:
        return bool(self.db.get_kv("drawdown_circuit_breaker_active", False))

    def clear_drawdown_breaker(self):
        self.db.set_kv("drawdown_circuit_breaker_active", False)

    def get_max_concurrent_positions(self, capital: Optional[float] = None) -> int:
        equity = float(capital) if capital is not None else float(self.db.get_kv("active_bankroll", 100.0))
        if equity <= 250.0:
            return int(self.policy.get("max_concurrent_positions_small_account", 1))
        return 3

    def get_asset_bucket(self, symbol: str) -> str:
        bucket_cfg = self.policy.get("paper_training_buckets", {}).get("buckets", {})
        sym_u = str(symbol or "").upper()
        for bucket_name, members in bucket_cfg.items():
            if sym_u in [m.upper() for m in members]:
                return bucket_name
        return get_asset_bucket(symbol)

    def get_paper_bucket_limits(self, is_live_ready: bool = False, capital: Optional[float] = None) -> Dict[str, Any]:
        """
        During TRAINING_CALIBRATION, allows up to 3 uncorrelated open/pending paper trades
        (max 1 per asset bucket: JUMP_FAMILY, VOL_1S_FAMILY, STEP_CLASSIC_FAMILY) capped at
        $4.50 (4.5%) total portfolio risk. Once LIVE_READY, locks strictly to 1 live slot.
        """
        equity = float(capital) if capital is not None else float(self.db.get_kv("active_bankroll", 100.0))
        pb = self.policy.get("paper_training_buckets", {})
        if not is_live_ready and pb.get("enabled", True):
            max_conc = int(pb.get("max_concurrent_positions", 3))
            max_per_b = int(pb.get("max_per_bucket", 1))
            max_heat_pct = float(pb.get("max_total_risk_percent", 4.5))
            max_heat_cap = float(pb.get("max_total_risk_dollars", 4.50))
            max_heat = round(min(max_heat_cap, equity * (max_heat_pct / 100.0)), 2) if equity <= 150.0 else round(equity * (max_heat_pct / 100.0), 2)
            return {
                "mode": "TRAINING_3_BUCKET",
                "max_concurrent": max_conc,
                "max_per_bucket": max_per_b,
                "max_total_risk": max_heat,
            }
        return {
            "mode": "LIVE_SINGLE_SLOT",
            "max_concurrent": self.get_max_concurrent_positions(equity),
            "max_per_bucket": 1,
            "max_total_risk": round(equity * (float(self.policy.get("live_hard_cap_risk_percent", 2.0)) / 100.0), 2),
        }

    def _sync_state(self):
        """Initializes or loads active bankroll, vault balance, and current epoch."""
        active_bankroll = self.db.get_kv("active_bankroll")
        vault_balance = self.db.get_kv("vault_balance")
        epoch_number = self.db.get_kv("epoch_number")
        base_watermark = self.db.get_kv("epoch_base_watermark")

        starting_capital = self.policy.get("sandbox_starting_capital", 100.0)

        if active_bankroll is None:
            self.db.set_kv("active_bankroll", starting_capital)
        if vault_balance is None:
            self.db.set_kv("vault_balance", 0.0)
        if epoch_number is None:
            self.db.set_kv("epoch_number", 1)
            self._record_epoch_start(1, starting_capital)
        if base_watermark is None:
            self.db.set_kv("epoch_base_watermark", starting_capital)

    def _record_epoch_start(self, epoch_num: int, starting_bal: float):
        now = datetime.now(timezone.utc).isoformat()
        with self.db.get_connection() as conn:
            conn.execute("""
            INSERT INTO simulation_epochs (epoch_number, start_time, starting_balance, status)
            VALUES (?, ?, ?, 'ACTIVE')
            """, (epoch_num, now, starting_bal))
            conn.commit()

    def get_status(self) -> Dict[str, Any]:
        return {
            "epoch_number": int(self.db.get_kv("epoch_number", 1)),
            "active_bankroll": float(self.db.get_kv("active_bankroll", 100.0)),
            "vault_balance": float(self.db.get_kv("vault_balance", 0.0)),
            "total_accumulated_capital": float(self.db.get_kv("active_bankroll", 100.0)) + float(self.db.get_kv("vault_balance", 0.0)),
            "base_watermark": float(self.db.get_kv("epoch_base_watermark", 100.0)),
            "bankruptcy_threshold": self.policy.get("sandbox_bankruptcy_threshold", 15.0),
            "hard_equity_floor": self.get_equity_floor(),
            "max_drawdown_dollars": float(self.policy.get("max_drawdown_dollars", 40.0)),
            "drawdown_breaker_active": self.is_drawdown_breaker_active(),
            "max_concurrent_positions": self.get_max_concurrent_positions(),
        }

    def process_trade_result(self, dollar_pnl: float, r_multiple: float, symbol: str) -> Dict[str, Any]:
        """
        Updates bankroll after a trade closes.
        Handles:
        1. $40 Max-Drawdown ($60.00 hard equity floor) DRAWDOWN_CIRCUIT_BREAKER + Council review.
        2. Sandbox bankruptcy auto-reset (<= $15.00).
        3. Two-Bucket Profit-Lock Vault sweeping (+20% milestone).
        """
        current_bankroll = float(self.db.get_kv("active_bankroll", 100.0))
        vault_balance = float(self.db.get_kv("vault_balance", 0.0))
        epoch_num = int(self.db.get_kv("epoch_number", 1))
        base_watermark = float(self.db.get_kv("epoch_base_watermark", 100.0))

        new_bankroll = round(current_bankroll + dollar_pnl, 2)
        bankruptcy_threshold = float(self.policy.get("sandbox_bankruptcy_threshold", 15.0))
        equity_floor = self.get_equity_floor()
        max_dd_dollars = float(self.policy.get("max_drawdown_dollars", 40.0))

        # Check legacy extreme bankruptcy threshold first (keeps epoch reset contract intact)
        if new_bankroll <= bankruptcy_threshold and self.policy.get("sandbox_auto_reset", True):
            now = datetime.now(timezone.utc).isoformat()
            with self.db.get_connection() as conn:
                conn.execute("""
                UPDATE simulation_epochs
                SET end_time = ?, final_balance = ?, status = 'BANKRUPT', reason = ?
                WHERE epoch_number = ? AND status = 'ACTIVE'
                """, (now, new_bankroll, f"Equity breached ${bankruptcy_threshold:.2f} threshold on {symbol}", epoch_num))
                conn.commit()

            self.db.log_council_note(
                symbol=symbol,
                outcome="LOSS",
                diagnosis=f"EPOCH_RESET_BANKRUPTCY at ${new_bankroll:.2f} (<= ${bankruptcy_threshold:.2f})",
                criticism=f"Catastrophic balance exhaustion during Epoch #{epoch_num} on {symbol}",
                mutation="Reset sandbox epoch to $100.00 while preserving Locked Profit Vault.",
            )

            next_epoch = epoch_num + 1
            starting_cap = float(self.policy.get("sandbox_starting_capital", 100.0))
            self.db.set_kv("epoch_number", next_epoch)
            self.db.set_kv("active_bankroll", starting_cap)
            self.db.set_kv("epoch_base_watermark", starting_cap)
            self._record_epoch_start(next_epoch, starting_cap)

            return {
                "event": "EPOCH_RESET_BANKRUPTCY",
                "old_epoch": epoch_num,
                "new_epoch": next_epoch,
                "final_balance": new_bankroll,
                "reset_bankroll": starting_cap,
                "vault_preserved": vault_balance,
                "circuit_breaker_triggered": True,
                "symbol": symbol,
                "pnl": dollar_pnl
            }

        # Check $40 Max-Drawdown ($60.00 Hard Equity Floor) Circuit Breaker
        if new_bankroll <= equity_floor:
            now = datetime.now(timezone.utc).isoformat()
            self.db.set_kv("drawdown_circuit_breaker_active", True)
            self.db.set_kv("drawdown_breaker_details", {
                "timestamp": now,
                "epoch": epoch_num,
                "final_balance": new_bankroll,
                "equity_floor": equity_floor,
                "symbol": symbol,
            })
            self.db.log_council_note(
                symbol=symbol,
                outcome="LOSS",
                diagnosis=f"DRAWDOWN_CIRCUIT_BREAKER triggered at ${new_bankroll:.2f} (<= ${equity_floor:.2f} floor / ${max_dd_dollars:.2f} max drawdown)",
                criticism=f"Account reached ${equity_floor:.2f} hard drawdown shield during Epoch #{epoch_num} on {symbol}. Trading paused pending Council audit.",
                mutation="Enforce DRAWDOWN_CIRCUIT_BREAKER pause, audit losing cluster, and cap active risk <= $1.50 (1 concurrent position).",
            )

            if self.policy.get("sandbox_auto_reset", True):
                with self.db.get_connection() as conn:
                    conn.execute("""
                    UPDATE simulation_epochs
                    SET end_time = ?, final_balance = ?, status = 'DRAWDOWN_BREAKER', reason = ?
                    WHERE epoch_number = ? AND status = 'ACTIVE'
                    """, (now, new_bankroll, f"$40 max drawdown (${equity_floor:.2f} floor) breached on {symbol}", epoch_num))
                    conn.commit()

                next_epoch = epoch_num + 1
                starting_cap = float(self.policy.get("sandbox_starting_capital", 100.0))
                self.db.set_kv("epoch_number", next_epoch)
                self.db.set_kv("active_bankroll", starting_cap)
                self.db.set_kv("epoch_base_watermark", starting_cap)
                self._record_epoch_start(next_epoch, starting_cap)

                return {
                    "event": "DRAWDOWN_CIRCUIT_BREAKER",
                    "pause_trading": True,
                    "council_review_logged": True,
                    "old_epoch": epoch_num,
                    "new_epoch": next_epoch,
                    "final_balance": new_bankroll,
                    "reset_bankroll": starting_cap,
                    "vault_preserved": vault_balance,
                    "equity_floor": equity_floor,
                    "max_drawdown_dollars": max_dd_dollars,
                    "symbol": symbol,
                    "pnl": dollar_pnl
                }

            self.db.set_kv("active_bankroll", new_bankroll)
            return {
                "event": "DRAWDOWN_CIRCUIT_BREAKER",
                "pause_trading": True,
                "council_review_logged": True,
                "old_epoch": epoch_num,
                "final_balance": new_bankroll,
                "vault_preserved": vault_balance,
                "equity_floor": equity_floor,
                "max_drawdown_dollars": max_dd_dollars,
                "symbol": symbol,
                "pnl": dollar_pnl
            }

        # Check Two-Bucket Profit-Lock Vault milestone
        # Milestone trigger: growth of 20% above base_watermark
        growth_trigger_pct = self.policy.get("profit_lock_vault", {}).get("growth_trigger_percent", 20.0)
        vault_split_pct = self.policy.get("profit_lock_vault", {}).get("vault_take_home_percent", 50.0)

        milestone_target = base_watermark * (1.0 + (growth_trigger_pct / 100.0))
        vault_sweep_occurred = False
        swept_to_vault = 0.0

        if new_bankroll >= milestone_target:
            profit_chunk = new_bankroll - base_watermark
            swept_to_vault = round(profit_chunk * (vault_split_pct / 100.0), 2)
            reinvested = round(profit_chunk - swept_to_vault, 2)

            new_vault_balance = round(vault_balance + swept_to_vault, 2)
            adjusted_active_bankroll = round(base_watermark + reinvested, 2)
            new_watermark = adjusted_active_bankroll

            self.db.set_kv("vault_balance", new_vault_balance)
            self.db.set_kv("active_bankroll", adjusted_active_bankroll)
            self.db.set_kv("epoch_base_watermark", new_watermark)

            # Record in vault_ledger
            now = datetime.now(timezone.utc).isoformat()
            with self.db.get_connection() as conn:
                conn.execute("""
                INSERT INTO vault_ledger (
                    timestamp, epoch_number, trigger_event,
                    amount_swept_to_vault, amount_compounded,
                    total_vault_balance, active_bankroll_balance
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, epoch_num, f"+{growth_trigger_pct}% Growth Sweep on {symbol}",
                    swept_to_vault, reinvested, new_vault_balance, adjusted_active_bankroll
                ))
                conn.commit()

            return {
                "event": "PROFIT_VAULT_SWEEP",
                "epoch_number": epoch_num,
                "pnl": dollar_pnl,
                "swept_to_vault": swept_to_vault,
                "compounded_to_bankroll": reinvested,
                "new_active_bankroll": adjusted_active_bankroll,
                "total_vaulted": new_vault_balance,
                "new_base_watermark": new_watermark
            }

        # Normal PnL update
        self.db.set_kv("active_bankroll", new_bankroll)
        return {
            "event": "TRADE_RECORDED",
            "epoch_number": epoch_num,
            "pnl": dollar_pnl,
            "r_multiple": r_multiple,
            "active_bankroll": new_bankroll,
            "vault_balance": vault_balance
        }
