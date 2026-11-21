"""
Paper Trading Simulator & Execution Engine.
Simulates realistic trade lifecycle:
- Entry verification (price touches entry zone)
- In-trade Break-Even trailing (+1.0R rule)
- Target resolution (TP1, TP2, SL)
- R-multiple calculation and Bankroll Vault synchronization
"""

from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.risk.pip_calculator import PipCalculator
from src.technicals.confluence_engine import TradingTip


class PaperTrader:
    def __init__(self, db: DatabaseManager, bankroll: BankrollManager, pip_calc: PipCalculator = None):
        self.db = db
        self.bankroll = bankroll
        self.pip_calc = pip_calc or PipCalculator()

    def submit_tip(self, tip: TradingTip, lot_size: float, dollar_risk: float, margin_req: float, screenshot_path: str = "") -> Optional[int]:
        """Submits a generated TradingTip as a virtual trade in the database."""
        if tip.direction == "NEUTRAL_NO_TRADE":
            return None

        epoch_info = self.bankroll.get_status()
        epoch_num = epoch_info["epoch_number"]
        now = datetime.now(timezone.utc).isoformat()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO paper_trades (
                epoch_number, symbol, direction, strategy_primary, strategy_secondary,
                entry_min, entry_max, stop_loss, original_stop_loss,
                take_profit_1, take_profit_2, lot_size, dollar_risk, margin_required,
                status, created_at, screenshot_path, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_ENTRY', ?, ?, ?)
            """, (
                epoch_num, tip.symbol, tip.direction, tip.primary_strategy, tip.secondary_strategy,
                tip.entry_min, tip.entry_max, tip.stop_loss, tip.stop_loss,
                tip.take_profit_1, tip.take_profit_2, lot_size, dollar_risk, margin_req,
                now, screenshot_path, "; ".join(tip.confluence_reasons)
            ))
            conn.commit()
            return cursor.lastrowid

    def update_open_positions(self, symbol: str, current_df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Simulates candle path for all open/pending trades on this symbol.
        Checks entry fill, break-even ratchet (+1.0R), and TP/SL hits.
        """
        resolved_trades = []
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM paper_trades
            WHERE symbol = ? AND status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')
            """, (symbol,))
            trades = [dict(row) for row in cursor.fetchall()]

        if not trades or len(current_df) == 0:
            return resolved_trades

        # Evaluate against the recent candles
        recent_candles = current_df.tail(10)

        for trade in trades:
            t_id = trade["trade_id"]
            direction = trade["direction"]
            lot = trade["lot_size"]
            orig_sl = trade["original_stop_loss"]
            curr_sl = trade["stop_loss"]
            tp1 = trade["take_profit_1"]
            entry_min = trade["entry_min"]
            entry_max = trade["entry_max"]
            status = trade["status"]

            for _, candle in recent_candles.iterrows():
                c_high = candle["high"]
                c_low = candle["low"]
                c_close = candle["close"]

                # 1. Check PENDING_ENTRY
                if status == "PENDING_ENTRY":
                    # Check if price touched entry zone
                    if (c_low <= entry_max and c_high >= entry_min):
                        status = "OPEN"
                        fill_price = (entry_min + entry_max) / 2.0
                        now = datetime.now(timezone.utc).isoformat()
                        with self.db.get_connection() as conn:
                            conn.execute("""
                            UPDATE paper_trades
                            SET status = 'OPEN', entry_executed = ?, entry_time = ?
                            WHERE trade_id = ?
                            """, (fill_price, now, t_id))
                            conn.commit()
                        trade["entry_executed"] = fill_price
                        trade["status"] = "OPEN"
                    else:
                        continue

                fill_price = trade["entry_executed"] or ((entry_min + entry_max) / 2.0)
                sl_dist = abs(fill_price - orig_sl) or 1.0

                # 2. Check Break-Even trigger (+1.0R)
                if status == "OPEN":
                    if direction == "BULLISH" and c_high >= (fill_price + sl_dist):
                        status = "BREAK_EVEN"
                        curr_sl = fill_price # Move SL to BE
                        with self.db.get_connection() as conn:
                            conn.execute("""
                            UPDATE paper_trades
                            SET status = 'BREAK_EVEN', stop_loss = ?
                            WHERE trade_id = ?
                            """, (curr_sl, t_id))
                            conn.commit()
                    elif direction == "BEARISH" and c_low <= (fill_price - sl_dist):
                        status = "BREAK_EVEN"
                        curr_sl = fill_price
                        with self.db.get_connection() as conn:
                            conn.execute("""
                            UPDATE paper_trades
                            SET status = 'BREAK_EVEN', stop_loss = ?
                            WHERE trade_id = ?
                            """, (curr_sl, t_id))
                            conn.commit()

                # 3. Check TP hit
                hit_tp = False
                hit_sl = False
                exit_price = 0.0

                if direction == "BULLISH":
                    if c_high >= tp1:
                        hit_tp = True
                        exit_price = tp1
                    elif c_low <= curr_sl:
                        hit_sl = True
                        exit_price = curr_sl
                else: # BEARISH
                    if c_low <= tp1:
                        hit_tp = True
                        exit_price = tp1
                    elif c_high >= curr_sl:
                        hit_sl = True
                        exit_price = curr_sl

                if hit_tp or hit_sl:
                    new_status = "CLOSED_TP" if hit_tp else "CLOSED_SL"
                    dollar_pnl = self.pip_calc.calculate_pnl(direction, fill_price, exit_price, lot, symbol)
                    r_mult = round((exit_price - fill_price) / sl_dist if direction == "BULLISH" else (fill_price - exit_price) / sl_dist, 2)
                    now = datetime.now(timezone.utc).isoformat()

                    with self.db.get_connection() as conn:
                        conn.execute("""
                        UPDATE paper_trades
                        SET status = ?, closed_at = ?, exit_price = ?, realized_pnl = ?, realized_r = ?
                        WHERE trade_id = ?
                        """, (new_status, now, exit_price, dollar_pnl, r_mult, t_id))
                        conn.commit()

                    # Notify Bankroll Manager (auto-reset and vault processing)
                    bankroll_event = self.bankroll.process_trade_result(dollar_pnl, r_mult, symbol)

                    resolved_trades.append({
                        "trade_id": t_id,
                        "symbol": symbol,
                        "outcome": "WIN" if hit_tp else "LOSS",
                        "status": new_status,
                        "exit_price": exit_price,
                        "realized_pnl": dollar_pnl,
                        "realized_r": r_mult,
                        "bankroll_event": bankroll_event,
                        "strategy": trade["strategy_primary"]
                    })
                    break

        return resolved_trades
