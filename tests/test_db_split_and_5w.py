"""
Unit tests for Separated profitable_trades & losing_trades SQLite tables,
complete fund accounting, 5W schema, and visual trade journal exports.
"""

import os
import json
import pytest
from src.database.db_manager import DatabaseManager


@pytest.fixture
def clean_db(tmp_path):
    db_file = os.path.join(tmp_path, "test_quant_state.db")
    db = DatabaseManager(db_file)
    return db, str(tmp_path)


def test_separate_table_routing_and_fund_accounting(clean_db):
    db, tmp_dir = clean_db

    # 1. Record an active trade
    trade_open = {
        "trade_id": "TRD-001",
        "epoch_id": 1,
        "symbol": "1HZ25V",
        "direction": "BULLISH",
        "timeframes_used": "H4,H1,M15",
        "strategy_framework": "SMC_ICT",
        "entry_min": 2800.0,
        "entry_max": 2810.0,
        "entry_executed": 2805.0,
        "tp_price": 2855.0,
        "sl_price": 2785.0,
        "original_sl": 2785.0,
        "lot_size": 0.05,
        "dollar_risk": 1.00,
        "margin_required": 0.14,
        "total_fund": 100.00,
        "used_fund": 1.14,
        "status": "OPEN",
        "before_screenshots": {"H4": "charts/TRD-001/BEFORE_H4.png", "H1": "charts/TRD-001/BEFORE_H1.png"},
        "trade_reason_summary": "1HZ25V Demand retest + MSS on M15",
        "w1_who": "SMC_ICT Engine",
        "w2_what": "1HZ25V BULLISH 0.05 lots",
        "w3_when": "2026-11-22T00:00:00Z",
        "w4_where": "H1 Demand [2785 - 2810]",
        "w5_why": "Liquidity sweep of Sell-Side resting stops",
        "opened_at": "2026-11-22T00:00:00Z"
    }
    db.record_active_trade(trade_open)

    active_list = db.get_active_trades()
    assert len(active_list) == 1
    assert active_list[0]["trade_id"] == "TRD-001"

    # 2. Close Trade as a WIN (net_pnl = +$2.50)
    closed_win = {
        "trade_id": "TRD-001",
        "epoch_id": 1,
        "symbol": "1HZ25V",
        "direction": "BULLISH",
        "timeframes_used": "H4,H1,M15",
        "strategy_framework": "SMC_ICT",
        "entry_price": 2805.0,
        "exit_price": 2855.0,
        "tp_price": 2855.0,
        "sl_price": 2785.0,
        "lot_size": 0.05,
        "rr_planned": 2.5,
        "rr_realized": 2.5,
        "total_fund": 100.00,
        "used_fund": 1.14,
        "net_pnl": 2.50,
        "after_trade_fund": 102.50,
        "vault_locked_after": 0.0,
        "trade_reason_summary": "Hit TP1 cleanly with strong displacement",
        "w1_who": "SMC_ICT Engine",
        "w2_what": "1HZ25V BULLISH 0.05 lots",
        "w3_when": "Held 3.5 hours",
        "w4_where": "H1 Demand",
        "w5_why": "Bullish FVG filled",
        "what_improved": "Increased SMC_ICT weight on 1HZ25V",
        "before_screenshots": {"H1": "data/trade_charts/TRD-001/BEFORE_H1.png"},
        "after_screenshots": {"H1": "data/trade_charts/TRD-001/AFTER_H1.png"},
        "opened_at": "2026-11-22T00:00:00Z",
        "closed_at": "2026-11-22T03:30:00Z",
        "hold_duration_hours": 3.5
    }

    target_table = db.record_closed_trade(closed_win)
    assert target_table == "profitable_trades"

    # Verify trade was removed from active_trades and routed to profitable_trades
    assert len(db.get_active_trades()) == 0
    wins = db.get_profitable_trades()
    assert len(wins) == 1
    assert wins[0]["trade_id"] == "TRD-001"
    assert wins[0]["net_pnl"] == 2.50
    assert wins[0]["after_trade_fund"] == 102.50
    assert wins[0]["what_improved"] == "Increased SMC_ICT weight on 1HZ25V"

    # losing_trades MUST be empty
    assert len(db.get_losing_trades()) == 0


