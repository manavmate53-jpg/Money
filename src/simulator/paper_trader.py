"""
Paper Trading Simulator & Execution Engine with Multi-Timeframe Visual Journaling.
Manages:
- Strict small-account ($100) viability gate before order submission
- 6-hour Pending Order TTL & pre-entry structural SL invalidation auto-expiry (EXPIRED_STALE)
- Entry fills, Break-Even ratcheting (+1.0R), TP/SL exits
- Automatic BEFORE & AFTER multi-timeframe (H4, H1, M15) screenshot capture
- Unified sync across active_trades, paper_trades, profitable_trades, and losing_trades
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.browser.screenshot_manager import ScreenshotManager
from src.technicals.confluence_engine import TradingTip

logger = logging.getLogger(__name__)


class PaperTrader:
    def __init__(
        self,
        db: DatabaseManager,
        bankroll: BankrollManager,
        pip_calc: Optional[PipCalculator] = None,
        screenshot_mgr: Optional[ScreenshotManager] = None,
        scraper: Any = None,
        position_sizer: Optional[PositionSizer] = None,
        filters_path: str = "config/active_filters.json",
    ):
        self.db = db
        self.bankroll = bankroll
        self.pip_calc = pip_calc or PipCalculator()
        self.position_sizer = position_sizer or PositionSizer(self.pip_calc)
        self.screenshot_mgr = screenshot_mgr or ScreenshotManager()
        self.scraper = scraper
        self.filters_path = filters_path

    def _get_filter_float(self, key: str, default: float) -> float:
        if self.filters_path and os.path.exists(self.filters_path):
            try:
                with open(self.filters_path, "r") as f:
                    return float(json.load(f).get(key, default))
            except Exception:
                pass
        return default

    def _get_max_pending_hours(self) -> float:
        return self._get_filter_float("max_pending_hours", 6.0)

    def submit_tip(
        self,
        tip: TradingTip,
        lot_size: float,
        dollar_risk: float,
        margin_req: float,
        scraper: Any = None
    ) -> Optional[str]:
        """
        Validates Anti-Revenge cooldown & $100 viability gate before opening a paper order,
        captures BEFORE screenshots across H4/H1/M15, and records fund telemetry.
        """
        if tip.direction == "NEUTRAL_NO_TRADE":
            return None

        # Anti-Revenge Shield: block re-entry within cooldown_hours after SL hit on same symbol
        cooldown_hours = self._get_filter_float("revenge_cooldown_hours", 2.0)
        hours_since_loss = self.db.get_hours_since_last_loss(tip.symbol)
        if hours_since_loss is not None and hours_since_loss < cooldown_hours:
            logger.warning(
                f"PaperTrader blocked anti-revenge entry on {tip.symbol}: "
                f"{hours_since_loss:.2f}h elapsed < {cooldown_hours:.1f}h cooldown"
            )
            return None

        active_scraper = scraper or self.scraper
        b_status = self.bankroll.get_status()
        epoch_num = b_status["epoch_number"]
        total_fund = b_status["active_bankroll"]
        equity_floor = b_status.get("hard_equity_floor", 60.0)

        # Hard $40 Max-Drawdown ($60.00 equity floor) Circuit Breaker check
        if self.bankroll.is_drawdown_breaker_active() or total_fund <= equity_floor:
            logger.warning(
                f"PaperTrader blocked order on {tip.symbol}: DRAWDOWN_CIRCUIT_BREAKER active "
                f"(equity=${total_fund:.2f} vs floor=${equity_floor:.2f})"
            )
            return None

        # Enforce max 1 concurrent open trade on $100 small accounts
        max_concurrent = self.bankroll.get_max_concurrent_positions(total_fund)
        if len(self.db.get_active_trades()) >= max_concurrent:
            logger.warning(
                f"PaperTrader blocked order on {tip.symbol}: max concurrent positions ({max_concurrent}) reached"
            )
            return None

        entry_ref = tip.entry_max if tip.direction == "BULLISH" else tip.entry_min

        # Strict Small-Account Viability Gate inside paper execution
        is_viable, reject_reason = self.position_sizer.is_viable_for_account(
            symbol=tip.symbol,
            account_equity=total_fund,
            entry_price=entry_ref,
            stop_loss_price=tip.stop_loss,
        )
        if not is_viable:
            logger.warning(f"PaperTrader blocked order on {tip.symbol}: {reject_reason}")
            return None

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
                    entry_price=entry_ref,
                    sl_price=tip.stop_loss,
                    tp_price=tip.take_profit_1,
                    scraper=active_scraper,
                    timeframes=["H4", "H1", "M15"]
                )
            except Exception as e:
                logger.warning(f"BEFORE screenshot capture failed for {trade_id}: {e}")
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

        self.db.record_active_trade(trade_record)
        if getattr(tip, "feature_vector", None):
            self.db.set_kv(f"trade_features:{trade_id}", list(tip.feature_vector))

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

    def update_open_positions(
        self,
        symbol: str,
        current_df: pd.DataFrame,
        scraper: Any = None,
        m15_df: Optional[pd.DataFrame] = None,
    ) -> List[Dict[str, Any]]:
        """
        Evaluates active trades for `symbol`:
        - Evaluates M15 candles strictly inside [opened_at, opened_at + max_pending_hours] BEFORE
          declaring EXPIRED_STALE so delayed-cron runs never miss valid fills inside the 6h TTL window
        - Ratchets SL to Break-Even at +1.0R across [fill_time, now_utc]
        - Resolves TP/SL hits, captures AFTER charts, updates Vault & split profit/loss tables
        """
        active_scraper = scraper or self.scraper
        resolved_trades: List[Dict[str, Any]] = []
        max_pending_hours = self._get_max_pending_hours()

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT * FROM active_trades
            WHERE symbol = ? AND status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')
            """, (symbol,))
            trades = [dict(row) for row in cursor.fetchall()]

        eval_df = m15_df if (m15_df is not None and len(m15_df) > 0) else current_df
        if not trades or eval_df is None or len(eval_df) == 0:
            return resolved_trades

        eval_df = eval_df.copy()
        if "epoch" in eval_df.columns:
            eval_df = eval_df.sort_values("epoch").reset_index(drop=True)
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()

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

            try:
                opened_dt = datetime.fromisoformat(trade["opened_at"])
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
            except Exception:
                opened_dt = now_utc

            opened_ts = opened_dt.timestamp()
            ttl_deadline_ts = opened_ts + (max_pending_hours * 3600.0)
            age_hours = max(0.0, (now_ts - opened_ts) / 3600.0)

            has_aligned_epochs = (
                "epoch" in eval_df.columns
                and len(eval_df) > 0
                and float(eval_df["epoch"].max()) >= (opened_ts - 86400.0)
            )

            if has_aligned_epochs:
                relevant = eval_df[eval_df["epoch"].astype(float) >= (opened_ts - 60.0)]
                if len(relevant) == 0:
                    relevant = eval_df.tail(15)
            else:
                relevant = eval_df.tail(15)

            # Partition aligned candles into valid [opened_at, opened_at + 6h] window vs post-TTL
            expired_early = False
            if status == "PENDING_ENTRY" and not has_aligned_epochs and age_hours >= max_pending_hours:
                reason = f"EXPIRED_STALE ({age_hours:.1f}h >= {max_pending_hours:.0f}h TTL)"
                self.db.mark_trade_expired(t_id, reason=reason)
                logger.info(f"Expired stale pending order {t_id} on {symbol}: {reason}")
                resolved_trades.append({
                    "trade_id": t_id,
                    "symbol": symbol,
                    "outcome": "EXPIRED_STALE",
                    "reason": reason,
                    "realized_pnl": 0.0,
                    "realized_r": 0.0,
                })
                continue

            closed_or_invalidated = False
            for _, candle in relevant.iterrows():
                c_ts = float(candle["epoch"]) if "epoch" in candle.index and candle["epoch"] is not None else opened_ts
                c_high = float(candle["high"])
                c_low = float(candle["low"])

                # 1. PENDING_ENTRY evaluation strictly inside [opened_at, opened_at + 6h TTL]
                if status == "PENDING_ENTRY":
                    if has_aligned_epochs and c_ts > ttl_deadline_ts:
                        # Outside 6h TTL window without ever touching [entry_min, entry_max]
                        reason = f"EXPIRED_STALE ({age_hours:.1f}h >= {max_pending_hours:.0f}h TTL; unfilled inside 6h window)"
                        self.db.mark_trade_expired(t_id, reason=reason)
                        logger.info(f"Expired stale pending order {t_id} on {symbol}: {reason}")
                        resolved_trades.append({
                            "trade_id": t_id,
                            "symbol": symbol,
                            "outcome": "EXPIRED_STALE",
                            "reason": reason,
                            "realized_pnl": 0.0,
                            "realized_r": 0.0,
                        })
                        closed_or_invalidated = True
                        expired_early = True
                        break

                    pre_invalidated = (
                        (direction == "BULLISH" and c_low <= orig_sl)
                        or (direction == "BEARISH" and c_high >= orig_sl)
                    )
                    if pre_invalidated:
                        reason = f"EXPIRED_STALE (pre-entry SL invalidation {orig_sl:.2f} breached)"
                        self.db.mark_trade_expired(t_id, reason=reason)
                        logger.info(f"Cancelled invalidated pending order {t_id} on {symbol}: {reason}")
                        resolved_trades.append({
                            "trade_id": t_id,
                            "symbol": symbol,
                            "outcome": "EXPIRED_STALE",
                            "reason": reason,
                            "realized_pnl": 0.0,
                            "realized_r": 0.0,
                        })
                        closed_or_invalidated = True
                        expired_early = True
                        break

                    # Check limit zone fill strictly inside valid 6h TTL window
                    if c_low <= entry_max and c_high >= entry_min:
                        status = "OPEN"
                        fill_p = (entry_min + entry_max) / 2.0
                        fill_iso = (
                            datetime.fromtimestamp(c_ts, tz=timezone.utc).isoformat()
                            if has_aligned_epochs
                            else now_utc.isoformat()
                        )
                        trade["entry_executed"] = fill_p
                        trade["status"] = "OPEN"
                        with self.db.get_connection() as conn:
                            conn.execute(
                                "UPDATE active_trades SET status = 'OPEN', entry_executed = ? WHERE trade_id = ?",
                                (fill_p, t_id),
                            )
                            conn.execute(
                                """
                                UPDATE paper_trades
                                SET status = 'OPEN', entry_executed = ?, entry_time = ?
                                WHERE trade_id = (
                                    SELECT MAX(trade_id) FROM paper_trades
                                    WHERE symbol = ? AND status = 'PENDING_ENTRY'
                                )
                                """,
                                (fill_p, fill_iso, symbol),
                            )
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
                else:
                    if c_low <= tp_price:
                        hit_tp = True
                        exit_price = tp_price
                    elif c_high >= curr_sl:
                        hit_sl = True
                        exit_price = curr_sl

                if hit_tp or hit_sl:
                    dollar_pnl = self.pip_calc.calculate_pnl(direction, fill_price, exit_price, lot, symbol)
                    r_mult = round((exit_price - fill_price) / sl_dist if direction == "BULLISH" else (fill_price - exit_price) / sl_dist, 2)
                    exit_dt = datetime.fromtimestamp(c_ts, tz=timezone.utc) if has_aligned_epochs else now_utc
                    now_iso = exit_dt.isoformat()

                    self.bankroll.process_trade_result(dollar_pnl, r_mult, symbol)
                    b_status = self.bankroll.get_status()

                    total_fund = trade["total_fund"]
                    after_fund = round(total_fund + dollar_pnl, 2)
                    vault_after = b_status["vault_balance"]

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
                        except Exception as e:
                            logger.warning(f"AFTER screenshot capture failed for {t_id}: {e}")
                            after_screens = {}

                    hold_hours = round(max(0.25, (exit_dt - opened_dt).total_seconds() / 3600.0), 1)

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
                        "what_improved": "Bayesian expectation updated; active filters refined",
                        "before_screenshots": json.loads(trade.get("before_screenshots_json") or "{}"),
                        "after_screenshots": after_screens,
                        "opened_at": trade["opened_at"],
                        "closed_at": now_iso,
                        "hold_duration_hours": hold_hours
                    }

                    target_table = self.db.record_closed_trade(closed_dict)
                    self.db.sync_paper_trade_closure(
                        symbol=symbol,
                        outcome_status="CLOSED_WIN" if dollar_pnl > 0 else "CLOSED_LOSS",
                        exit_price=exit_price,
                        net_pnl=dollar_pnl,
                        realized_r=r_mult,
                        hold_hours=hold_hours,
                        closed_at=now_iso,
                    )

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
                    closed_or_invalidated = True
                    break

            # If all available candles fell inside [opened_at, opened_at + 6h] without filling AND
            # wall-clock time has now passed the 6h TTL deadline, expire stale cleanly
            if not closed_or_invalidated and not expired_early and status == "PENDING_ENTRY" and age_hours >= max_pending_hours:
                reason = f"EXPIRED_STALE ({age_hours:.1f}h >= {max_pending_hours:.0f}h TTL; unfilled inside 6h window)"
                self.db.mark_trade_expired(t_id, reason=reason)
                logger.info(f"Expired stale pending order {t_id} on {symbol}: {reason}")
                resolved_trades.append({
                    "trade_id": t_id,
                    "symbol": symbol,
                    "outcome": "EXPIRED_STALE",
                    "reason": reason,
                    "realized_pnl": 0.0,
                    "realized_r": 0.0,
                })

        return resolved_trades
