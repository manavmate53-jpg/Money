"""
Institutional Database Manager with Strictly Separated Win/Loss Tables.
Creates:
1. active_trades: currently open or pending positions.
2. profitable_trades: dedicated table for closed trades with net_pnl > 0.
3. losing_trades: dedicated table for closed trades with net_pnl <= 0.
4. simulation_epochs, vault_ledger, council_journal, system_kv.
Exports:
- reports/PROFIT_LEDGER.md
- reports/LOSS_LEDGER.md
- reports/TRADE_VISUAL_JOURNAL.md
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any


class DatabaseManager:
    def __init__(self, db_path: str = "data/trading_state.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.init_tables()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_tables(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Active/pending trades
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS active_trades (
                trade_id TEXT PRIMARY KEY,
                epoch_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                timeframes_used TEXT NOT NULL,
                strategy_framework TEXT NOT NULL,
                entry_min REAL NOT NULL,
                entry_max REAL NOT NULL,
                entry_executed REAL,
                tp_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                original_sl REAL NOT NULL,
                lot_size REAL NOT NULL,
                dollar_risk REAL NOT NULL,
                margin_required REAL NOT NULL,
                total_fund REAL NOT NULL,
                used_fund REAL NOT NULL,
                status TEXT NOT NULL, -- PENDING_ENTRY, OPEN, BREAK_EVEN
                before_screenshots_json TEXT NOT NULL,
                trade_reason_summary TEXT,
                w1_who TEXT, w2_what TEXT, w3_when TEXT, w4_where TEXT, w5_why TEXT,
                opened_at TEXT NOT NULL
            );
            """)

            # Dedicated Table 1: PROFITABLE TRADES (net_pnl > 0)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS profitable_trades (
                trade_id TEXT PRIMARY KEY,
                epoch_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                timeframes_used TEXT NOT NULL,
                strategy_framework TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                tp_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                lot_size REAL NOT NULL,
                rr_planned REAL NOT NULL,
                rr_realized REAL NOT NULL,
                total_fund REAL NOT NULL,
                used_fund REAL NOT NULL,
                net_pnl REAL NOT NULL,
                after_trade_fund REAL NOT NULL,
                vault_locked_after REAL NOT NULL,
                trade_reason_summary TEXT NOT NULL,
                w1_who TEXT NOT NULL,
                w2_what TEXT NOT NULL,
                w3_when TEXT NOT NULL,
                w4_where TEXT NOT NULL,
                w5_why TEXT NOT NULL,
                what_improved TEXT NOT NULL,
                before_screenshots_json TEXT NOT NULL,
                after_screenshots_json TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                hold_duration_hours REAL NOT NULL
            );
            """)

            # Dedicated Table 2: LOSING TRADES (net_pnl <= 0)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS losing_trades (
                trade_id TEXT PRIMARY KEY,
                epoch_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                timeframes_used TEXT NOT NULL,
                strategy_framework TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                tp_price REAL NOT NULL,
                sl_price REAL NOT NULL,
                lot_size REAL NOT NULL,
                rr_planned REAL NOT NULL,
                rr_realized REAL NOT NULL,
                total_fund REAL NOT NULL,
                used_fund REAL NOT NULL,
                net_pnl REAL NOT NULL,
                after_trade_fund REAL NOT NULL,
                vault_locked_after REAL NOT NULL,
                trade_reason_summary TEXT NOT NULL,
                w1_who TEXT NOT NULL,
                w2_what TEXT NOT NULL,
                w3_when TEXT NOT NULL,
                w4_where TEXT NOT NULL,
                w5_why TEXT NOT NULL,
                what_improved TEXT NOT NULL,
                before_screenshots_json TEXT NOT NULL,
                after_screenshots_json TEXT NOT NULL,
                opened_at TEXT NOT NULL,
                closed_at TEXT NOT NULL,
                hold_duration_hours REAL NOT NULL
            );
            """)

            # Backward-compatible view or legacy table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch_number INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
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
                status TEXT NOT NULL,
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

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulation_epochs (
                epoch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                epoch_number INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                starting_balance REAL NOT NULL,
                final_balance REAL,
                status TEXT NOT NULL,
                reason TEXT
            );
            """)

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

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS council_journal (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                trade_id TEXT,
                symbol TEXT NOT NULL,
                outcome TEXT NOT NULL,
                structural_diagnosis TEXT NOT NULL,
                risk_criticism TEXT NOT NULL,
                actionable_rule_mutation TEXT NOT NULL,
                weight_changes_json TEXT,
                raw_gemini_response TEXT
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS pattern_analogs (
                analog_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                features_json TEXT NOT NULL,
                outcome_r REAL NOT NULL,
                is_win INTEGER NOT NULL,
                source TEXT NOT NULL
            );
            """)

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

    def record_active_trade(self, trade_dict: Dict[str, Any]):
        """Inserts an open trade into active_trades."""
        with self.get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO active_trades (
                trade_id, epoch_id, symbol, direction, timeframes_used, strategy_framework,
                entry_min, entry_max, entry_executed, tp_price, sl_price, original_sl,
                lot_size, dollar_risk, margin_required, total_fund, used_fund,
                status, before_screenshots_json, trade_reason_summary,
                w1_who, w2_what, w3_when, w4_where, w5_why, opened_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_dict["trade_id"], trade_dict["epoch_id"], trade_dict["symbol"], trade_dict["direction"],
                trade_dict.get("timeframes_used", "H4,H1,M15"), trade_dict.get("strategy_framework", "SMC_ICT"),
                trade_dict["entry_min"], trade_dict["entry_max"], trade_dict.get("entry_executed"),
                trade_dict["tp_price"], trade_dict["sl_price"], trade_dict["original_sl"],
                trade_dict["lot_size"], trade_dict["dollar_risk"], trade_dict["margin_required"],
                trade_dict["total_fund"], trade_dict["used_fund"], trade_dict.get("status", "PENDING_ENTRY"),
                json.dumps(trade_dict.get("before_screenshots", {})),
                trade_dict.get("trade_reason_summary", ""),
                trade_dict.get("w1_who", ""), trade_dict.get("w2_what", ""),
                trade_dict.get("w3_when", ""), trade_dict.get("w4_where", ""),
                trade_dict.get("w5_why", ""), trade_dict.get("opened_at", datetime.now(timezone.utc).isoformat())
            ))
            conn.commit()

    def record_closed_trade(self, closed_dict: Dict[str, Any]) -> str:
        """
        Routes closed trade to profitable_trades if net_pnl > 0 else losing_trades.
        Removes the trade from active_trades.
        """
        net_pnl = float(closed_dict.get("net_pnl", 0.0))
        target_table = "profitable_trades" if net_pnl > 0 else "losing_trades"
        t_id = str(closed_dict["trade_id"])

        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Delete from active_trades
            cursor.execute("DELETE FROM active_trades WHERE trade_id = ?", (t_id,))

            cursor.execute(f"""
            INSERT OR REPLACE INTO {target_table} (
                trade_id, epoch_id, symbol, direction, timeframes_used, strategy_framework,
                entry_price, exit_price, tp_price, sl_price, lot_size, rr_planned, rr_realized,
                total_fund, used_fund, net_pnl, after_trade_fund, vault_locked_after,
                trade_reason_summary, w1_who, w2_what, w3_when, w4_where, w5_why,
                what_improved, before_screenshots_json, after_screenshots_json,
                opened_at, closed_at, hold_duration_hours
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                t_id, closed_dict.get("epoch_id", 1), closed_dict["symbol"], closed_dict["direction"],
                closed_dict.get("timeframes_used", "H4,H1,M15"), closed_dict.get("strategy_framework", "SMC_ICT"),
                closed_dict["entry_price"], closed_dict["exit_price"], closed_dict["tp_price"], closed_dict["sl_price"],
                closed_dict["lot_size"], closed_dict.get("rr_planned", 2.5), closed_dict.get("rr_realized", 0.0),
                closed_dict.get("total_fund", 100.0), closed_dict.get("used_fund", 1.50),
                net_pnl, closed_dict.get("after_trade_fund", 100.0 + net_pnl), closed_dict.get("vault_locked_after", 0.0),
                closed_dict.get("trade_reason_summary", ""),
                closed_dict.get("w1_who", "Confluence Engine"),
                closed_dict.get("w2_what", f"{closed_dict['symbol']} {closed_dict['direction']}"),
                closed_dict.get("w3_when", datetime.now(timezone.utc).isoformat()),
                closed_dict.get("w4_where", "H4/H1 structural zone"),
                closed_dict.get("w5_why", "SMC/ICT liquidity alignment"),
                closed_dict.get("what_improved", "Bayesian weight updated"),
                json.dumps(closed_dict.get("before_screenshots", {})),
                json.dumps(closed_dict.get("after_screenshots", {})),
                closed_dict.get("opened_at", datetime.now(timezone.utc).isoformat()),
                closed_dict.get("closed_at", datetime.now(timezone.utc).isoformat()),
                closed_dict.get("hold_duration_hours", 2.0)
            ))
            conn.commit()

        # Re-export Markdown Ledgers
        self.export_all_ledgers()
        return target_table

    def get_profitable_trades(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM profitable_trades ORDER BY closed_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def get_losing_trades(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM losing_trades ORDER BY closed_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def get_last_loss_for_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Returns the most recent losing trade on `symbol` for anti-revenge cooldown checks."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM losing_trades WHERE symbol = ? ORDER BY closed_at DESC LIMIT 1",
                (symbol,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_hours_since_last_loss(self, symbol: str) -> Optional[float]:
        """Returns elapsed hours since the latest SL hit on `symbol`, or None if never lost."""
        last_loss = self.get_last_loss_for_symbol(symbol)
        if not last_loss or not last_loss.get("closed_at"):
            return None
        try:
            closed_dt = datetime.fromisoformat(last_loss["closed_at"])
            if closed_dt.tzinfo is None:
                closed_dt = closed_dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - closed_dt).total_seconds() / 3600.0)
        except Exception:
            return None

    def get_active_trades(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM active_trades ORDER BY opened_at DESC")
            return [dict(r) for r in cursor.fetchall()]

    def get_resolved_trades(self, epoch_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Unified query returning resolved trades across profitable_trades and losing_trades
        via UNION ALL ordered chronologically by closed_at ASC.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if epoch_id is not None:
                cursor.execute("""
                SELECT *, 'WIN' AS outcome FROM profitable_trades WHERE epoch_id = ?
                UNION ALL
                SELECT *, 'LOSS' AS outcome FROM losing_trades WHERE epoch_id = ?
                ORDER BY closed_at ASC
                """, (epoch_id, epoch_id))
            else:
                cursor.execute("""
                SELECT *, 'WIN' AS outcome FROM profitable_trades
                UNION ALL
                SELECT *, 'LOSS' AS outcome FROM losing_trades
                ORDER BY closed_at ASC
                """)
            return [dict(r) for r in cursor.fetchall()]

    def add_pattern_analog(self, analog: Dict[str, Any]) -> int:
        """Inserts a single normalized 8-feature setup analog row."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO pattern_analogs (
                symbol, timeframe, direction, timestamp,
                features_json, outcome_r, is_win, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analog["symbol"],
                analog.get("timeframe", "H1"),
                analog["direction"],
                analog["timestamp"],
                json.dumps(analog["features"]),
                float(analog["outcome_r"]),
                int(analog["is_win"]),
                analog.get("source", "PAPER_TRADE"),
            ))
            conn.commit()
            return cursor.lastrowid

    def bulk_add_pattern_analogs(self, analogs: List[Dict[str, Any]]) -> int:
        """Bulk-inserts walk-forward historical analogs cleanly."""
        if not analogs:
            return 0
        rows = [
            (
                a["symbol"],
                a.get("timeframe", "H1"),
                a["direction"],
                a["timestamp"],
                json.dumps(a["features"]),
                float(a["outcome_r"]),
                int(a["is_win"]),
                a.get("source", "WALK_FORWARD_90D"),
            )
            for a in analogs
        ]
        with self.get_connection() as conn:
            conn.executemany("""
            INSERT INTO pattern_analogs (
                symbol, timeframe, direction, timestamp,
                features_json, outcome_r, is_win, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
        return len(rows)

    def clear_pattern_analogs(self, source: Optional[str] = None):
        with self.get_connection() as conn:
            if source:
                conn.execute("DELETE FROM pattern_analogs WHERE source = ?", (source,))
            else:
                conn.execute("DELETE FROM pattern_analogs")
            conn.commit()

    def get_pattern_analogs(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Loads stored historical pattern analogs for k-NN Euclidean lookup."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if symbol:
                cursor.execute("SELECT * FROM pattern_analogs WHERE symbol = ?", (symbol,))
            else:
                cursor.execute("SELECT * FROM pattern_analogs")
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                try:
                    d["features"] = json.loads(d["features_json"])
                    results.append(d)
                except Exception:
                    continue
            return results

    def get_active_epoch_info(self) -> Dict[str, Any]:
        """Returns the latest active simulation epoch metadata."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT epoch_number, start_time, starting_balance, status
            FROM simulation_epochs
            ORDER BY epoch_number DESC LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return dict(row)
        return {
            "epoch_number": 1,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "starting_balance": 100.0,
            "status": "ACTIVE"
        }

    def mark_trade_expired(self, trade_id: str, reason: str = "EXPIRED_STALE") -> bool:
        """
        Cancels a stale or pre-entry invalidated PENDING_ENTRY order cleanly without
        recording a realized PnL loss, and syncs active_trades & paper_trades.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT symbol, opened_at FROM active_trades WHERE trade_id = ?", (trade_id,))
            row = cursor.fetchone()
            cursor.execute("DELETE FROM active_trades WHERE trade_id = ?", (trade_id,))
            if row:
                cursor.execute("""
                UPDATE paper_trades
                SET status = 'EXPIRED_STALE', closed_at = ?, notes = COALESCE(notes, '') || ' | ' || ?
                WHERE trade_id = (
                    SELECT MAX(trade_id) FROM paper_trades
                    WHERE symbol = ? AND status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')
                )
                """, (now_iso, reason, row["symbol"]))
            conn.commit()
            return bool(row)

    def sync_paper_trade_closure(self, symbol: str, outcome_status: str, exit_price: float, net_pnl: float, realized_r: float, hold_hours: float, closed_at: str):
        """Keeps legacy paper_trades table synchronized with resolved trades."""
        with self.get_connection() as conn:
            conn.execute("""
            UPDATE paper_trades
            SET status = ?, closed_at = ?, exit_price = ?, realized_pnl = ?, realized_r = ?, hold_duration_hours = ?
            WHERE trade_id = (
                SELECT MAX(trade_id) FROM paper_trades
                WHERE symbol = ? AND status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')
            )
            """, (outcome_status, closed_at, exit_price, net_pnl, realized_r, hold_hours, symbol))
            conn.commit()

    def log_council_note(
        self,
        symbol: str,
        outcome: str,
        diagnosis: str,
        criticism: str,
        mutation: str,
        trade_id: Optional[str] = None,
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
                now, str(trade_id or ""), symbol, outcome,
                diagnosis, criticism, mutation, weight_json, raw_response or ""
            ))
            conn.commit()
            return cursor.lastrowid

    def export_all_ledgers(self, reports_dir: str = "reports"):
        """Exports reports/PROFIT_LEDGER.md, LOSS_LEDGER.md, and TRADE_VISUAL_JOURNAL.md."""
        os.makedirs(reports_dir, exist_ok=True)
        self.export_profit_ledger(os.path.join(reports_dir, "PROFIT_LEDGER.md"))
        self.export_loss_ledger(os.path.join(reports_dir, "LOSS_LEDGER.md"))
        self.export_visual_journal(os.path.join(reports_dir, "TRADE_VISUAL_JOURNAL.md"))

    def export_profit_ledger(self, file_path: str):
        wins = self.get_profitable_trades()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        total_pnl = sum(w["net_pnl"] for w in wins)

        lines = [
            "# 🟢 PROFIT LEDGER (WINNING TRADES REPOSITORY)",
            f"*Auto-generated from `profitable_trades` SQLite table | Refreshed: {now}*",
            f"**Total Realized Profit**: `+${total_pnl:.2f}` across `{len(wins)}` winning setups\n",
            "| Trade ID | Symbol | Dir | Lot | Entry ➔ Exit | Realized R | Net Profit | Pre-Fund | Post-Fund | Vault After | 5W Thesis |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]

        for w in wins:
            lines.append(
                f"| `{w['trade_id']}` | **{w['symbol']}** | {w['direction']} | {w['lot_size']} | "
                f"{w['entry_price']:.2f} ➔ {w['exit_price']:.2f} | **+{w['rr_realized']:.2f}R** | "
                f"**+${w['net_pnl']:.2f}** | ${w['total_fund']:.2f} | ${w['after_trade_fund']:.2f} | "
                f"${w['vault_locked_after']:.2f} | *{w['trade_reason_summary']}* |"
            )

        if not wins:
            lines.append("\n*No profitable trades recorded yet in this forward-testing calibration window.*")

        with open(file_path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def export_loss_ledger(self, file_path: str):
        losses = self.get_losing_trades()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        total_loss = abs(sum(l["net_pnl"] for l in losses))

        lines = [
            "# 🔴 LOSS LEDGER (POST-MORTEM & LOSS AUDIT REPOSITORY)",
            f"*Auto-generated from `losing_trades` SQLite table | Refreshed: {now}*",
            f"**Total Realized Loss**: `-${total_loss:.2f}` across `{len(losses)}` losing setups\n",
            "| Trade ID | Symbol | Dir | Lot | Entry ➔ Exit | Realized R | Net Loss | Pre-Fund | Post-Fund | 5W Root Cause | What Improved? |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |"
        ]

        for l in losses:
            lines.append(
                f"| `{l['trade_id']}` | **{l['symbol']}** | {l['direction']} | {l['lot_size']} | "
                f"{l['entry_price']:.2f} ➔ {l['exit_price']:.2f} | **{l['rr_realized']:.2f}R** | "
                f"**-${abs(l['net_pnl']):.2f}** | ${l['total_fund']:.2f} | ${l['after_trade_fund']:.2f} | "
                f"*{l['trade_reason_summary']}* | **{l['what_improved']}** |"
            )

        if not losses:
            lines.append("\n*No losing trades recorded yet in this forward-testing calibration window.*")

        with open(file_path, "w") as f:
            f.write("\n".join(lines) + "\n")

    def export_visual_journal(self, file_path: str):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        wins = self.get_profitable_trades()
        losses = self.get_losing_trades()
        all_trades = sorted(wins + losses, key=lambda x: x["closed_at"], reverse=True)

        lines = [
            "# 🖼️ TRADE VISUAL PROGRESS JOURNAL (BEFORE VS. AFTER AUDIT)",
            f"*Side-by-side visual chart gallery rendered across all timeframes (H4, H1, M15) | Refreshed: {now}*\n"
        ]

        for t in all_trades:
            t_id = t["trade_id"]
            outcome = "WIN 🟢" if t["net_pnl"] > 0 else "LOSS 🔴"
            pnl_str = f"+${t['net_pnl']:.2f}" if t["net_pnl"] > 0 else f"-${abs(t['net_pnl']):.2f}"

            lines.append(f"## Trade `{t_id}`: **{t['symbol']}** — {t['direction']} ({outcome} {pnl_str} / {t['rr_realized']:+.2f}R)")
            lines.append(f"- **Strategy**: `{t['strategy_framework']}` | **Hold**: `{t['hold_duration_hours']:.1f} hrs`")
            lines.append(f"- **5W Summary**: *{t['trade_reason_summary']}*")
            lines.append(f"- **Algorithm Delta**: `{t['what_improved']}`\n")

            # Parse screenshot JSONs
            try:
                b_imgs = json.loads(t.get("before_screenshots_json") or "{}")
            except Exception:
                b_imgs = {}
            try:
                a_imgs = json.loads(t.get("after_screenshots_json") or "{}")
            except Exception:
                a_imgs = {}

            lines.append("| Timeframe | BEFORE Trade Setup | AFTER Trade Outcome |")
            lines.append("| :---: | :---: | :---: |")

            for tf in ["H4", "H1", "M15"]:
                b_path = b_imgs.get(tf, f"data/trade_charts/{t_id}/BEFORE_{tf}.png")
                a_path = a_imgs.get(tf, f"data/trade_charts/{t_id}/AFTER_{tf}.png")
                lines.append(f"| **{tf}** | ![{tf} Before]({b_path}) | ![{tf} After]({a_path}) |")

            lines.append("\n---\n")

        if not all_trades:
            lines.append("*No visual trade journals generated yet. Visual comparisons appear here as trades complete.*")

        with open(file_path, "w") as f:
            f.write("\n".join(lines) + "\n")
