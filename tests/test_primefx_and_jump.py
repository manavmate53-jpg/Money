"""
Unit & Integration Tests for PrimeFX Curriculum, Pure Liquidity/POI Engines,
Inducement Trap Gating, Jump Indices Risk Guard, and Anti-Revenge Cooldown.
"""

import json
import os
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import pytest

from src.training.primefx_trainer import PrimeFxTrainer, PrimeFxVideoLessonSchema, PrimeFxMasterPlaybookSchema
from src.technicals.liquidity_engine import LiquidityEngine
from src.technicals.poi_price_action import PoiPriceActionEngine
from src.technicals.confluence_engine import ConfluenceEngine, TradingTip
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.simulator.paper_trader import PaperTrader
from src.council.gemini_council import PostMortemSchema


def test_chronological_video_sorting_oldest_to_latest():
    unsorted = [
        {"id": "newest", "title": "Latest Trade", "timestamp": 1795000000, "upload_date": "2026-11-20", "playlist_index": 1},
        {"id": "oldest", "title": "First Deriv Lesson", "timestamp": 1700000000, "upload_date": "2023-11-14", "playlist_index": 133},
        {"id": "middle", "title": "Jump 50 OB", "timestamp": 1750000000, "upload_date": "2025-06-15", "playlist_index": 60},
    ]
    ordered = PrimeFxTrainer.sort_videos_chronologically(unsorted)
    assert [v["id"] for v in ordered] == ["oldest", "middle", "newest"]
    assert [v["chronological_rank"] for v in ordered] == [1, 2, 3]


def test_primefx_schemas_no_dict_fields():
    for cls in (PrimeFxVideoLessonSchema, PrimeFxMasterPlaybookSchema, PostMortemSchema):
        raw_schema = json.dumps(cls.model_json_schema())
        assert "additionalProperties" not in raw_schema


def test_liquidity_sweep_vs_breakout_classifier():
    liq = LiquidityEngine()
    level = 1000.0

    # Sweep HIGH: wicked above 1000.0 to 1008.0 and closed back inside at 996.0 -> BEARISH_SWEEP
    df_sweep = pd.DataFrame([
        {"epoch": 1, "open": 990, "high": 998, "low": 988, "close": 995},
        {"epoch": 2, "open": 995, "high": 1008, "low": 994, "close": 996},
        {"epoch": 3, "open": 996, "high": 997, "low": 985, "close": 986},
    ])
    res_sweep = liq.classify_sweep_vs_breakout(df_sweep, level=level, side="HIGH")
    assert res_sweep["event"] == "BEARISH_SWEEP"
    assert res_sweep["confirmation_candles"] == 2

    # True Breakout: body closed strongly above 1000.0 and held higher -> BULLISH_BREAKOUT
    df_brk = pd.DataFrame([
        {"epoch": 1, "open": 990, "high": 998, "low": 988, "close": 997},
        {"epoch": 2, "open": 997, "high": 1012, "low": 996, "close": 1010},
        {"epoch": 3, "open": 1010, "high": 1018, "low": 1006, "close": 1016},
    ])
    res_brk = liq.classify_sweep_vs_breakout(df_brk, level=level, side="HIGH")
    assert res_brk["event"] == "BULLISH_BREAKOUT"


def test_inducement_trap_gating_on_poi():
    liq = LiquidityEngine()
    # Bullish POI [100.0, 105.0] at candle idx 1, with an unswept internal pullback low at 110.0 at idx 3
    candles = [
        {"open": 115, "high": 116, "low": 108, "close": 110},  # 0
        {"open": 105, "high": 105, "low": 100, "close": 102},  # 1 (POI zone [100, 105])
        {"open": 102, "high": 120, "low": 102, "close": 118},  # 2 impulse up
        {"open": 118, "high": 119, "low": 114, "close": 115},  # 3
        {"open": 115, "high": 116, "low": 110, "close": 113},  # 4 internal low = 110 (IDM in front of [100,105])
        {"open": 113, "high": 122, "low": 112, "close": 121},  # 5 moves away without sweeping 110
    ]
    df_trap = pd.DataFrame(candles)
    idm_check = liq.check_inducement_for_poi(df_trap, poi_type="BULLISH", zone_bottom=100.0, zone_top=105.0, poi_candle_idx=1)
    assert idm_check["inducement_cleared"] is False
    assert idm_check["status"] == "TRAP_PRE_INDUCEMENT"
    assert idm_check["idm_level"] == 110.0

    # Once price sweeps below IDM 110.0 to 107.0, inducement is cleared
    df_cleared = pd.DataFrame(candles + [
        {"open": 121, "high": 121, "low": 107, "close": 108},
    ])
    idm_ok = liq.check_inducement_for_poi(df_cleared, poi_type="BULLISH", zone_bottom=100.0, zone_top=105.0, poi_candle_idx=1)
    assert idm_ok["inducement_cleared"] is True
    assert idm_ok["status"] == "IDM_SWEPT_CONFIRMED"


