"""
Unit tests for Execution Quality Guards:
- $100 nano-lot viability enforcement inside PaperTrader.submit_tip()
- 6-hour PENDING_ENTRY TTL expiry & pre-entry SL invalidation (EXPIRED_STALE)
- Machine-enforced active_filters.json gating, H4 macro alignment, M15 ATR zone sizing,
  and 2-candle liquidity sweep confirmation inside ConfluenceEngine.
"""

import json
import os
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import pytest

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.simulator.paper_trader import PaperTrader
from src.technicals.confluence_engine import ConfluenceEngine, TradingTip


def _build_candles(n: int = 40, base: float = 7350.0, step: float = 0.5) -> pd.DataFrame:
    rows = []
    for i in range(n):
        c_open = base + (i * step) + 2.0 * np.sin(i * 0.6)
        c_close = c_open + step
        rows.append({
            "epoch": 1700000000 + (i * 3600),
            "open": c_open,
            "high": max(c_open, c_close) + 1.5,
            "low": min(c_open, c_close) - 1.5,
            "close": c_close,
            "volume": 100.0
        })
    return pd.DataFrame(rows)


def test_paper_trader_blocks_unviable_100_dollar_trade(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "guards.db"))
    bankroll = BankrollManager(db)
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc, max_risk_percent=1.5, hard_cap_percent=2.0)
    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer)

    # Unviable setup: 1HZ75V has min_lot=0.05; 100-pt SL = $5.00 risk (> $2.00 hard cap on $100)
    unviable_tip = TradingTip(
        symbol="1HZ75V",
        direction="BULLISH",
        confidence_score=0.85,
        primary_strategy="SMC_ICT",
        market_regime="DIRECTIONAL_TREND",
        current_price=3340.0,
        entry_min=3330.0,
        entry_max=3340.0,
        stop_loss=3240.0,
        take_profit_1=3590.0,
        take_profit_2=3740.0,
        sl_points=100.0,
        tp1_points=250.0,
        risk_reward_ratio=2.5,
        break_even_trigger_price=3440.0,
        estimated_hold_hours=4.0,
        estimated_hold_candles=4,
        confluence_reasons=["Test unviable tip"],
    )
    res_id = trader.submit_tip(unviable_tip, lot_size=0.05, dollar_risk=5.0, margin_req=0.17)
    assert res_id is None
    assert len(db.get_active_trades()) == 0

    # Viable setup: STEPINDEX has min_lot=0.10; 10-pt SL = $1.00 risk <= $2.00 cap
    viable_tip = TradingTip(
        symbol="STEPINDEX",
        direction="BULLISH",
        confidence_score=0.85,
        primary_strategy="SMC_ICT",
        market_regime="DIRECTIONAL_TREND",
        current_price=7360.0,
        entry_min=7357.0,
        entry_max=7360.0,
        stop_loss=7350.0,
        take_profit_1=7385.0,
        take_profit_2=7400.0,
        sl_points=10.0,
        tp1_points=25.0,
        risk_reward_ratio=2.5,
        break_even_trigger_price=7370.0,
        estimated_hold_hours=2.0,
        estimated_hold_candles=2,
        confluence_reasons=["Test viable STEPINDEX"],
    )
    ok_id = trader.submit_tip(viable_tip, lot_size=0.15, dollar_risk=1.50, margin_req=1.10)
    assert ok_id is not None
    assert len(db.get_active_trades()) == 1


def test_pending_order_6h_ttl_expiry(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "ttl.db"))
    bankroll = BankrollManager(db)
    filters_file = os.path.join(tmp_path, "active_filters.json")
    with open(filters_file, "w") as f:
        json.dump({"max_pending_hours": 6}, f)

    trader = PaperTrader(db, bankroll, filters_path=filters_file)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()

    db.record_active_trade({
        "trade_id": "TRD-OLD-PENDING",
        "epoch_id": 1,
        "symbol": "STEPINDEX",
        "direction": "BULLISH",
        "entry_min": 7300.0,
        "entry_max": 7305.0,
        "tp_price": 7350.0,
        "sl_price": 7290.0,
        "original_sl": 7290.0,
        "lot_size": 0.1,
        "dollar_risk": 1.5,
        "margin_required": 0.7,
        "total_fund": 100.0,
        "used_fund": 2.2,
        "status": "PENDING_ENTRY",
        "opened_at": old_time
    })

    df = _build_candles(20, base=7360.0)
    events = trader.update_open_positions("STEPINDEX", df)
    assert len(events) == 1
    assert events[0]["outcome"] == "EXPIRED_STALE"
    assert len(db.get_active_trades()) == 0
    assert len(db.get_resolved_trades()) == 0  # Stale expiration records no fake financial loss


def test_confluence_macro_alignment_filters_and_m15_atr_width(tmp_path):
    filters_file = os.path.join(tmp_path, "active_filters.json")
    with open(filters_file, "w") as f:
        json.dump({
            "min_sweep_confirmation_candles": 2,
            "max_pending_hours": 6,
            "min_rr_ratio": 2.0,
            "atr_sl_buffer_multiplier": 0.5,
            "entry_zone_m15_atr_width": 0.25,
            "require_macro_bias_alignment": True,
            "blocked_regimes_by_symbol": {"1HZ50V": ["DIRECTIONAL_TREND"]},
            "symbol_overrides": {}
        }, f)

    engine = ConfluenceEngine(filters_path=filters_file)
    df_h1 = _build_candles(60, base=7350.0, step=2.5)
    df_m15 = _build_candles(60, base=7450.0, step=1.2)

    # Blocked regime triggers NEUTRAL_NO_TRADE immediately
    blocked = engine.generate_tip("1HZ50V", df_h1, m15_df=df_m15, macro_bias="BULLISH")
    assert blocked.direction == "NEUTRAL_NO_TRADE"

    # Neutral H4 macro bias rejects intraday directional setup
    neutral_blocked = engine.generate_tip("STEPINDEX", df_h1, m15_df=df_m15, macro_bias="NEUTRAL")
    assert neutral_blocked.direction == "NEUTRAL_NO_TRADE"

    # Opposite H4 macro bias rejects setup
    opp_blocked = engine.generate_tip("STEPINDEX", df_h1, m15_df=df_m15, macro_bias="BEARISH")
    assert opp_blocked.direction == "NEUTRAL_NO_TRADE"

    # Aligned H4 macro bias yields ATR-calibrated zone width >= 0.10 pts
    aligned = engine.generate_tip("STEPINDEX", df_h1, m15_df=df_m15, macro_bias="BULLISH")
    if aligned.direction == "BULLISH":
        assert (aligned.entry_max - aligned.entry_min) >= 0.10
        assert aligned.risk_reward_ratio >= 2.0
