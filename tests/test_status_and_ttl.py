"""
Unit & CLI Integration Tests for Task 1 (5-Table Markdown Status Reporter)
and Task 2 (Delayed-Cron 6h TTL Intra-Window M15 Order Fill Evaluation).
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
import pandas as pd
import pytest

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.simulator.paper_trader import PaperTrader
from src.reporting.status_table import render_status_tables


def test_status_table_outputs_all_5_tables_and_placeholder(tmp_path):
    db_file = os.path.join(tmp_path, "status.db")
    macro_file = os.path.join(tmp_path, "macro_bias.json")
    filters_file = os.path.join(tmp_path, "active_filters.json")
    advice_file = os.path.join(tmp_path, "LATEST_ADVICE.md")
    dash_file = os.path.join(tmp_path, "TRAINING_DASHBOARD.md")

    db = DatabaseManager(db_file)
    BankrollManager(db, policy_path="config/bankroll_policy.json")
    db.record_closed_trade({
        "trade_id": "TRD-TEST-LOSS-1",
        "epoch_id": 1,
        "symbol": "1HZ75V",
        "direction": "BULLISH",
        "entry_price": 700.0,
        "exit_price": 650.0,
        "tp_price": 825.0,
        "sl_price": 650.0,
        "lot_size": 0.025,
        "rr_planned": 2.5,
        "rr_realized": -1.0,
        "total_fund": 100.0,
        "used_fund": 1.50,
        "net_pnl": -1.30,
        "after_trade_fund": 98.70,
        "vault_locked_after": 0.0,
        "what_improved": "Require 2 confirmation candles post-sweep",
    })

    with open(macro_file, "w") as f:
        json.dump({
            "instruments": {
                "STEPINDEX": {
                    "macro_bias": "BULLISH",
                    "confidence_score": 0.88,
                    "primary_regime": "DIRECTIONAL_TREND",
                    "nano_lot_viable": True,
                    "key_invalidation_level": 7372.4,
                    "macro_target": 7460.0,
                }
            }
        }, f)
    with open(filters_file, "w") as f:
        json.dump({"version": "2.0.0"}, f)
    with open(advice_file, "w") as f:
        f.write(
            "- **Instrument**: **`STEPINDEX`**\n"
            "- **Directional Bias**: **BULLISH**\n"
            "- **Primary Strategy**: `SMC_ICT`\n"
            "| **Recommended Lot Size** | **`0.15 lots`** | note |\n"
            "| **Dollar Risk ($)** | **`$1.49`** | **1.49%** of $100 |\n"
            "| **Entry Zone** | **`7352.00 - 7355.50`** | note |\n"
            "| **Stop Loss (SL)** | **`7345.57`** | note |\n"
            "| **Take Profit 1 (TP1)** | **`7380.33`** | note |\n"
            "| **Cover-Risk-to-Zero** | **`7365.43 (+1.0R)`** | note |\n"
            "| **Estimated Hold Time** | **2.1 hours (2 bars)** | note |\n"
        )

    out = render_status_tables(
        fetch_remote_ci=False,
        db_path=db_file,
        macro_bias_path=macro_file,
        active_filters_path=filters_file,
        latest_advice_path=advice_file,
        dashboard_path=dash_file,
    )

    assert "1. Bankroll, Calibration & Risk Scoreboard" in out
    assert "2. Active & Recent Orders" in out
    assert "3. Closed Trade Ledgers" in out
    assert "4. 4H Macro Regime & $100 Account Viability" in out
    assert "5. Latest $100 Capital Advice & GitHub Actions Health" in out
    assert "5A. Latest $100 Setup" in out
    assert "5B. GitHub Actions Desks" in out
    assert "| None yet | PROFIT | - | - | - | - | - | - | - | - | - | Awaiting first closed winner |" in out
    assert "TRD-TEST-LOSS-1" in out
    assert "STEPINDEX" in out

    # Also verify CLI execution: python3 main.py --cycle status
    proc = subprocess.run(
        [sys.executable, "main.py", "--cycle", "status"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0
    assert "| Metric | Live Value | Target / Limit | Status |" in proc.stdout
    assert "| Workflow Desk | Last Run (UTC) | Conclusion | Commit |" in proc.stdout


def test_delayed_cron_ttl_fills_inside_6h_and_expires_untouched(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "delayed_ttl.db"))
    bankroll = BankrollManager(db, policy_path="config/bankroll_policy.json")
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc)
    filters_file = os.path.join(tmp_path, "active_filters.json")
    with open(filters_file, "w") as f:
        json.dump({"max_pending_hours": 6}, f)
    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer, filters_path=filters_file)

    now_utc = datetime.now(timezone.utc)
    opened_dt = now_utc - timedelta(hours=10)
    opened_ts = opened_dt.timestamp()

    # Case 1: Delayed cron runs 10h after opened_at, but M15 candle at +3.0h entered [7350, 7355]
    # and hit TP1=7385.0 at +6.5h -> MUST resolve as WIN inside 6h TTL, NOT EXPIRED_STALE!
    db.record_active_trade({
        "trade_id": "TRD-DELAYED-FILL",
        "epoch_id": 1,
        "symbol": "STEPINDEX",
        "direction": "BULLISH",
        "strategy_framework": "SMC_ICT",
        "entry_min": 7350.0,
        "entry_max": 7355.0,
        "tp_price": 7385.0,
        "sl_price": 7340.0,
        "original_sl": 7340.0,
        "lot_size": 0.12,
        "dollar_risk": 1.50,
        "margin_required": 0.88,
        "total_fund": 100.0,
        "used_fund": 2.38,
        "status": "PENDING_ENTRY",
        "opened_at": opened_dt.isoformat(),
    })

    m15_rows = []
    for bar_idx in range(41):  # 0h to +10h in 15m steps
        ep = opened_ts + (bar_idx * 900)
        if bar_idx < 12:
            # 0h .. +2.75h: price hovers above entry zone (unfilled)
            o, h, l, c = 7365.0, 7370.0, 7362.0, 7366.0
        elif bar_idx == 12:
            # +3.0h: inside 6h TTL window, price taps entry zone [7350, 7355] without hitting SL=7340
            o, h, l, c = 7362.0, 7364.0, 7352.0, 7358.0
        elif bar_idx < 26:
            # +3.25h .. +6.25h: rallies through +1.0R BE (7365) without hitting TP yet
            o, h, l, c = 7358.0, 7376.0, 7356.0, 7374.0
        elif bar_idx == 26:
            # +6.5h: reaches TP1=7385.0 cleanly
            o, h, l, c = 7374.0, 7388.0, 7373.0, 7386.0
        else:
            o, h, l, c = 7386.0, 7390.0, 7382.0, 7385.0
        m15_rows.append({"epoch": ep, "open": o, "high": h, "low": l, "close": c, "volume": 100.0})

    res_win = trader.update_open_positions("STEPINDEX", pd.DataFrame(m15_rows), m15_df=pd.DataFrame(m15_rows))
    assert len(res_win) == 1
    assert res_win[0]["outcome"] == "WIN"
    assert res_win[0]["realized_pnl"] > 0
    assert len(db.get_profitable_trades()) == 1

    # Case 2: Touches entry zone ONLY AFTER +6h TTL (+7.0h) -> MUST expire EXPIRED_STALE
    db.record_active_trade({
        "trade_id": "TRD-LATE-UNFILLED",
        "epoch_id": 1,
        "symbol": "JD100",
        "direction": "BEARISH",
        "strategy_framework": "SMC_ICT",
        "entry_min": 36.92,
        "entry_max": 37.72,
        "tp_price": 33.47,
        "sl_price": 38.30,
        "original_sl": 38.30,
        "lot_size": 1.0,
        "dollar_risk": 1.40,
        "margin_required": 0.04,
        "total_fund": 100.0,
        "used_fund": 1.44,
        "status": "PENDING_ENTRY",
        "opened_at": opened_dt.isoformat(),
    })

    jd_rows = []
    for bar_idx in range(41):
        ep = opened_ts + (bar_idx * 900)
        if bar_idx <= 24:
            # 0h .. +6.0h: stays well below entry [36.92, 37.72] -> NEVER fills in 6h TTL
            o, h, l, c = 35.5, 36.2, 35.1, 35.8
        else:
            # +6.25h .. +10h: rallies into old zone too late (past 6h TTL)
            o, h, l, c = 36.8, 37.5, 36.7, 37.2
        jd_rows.append({"epoch": ep, "open": o, "high": h, "low": l, "close": c, "volume": 100.0})

    res_exp = trader.update_open_positions("JD100", pd.DataFrame(jd_rows), m15_df=pd.DataFrame(jd_rows))
    assert len(res_exp) == 1
    assert res_exp[0]["outcome"] == "EXPIRED_STALE"
    assert len(db.get_active_trades()) == 0
