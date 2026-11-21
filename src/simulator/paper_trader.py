"""
Paper Trading Simulator & Execution Engine with Multi-Timeframe Visual Journaling.
Manages:
- Entry fills, Break-Even ratcheting (+1.0R), TP/SL exits
- Automatic BEFORE & AFTER multi-timeframe (H4, H1, M15) screenshot capture
- Fund telemetry: total_fund, used_fund, net_pnl, after_trade_fund, vault_locked_after
- Separate routing to profitable_trades (net_pnl > 0) vs losing_trades (net_pnl <= 0)
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.risk.pip_calculator import PipCalculator
from src.browser.screenshot_manager import ScreenshotManager
from src.technicals.confluence_engine import TradingTip


class PaperTrader:
    def __init__(
        self,
        db: DatabaseManager,
        bankroll: BankrollManager,
        pip_calc: Optional[PipCalculator] = None,
        screenshot_mgr: Optional[ScreenshotManager] = None,
        scraper: Any = None
    ):
        self.db = db
        self.bankroll = bankroll
        self.pip_calc = pip_calc or PipCalculator()
        self.screenshot_mgr = screenshot_mgr or ScreenshotManager()
        self.scraper = scraper

    def submit_tip(
        self,
        tip: TradingTip,
        lot_size: float,
        dollar_risk: float,
        margin_req: float,
        scraper: Any = None
    ) -> Optional[str]:
        """
        Opens a new trade, captures BEFORE screenshots across H4, H1, and M15,
        and records fund telemetry in active_trades.
        """
        if tip.direction == "NEUTRAL_NO_TRADE":
            return None

        active_scraper = scraper or self.scraper
        b_status = self.bankroll.get_status()
        epoch_num = b_status["epoch_number"]
        total_fund = b_status["active_bankroll"]
        used_fund = round(margin_req + dollar_risk, 2)

        trade_id = f"TRD-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{tip.symbol[:4]}"
        now = datetime.now(timezone.utc).isoformat()

        # Capture BEFORE screenshots across H4, H1, M15
        before_screens = {}
        if active_scraper:
            try:
                before_screens = self.screenshot_mgr.capture_and_store_trade_timeframes(
                    trade_id=trade_id,
                    symbol=tip.symbol,
                    stage="BEFORE",
                    entry_price=tip.entry_max if tip.direction == "BULLISH" else tip.entry_min,
                    sl_price=tip.stop_loss,
                    tp_price=tip.take_profit_1,
                    scraper=active_scraper,
                    timeframes=["H4", "H1", "M15"]
                )
            except Exception:
                before_screens = {}

        # 5W Breakdown
        w1 = f"Strategy: {tip.primary_strategy} | Evaluator: Confluence Engine"
        w2 = f"{tip.symbol} {tip.direction} | Lot: {lot_size} | TP: {tip.take_profit_1} | SL: {tip.stop_loss}"
        w3 = f"Opened: {now} | Target Hold: {tip.estimated_hold_hours:.1f} hrs"
        w4 = f"Entry Zone [{tip.entry_min:.2f} - {tip.entry_max:.2f}] | Invalidation {tip.stop_loss:.2f}"
        w5 = "; ".join(tip.confluence_reasons)

        trade_record = {
            "trade_id": trade_id,
            "epoch_id": epoch_num,
            "symbol": tip.symbol,
            "direction": tip.direction,
            "timeframes_used": "H4,H1,M15",
            "strategy_framework": tip.primary_strategy,
            "entry_min": tip.entry_min,
            "entry_max": tip.entry_max,
            "entry_executed": None,
            "tp_price": tip.take_profit_1,
            "sl_price": tip.stop_loss,
            "original_sl": tip.stop_loss,
            "lot_size": lot_size,
            "dollar_risk": dollar_risk,
            "margin_required": margin_req,
            "total_fund": total_fund,
            "used_fund": used_fund,
            "status": "PENDING_ENTRY",
            "before_screenshots": before_screens,
            "trade_reason_summary": f"{tip.symbol} {tip.direction} setup based on {tip.primary_strategy}",
            "w1_who": w1, "w2_what": w2, "w3_when": w3, "w4_where": w4, "w5_why": w5,
            "opened_at": now
        }

        # Insert into active_trades
        self.db.record_active_trade(trade_record)

        # Backward-compatibility insert into paper_trades
        with self.db.get_connection() as conn:
            conn.execute("""
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
                now, before_screens.get("H1", ""), "; ".join(tip.confluence_reasons)
            ))
            conn.commit()

        return trade_id

    def update_open_positions(self, symbol: str, current_df: pd.DataFrame, scraper: Any = None) -> List[Dict[str, Any]]:
        """
        Checks intrabar price paths for active trades on this symbol:
        - Fills pending entries
        - Ratchets SL to Break-Even at +1.0R
        - On TP or SL: captures AFTER screenshots (H4, H1, M15), routes to profitable_trades
          or losing_trades, sweeps 50% to Vault if milestone reached, and exports ledgers.
        """
        active_scraper = scraper or self.scraper
        resolved_trades = []

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM active_trades
            WHERE symbol = ? AND status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')
            """, (symbol,))
            trades = [dict(row) for row in cursor.fetchall()]

        if not trades or len(current_df) == 0:
            return resolved_trades

        recent_candles = current_df.tail(15)

        for trade in trades:
            t_id = trade["trade_id"]
            direction = trade["direction"]
            lot = trade["lot_size"]
            orig_sl = trade["original_sl"]
            curr_sl = trade["sl_price"]
            tp_price = trade["tp_price"]
            entry_min = trade["entry_min"]
            entry_max = trade["entry_max"]
            status = trade["status"]

            for _, candle in recent_candles.iterrows():
                c_high = candle["high"]
                c_low = candle["low"]

                # 1. Check entry fill
                if status == "PENDING_ENTRY":
                    if c_low <= entry_max and c_high >= entry_min:
                        status = "OPEN"
                        fill_p = (entry_min + entry_max) / 2.0
                        trade["entry_executed"] = fill_p
                        trade["status"] = "OPEN"
                        with self.db.get_connection() as conn:
                            conn.execute("UPDATE active_trades SET status = 'OPEN', entry_executed = ? WHERE trade_id = ?", (fill_p, t_id))
                            conn.commit()
                    else:
                        continue

                fill_price = trade["entry_executed"] or ((entry_min + entry_max) / 2.0)
                sl_dist = abs(fill_price - orig_sl) or 1.0

                # 2. Break-Even ratchet (+1.0R)
                if status == "OPEN":
                    if direction == "BULLISH" and c_high >= (fill_price + sl_dist):
                        status = "BREAK_EVEN"
                        curr_sl = fill_price
                        with self.db.get_connection() as conn:
                            conn.execute("UPDATE active_trades SET status = 'BREAK_EVEN', sl_price = ? WHERE trade_id = ?", (curr_sl, t_id))
                            conn.commit()
                    elif direction == "BEARISH" and c_low <= (fill_price - sl_dist):
                        status = "BREAK_EVEN"
                        curr_sl = fill_price
                        with self.db.get_connection() as conn:
                            conn.execute("UPDATE active_trades SET status = 'BREAK_EVEN', sl_price = ? WHERE trade_id = ?", (curr_sl, t_id))
                            conn.commit()

                # 3. Check TP or SL exit
                hit_tp = False
                hit_sl = False
                exit_price = 0.0

                if direction == "BULLISH":
                    if c_high >= tp_price:
                        hit_tp = True
                        exit_price = tp_price
                    elif c_low <= curr_sl:
                        hit_sl = True
                        exit_price = curr_sl
                else: # BEARISH
                    if c_low <= tp_price:
                        hit_tp = True
                        exit_price = tp_price
                    elif c_high >= curr_sl:
                        hit_sl = True
                        exit_price = curr_sl

                if hit_tp or hit_sl:
                    dollar_pnl = self.pip_calc.calculate_pnl(direction, fill_price, exit_price, lot, symbol)
                    r_mult = round((exit_price - fill_price) / sl_dist if direction == "BULLISH" else (fill_price - exit_price) / sl_dist, 2)
                    now_iso = datetime.now(timezone.utc).isoformat()

                    # Process bankroll & Profit Vault
                    bankroll_evt = self.bankroll.process_trade_result(dollar_pnl, r_mult, symbol)
                    b_status = self.bankroll.get_status()

                    total_fund = trade["total_fund"]
                    after_fund = round(total_fund + dollar_pnl, 2)
                    vault_after = b_status["vault_balance"]

                    # Capture AFTER screenshots across H4, H1, M15
                    after_screens = {}
                    if active_scraper:
                        try:
                            after_screens = self.screenshot_mgr.capture_and_store_trade_timeframes(
                                trade_id=t_id,
                                symbol=symbol,
                                stage="AFTER",
                                entry_price=fill_price,
                                sl_price=curr_sl,
                                tp_price=tp_price,
                                scraper=active_scraper,
                                timeframes=["H4", "H1", "M15"],
                                pnl=dollar_pnl
                            )
                        except Exception:
                            after_screens = {}

                    # Calculate hold duration
                    opened_dt = datetime.fromisoformat(trade["opened_at"])
                    hold_hours = round(max(0.5, (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600.0), 1)

                    # Routing to profitable_trades or losing_trades
                    closed_dict = {
                        "trade_id": t_id,
                        "epoch_id": trade["epoch_id"],
                        "symbol": symbol,
                        "direction": direction,
                        "timeframes_used": trade.get("timeframes_used", "H4,H1,M15"),
                        "strategy_framework": trade.get("strategy_framework", "SMC_ICT"),
                        "entry_price": fill_price,
                        "exit_price": exit_price,
                        "tp_price": tp_price,
                        "sl_price": curr_sl,
                        "lot_size": lot,
                        "rr_planned": round(abs(tp_price - fill_price) / sl_dist, 2),
                        "rr_realized": r_mult,
                        "total_fund": total_fund,
                        "used_fund": trade.get("used_fund", 1.50),
                        "net_pnl": dollar_pnl,
                        "after_trade_fund": after_fund,
                        "vault_locked_after": vault_after,
                        "trade_reason_summary": trade.get("trade_reason_summary") or f"{symbol} {direction} exit at {exit_price:.2f}",
                        "w1_who": trade.get("w1_who", "Confluence Engine"),
                        "w2_what": f"{symbol} {direction} {lot} lots",
                        "w3_when": f"Open: {trade['opened_at']} | Close: {now_iso} | Hold: {hold_hours}h",
                        "w4_where": trade.get("w4_where", "H4/H1 Zone"),
                        "w5_why": f"Exit triggered by {'Take Profit' if hit_tp else 'Stop Loss'}",
                        "what_improved": "Bayesian expectation updated; Monte Carlo simulated",
                        "before_screenshots": json.loads(trade.get("before_screenshots_json") or "{}"),
                        "after_screenshots": after_screens,
                        "opened_at": trade["opened_at"],
                        "closed_at": now_iso,
                        "hold_duration_hours": hold_hours
                    }

                    target_table = self.db.record_closed_trade(closed_dict)

                    resolved_trades.append({
                        "trade_id": t_id,
                        "symbol": symbol,
                        "outcome": "WIN" if hit_tp else "LOSS",
                        "target_table": target_table,
                        "realized_pnl": dollar_pnl,
                        "realized_r": r_mult,
                        "exit_price": exit_price,
                        "strategy": trade.get("strategy_framework", "SMC_ICT"),
                        "closed_dict": closed_dict,
                        "before_screenshots": closed_dict["before_screenshots"],
                        "after_screenshots": after_screens
                    })
                    break

        return resolved_trades
