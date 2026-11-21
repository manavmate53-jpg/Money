"""
Unit tests for deterministic Technical Analysis engines:
- SMC / ICT
- Supply & Demand
- Price Action & Auction Market Theory
"""

import pandas as pd
import numpy as np
import pytest

from src.technicals.smc_ict import SmcIctEngine
from src.technicals.supply_demand import SupplyDemandEngine
from src.technicals.price_action import PriceActionEngine
from src.technicals.confluence_engine import ConfluenceEngine


def generate_mock_trend_df(bullish: bool = True, n_candles: int = 50) -> pd.DataFrame:
    records = []
    base = 1000.0
    step = 3.0 if bullish else -3.0

    for i in range(n_candles):
        # Add sinusoidal wave oscillation to create genuine swing peaks and troughs
        wave = 8.0 * np.sin(i * 0.7)
        c_open = base + (i * step) + wave
        c_close = c_open + (step * 0.5)
        c_high = max(c_open, c_close) + 3.0
        c_low = min(c_open, c_close) - 3.0
        records.append({
            "epoch": 1700000000 + (i * 3600),
            "open": c_open,
            "high": c_high,
            "low": c_low,
            "close": c_close,
            "volume": 100.0
        })
    return pd.DataFrame(records)


def test_smc_swing_points_and_trend():
    engine = SmcIctEngine(swing_length=2)
    bullish_df = generate_mock_trend_df(bullish=True, n_candles=40)
    structure = engine.detect_structure(bullish_df)
    assert structure["trend"] == "BULLISH"
    assert structure["last_swing_high"] > structure["prior_swing_high"]


def test_smc_fvg_detection():
    engine = SmcIctEngine()
    # Create deliberate 3-candle bullish FVG:
    # C1: High 100.0
    # C2: Big impulse
    # C3: Low 105.0 (Gap between 100.0 and 105.0)
    df = pd.DataFrame([
        {"open": 95, "high": 100, "low": 94, "close": 99, "epoch": 1},
        {"open": 99, "high": 110, "low": 98, "close": 109, "epoch": 2},
        {"open": 109, "high": 115, "low": 105, "close": 114, "epoch": 3},
        {"open": 114, "high": 116, "low": 112, "close": 115, "epoch": 4}
    ])
    fvgs = engine.detect_fair_value_gaps(df)
    assert len(fvgs) >= 1
    bull_fvg = next(f for f in fvgs if f["type"] == "BULLISH_FVG")
    assert bull_fvg["gap_low"] == 100
    assert bull_fvg["gap_high"] == 105
    assert bull_fvg["consequent_encroachment"] == 102.5


def test_supply_demand_dbr_detection():
    sd_engine = SupplyDemandEngine(atr_period=5, min_impulse_multiplier=1.5)
    # Construct Drop-Base-Rally pattern
    candles = [
        {"open": 200, "high": 202, "low": 180, "close": 182, "epoch": 1}, # Drop
        {"open": 182, "high": 184, "low": 181, "close": 183, "epoch": 2}, # Base
        {"open": 183, "high": 210, "low": 182, "close": 208, "epoch": 3}, # Rally Impulse
        {"open": 208, "high": 212, "low": 205, "close": 210, "epoch": 4}
    ]
    # Pad with earlier stable candles to seed ATR
    prefix = [{"open": 195, "high": 197, "low": 193, "close": 195, "epoch": 0 - k} for k in range(10, 0, -1)]
    df = pd.DataFrame(prefix + candles)
    analysis = sd_engine.analyze(df)
    assert len(analysis["all_zones"]) >= 1


def test_price_action_one_time_framing():
    pa_engine = PriceActionEngine()
    df = generate_mock_trend_df(bullish=True, n_candles=10)
    otf = pa_engine.check_one_time_framing(df, bars_back=3)
    assert otf["status"] == "BULLISH_ONE_TIME_FRAMING"


def test_confluence_engine_tip():
    confluence = ConfluenceEngine()
    df = generate_mock_trend_df(bullish=True, n_candles=60)
    tip = confluence.generate_tip("1HZ25V", df)
    assert tip.symbol == "1HZ25V"
    assert tip.direction in ["BULLISH", "BEARISH", "NEUTRAL_NO_TRADE"]
    assert tip.confidence_score >= 0.0
    assert tip.estimated_hold_hours > 0.0
