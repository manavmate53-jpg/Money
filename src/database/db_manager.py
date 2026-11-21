"""
SQLite Database Manager for Deriv Synthetic Trading Bot & Gemini Council.
Handles candles, paper trades, simulation epochs, profit vault, council notes, and readiness gate.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any


class DatabaseManager:
    def __init__(self, db_path: str = "data/trading_state.db"):
        self.db_path = db_path
        # Migrate legacy path if exists
        if db_path == "data/trading_state.db" and not os.path.exists(db_path) and os.path.exists("data/trading_bot.db"):
            import shutil
            shutil.copyfile("data/trading_bot.db", db_path)
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_tables()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_tables(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Candles cache
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS candles (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, timeframe, epoch)
            );
            """)

            # Simulation Epochs (Auto-Reset tracking)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulation_epochs (
                epoch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch_number INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                starting_balance REAL NOT NULL,
                final_balance REAL,
                status TEXT NOT NULL, -- ACTIVE, BANKRUPT, COMPLETED
                reason TEXT
            );
            """)

            # Paper Trades table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch_number INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL, -- BUY, SELL
                strategy_primary TEXT NOT NULL,
                strategy_secondary TEXT,
                entry_min REAL NOT NULL,
                entry_max REAL NOT NULL,
                entry_executed REAL,
                stop_loss REAL NOT NULL,
                original_stop_loss REAL NOT NULL,
                take_profit_1 REAL NOT NULL,
                take_profit_2 REAL,
                lot_size REAL NOT NULL,
                dollar_risk REAL NOT NULL,
                margin_required REAL NOT NULL,
                status TEXT NOT NULL, -- PENDING_ENTRY, OPEN, BREAK_EVEN, CLOSED_TP, CLOSED_SL, TIMEOUT
                created_at TEXT NOT NULL,
                entry_time TEXT,
                closed_at TEXT,
                exit_price REAL,
                realized_pnl REAL,
                realized_r REAL,
                hold_duration_hours REAL,
                screenshot_path TEXT,
                notes TEXT
            );
            """)

            # Two-Bucket Profit Lock Vault ("Profit Comes Home With Us")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS vault_ledger (
                transfer_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                epoch_number INTEGER NOT NULL,
                trigger_event TEXT NOT NULL,
                amount_swept_to_vault REAL NOT NULL,
                amount_compounded REAL NOT NULL,
                total_vault_balance REAL NOT NULL,
                active_bankroll_balance REAL NOT NULL
            );
            """)

            # Gemini Council Notes & Reflexion Journal
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS council_journal (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trade_id INTEGER,
                symbol TEXT NOT NULL,
                outcome TEXT NOT NULL, -- WIN, LOSS, BANKRUPTCY, SCREENING
                structural_diagnosis TEXT NOT NULL,
                risk_criticism TEXT NOT NULL,
                actionable_rule_mutation TEXT NOT NULL,
                weight_changes_json TEXT,
                raw_gemini_response TEXT
            );
            """)

            # System State & Readiness Snapshots
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS readiness_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                day_number INTEGER NOT NULL,
                total_trades INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                profit_factor REAL NOT NULL,
                max_drawdown_percent REAL NOT NULL,
                total_vaulted_profit REAL NOT NULL,
                active_bankroll REAL NOT NULL,
                readiness_status TEXT NOT NULL -- TRAINING_CALIBRATION, LIVE_READY
            );
            """)

            # Key-Value system store
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)
            conn.commit()

    def set_kv(self, key: str, value: Any):
        val_str = json.dumps(value) if not isinstance(value, str) else value
        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_kv (key, value, updated_at) VALUES (?, ?, ?)",
                (key, val_str, now)
            )
            conn.commit()

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_kv WHERE key = ?", (key,))
            row = cursor.fetchone()
            if not row:
                return default
            try:
                return json.loads(row["value"])
            except Exception:
                return row["value"]

    def log_council_note(
        self,
        symbol: str,
        outcome: str,
        diagnosis: str,
        criticism: str,
        mutation: str,
        trade_id: Optional[int] = None,
        weight_changes: Optional[Dict] = None,
        raw_response: Optional[str] = None
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        weight_json = json.dumps(weight_changes) if weight_changes else "{}"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO council_journal (
                timestamp, trade_id, symbol, outcome,
                structural_diagnosis, risk_criticism,
                actionable_rule_mutation, weight_changes_json, raw_gemini_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now, trade_id, symbol, outcome,
                diagnosis, criticism, mutation, weight_json, raw_response or ""
            ))
            conn.commit()
            return cursor.lastrowid