def test_loss_routing_and_5w_export(clean_db):
    db, tmp_dir = clean_db

    # Record a losing trade (net_pnl = -$1.40)
    closed_loss = {
        "trade_id": "TRD-002",
        "epoch_id": 1,
        "symbol": "1HZ50V",
        "direction": "BEARISH",
        "timeframes_used": "H4,H1,M15",
        "strategy_framework": "PRICE_ACTION",
        "entry_price": 1450.0,
        "exit_price": 1478.0,
        "tp_price": 1390.0,
        "sl_price": 1478.0,
        "lot_size": 0.05,
        "rr_planned": 2.14,
        "rr_realized": -1.0,
        "total_fund": 102.50,
        "used_fund": 1.40,
        "net_pnl": -1.40,
        "after_trade_fund": 101.10,
        "vault_locked_after": 0.0,
        "trade_reason_summary": "Range deviation failed; broke above Range High",
        "w1_who": "Price Action Engine",
        "w2_what": "1HZ50V BEARISH 0.05 lots",
        "w3_when": "Held 1.2 hours",
        "w4_where": "Range High 1470",
        "w5_why": "Stop triggered at 1478 invalidation",
        "what_improved": "Penalized counter-trend range fades during expansion",
        "before_screenshots": {"H1": "data/trade_charts/TRD-002/BEFORE_H1.png"},
        "after_screenshots": {"H1": "data/trade_charts/TRD-002/AFTER_H1.png"},
        "opened_at": "2026-11-22T04:00:00Z",
        "closed_at": "2026-11-22T05:12:00Z",
        "hold_duration_hours": 1.2
    }

    target = db.record_closed_trade(closed_loss)
    assert target == "losing_trades"

    losses = db.get_losing_trades()
    assert len(losses) == 1
    assert losses[0]["trade_id"] == "TRD-002"
    assert losses[0]["net_pnl"] == -1.40
    assert losses[0]["what_improved"] == "Penalized counter-trend range fades during expansion"

    # Verify unified UNION ALL query across wins + losses
    resolved = db.get_resolved_trades(epoch_id=1)
    assert len(resolved) == 1
    assert resolved[0]["trade_id"] == "TRD-002"
    assert resolved[0]["outcome"] == "LOSS"

    # Verify mark_trade_expired removes stale pending order cleanly without touching losses
    db.record_active_trade({
        "trade_id": "TRD-STALE",
        "epoch_id": 1,
        "symbol": "1HZ25V",
        "direction": "BULLISH",
        "entry_min": 2800.0,
        "entry_max": 2810.0,
        "tp_price": 2850.0,
        "sl_price": 2780.0,
        "original_sl": 2780.0,
        "lot_size": 0.005,
        "dollar_risk": 0.5,
        "margin_required": 0.1,
        "total_fund": 100.0,
        "used_fund": 0.6,
        "status": "PENDING_ENTRY",
        "opened_at": "2026-11-22T00:00:00+00:00"
    })
    assert db.mark_trade_expired("TRD-STALE", "TTL exceeded") is True
    assert len(db.get_active_trades()) == 0
    assert len(db.get_resolved_trades(epoch_id=1)) == 1

    # Export ledgers to temp dir and verify Markdown files exist
    reports_dir = os.path.join(tmp_dir, "reports")
    db.export_all_ledgers(reports_dir)

    assert os.path.exists(os.path.join(reports_dir, "PROFIT_LEDGER.md"))
    assert os.path.exists(os.path.join(reports_dir, "LOSS_LEDGER.md"))
    assert os.path.exists(os.path.join(reports_dir, "TRADE_VISUAL_JOURNAL.md"))

    # Verify content in LOSS_LEDGER.md
    with open(os.path.join(reports_dir, "LOSS_LEDGER.md")) as f:
        loss_text = f.read()
    assert "TRD-002" in loss_text
    assert "Penalized counter-trend" in loss_text