def test_jump_index_specs_and_sl_volatility_guard():
    calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(calc, max_risk_percent=1.5, hard_cap_percent=2.0)

    # JD10 at 89943 with 300-pt stop & 0.01 min lot = $3.00 risk > $2.00 -> disqualified on $100
    v_jd10 = sizer.evaluate_instrument_viability("JD10", 89943.0, 89643.0, account_equity=100.0)
    assert v_jd10["viable"] is False

    # JD75 at 3918 with 80-pt stop & 0.01 min lot = $0.80 <= $2.00 -> viable on $100
    v_jd75 = sizer.evaluate_instrument_viability("JD75", 3918.0, 3838.0, account_equity=100.0)
    assert v_jd75["viable"] is True
    assert v_jd75["actual_dollar_risk"] <= 2.00

    # JD100 at 47.32 with 5-pt stop & 0.01 min lot -> viable on $100
    v_jd100 = sizer.evaluate_instrument_viability("JD100", 47.32, 42.32, account_equity=100.0)
    assert v_jd100["viable"] is True

    # Verify ConfluenceEngine enforces >= 1.5*ATR_14 SL guard on Jump Index
    rows = []
    for i in range(50):
        c_open = 3900.0 + i * 2.5 + 4.0 * np.sin(i * 0.6)
        c_close = c_open + 1.5
        rows.append({"epoch": 1700000000 + i * 3600, "open": c_open, "high": max(c_open, c_close) + 6.0, "low": min(c_open, c_close) - 6.0, "close": c_close, "volume": 100.0})
    df_jd75 = pd.DataFrame(rows)
    engine = ConfluenceEngine()
    atr = float(engine.pa.calculate_atr(df_jd75).iloc[-1])
    tip = engine.generate_tip("JD75", df_jd75, macro_bias="BULLISH")
    if tip.direction == "BULLISH":
        assert tip.sl_points >= round(1.4 * atr, 2)


def test_anti_revenge_2h_cooldown_blocks_reentry(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "revenge.db"))
    bankroll = BankrollManager(db)
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc)
    filters_file = os.path.join(tmp_path, "filters.json")
    with open(filters_file, "w") as f:
        json.dump({"revenge_cooldown_hours": 2.0, "max_pending_hours": 6}, f)

    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer, filters_path=filters_file)

    # Record recent loss 30 minutes ago on STEPINDEX
    thirty_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    db.record_closed_trade({
        "trade_id": "TRD-RECENT-LOSS",
        "epoch_id": 1,
        "symbol": "STEPINDEX",
        "direction": "BULLISH",
        "entry_price": 7350.0,
        "exit_price": 7340.0,
        "tp_price": 7375.0,
        "sl_price": 7340.0,
        "lot_size": 0.10,
        "rr_realized": -1.0,
        "net_pnl": -1.00,
        "opened_at": thirty_mins_ago,
        "closed_at": thirty_mins_ago,
    })

    # Verify DB cooldown age
    hrs = db.get_hours_since_last_loss("STEPINDEX")
    assert hrs is not None and hrs < 2.0

    # PaperTrader must refuse immediate revenge entry on STEPINDEX
    tip = TradingTip(
        symbol="STEPINDEX", direction="BULLISH", confidence_score=0.90,
        primary_strategy="SMC_ICT", market_regime="DIRECTIONAL_TREND",
        current_price=7345.0, entry_min=7342.0, entry_max=7345.0,
        stop_loss=7335.0, take_profit_1=7370.0, take_profit_2=7385.0,
        sl_points=10.0, tp1_points=25.0, risk_reward_ratio=2.5,
        break_even_trigger_price=7355.0, estimated_hold_hours=2.0,
        estimated_hold_candles=2, confluence_reasons=["Test revenge entry"],
    )
    blocked_id = trader.submit_tip(tip, lot_size=0.10, dollar_risk=1.00, margin_req=0.73)
    assert blocked_id is None
    assert len(db.get_active_trades()) == 0
