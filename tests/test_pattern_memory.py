"""
Unit Tests for Phase 2 k-NN Pattern Memory & Unpredictable-Move Volatility Circuit Breaker.
"""

import os
import numpy as np
import pandas as pd
import pytest

from src.database.db_manager import DatabaseManager
from src.technicals.pattern_memory import PatternMemoryEngine, FEATURE_NAMES
from src.technicals.confluence_engine import ConfluenceEngine


def _make_ohlc(n: int = 40, base: float = 2800.0, amplitude: float = 4.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        c_open = base + i * 1.5 + amplitude * np.sin(i * 0.5)
        c_close = c_open + 1.2
        rows.append({
            "epoch": 1700000000 + i * 3600,
            "open": c_open,
            "high": max(c_open, c_close) + 2.0,
            "low": min(c_open, c_close) - 2.0,
            "close": c_close,
            "volume": 100.0,
        })
    return pd.DataFrame(rows)


def test_knn_8_feature_vector_and_similarity_blocking(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "knn.db"))
    pm = PatternMemoryEngine(db=db, k=20, min_win_rate=0.42, min_avg_r=0.15, min_analogs_required=10)

    df = _make_ohlc(50)
    vec = pm.extract_feature_vector(
        df_h1=df,
        direction="BULLISH",
        htf_trend="BULLISH",
        sweep_info={"level": 2840.0, "wick_extreme": 2835.0},
        poi_info={"poi_kind": "BULLISH_OB", "inducement_cleared": True, "distance_atr": 0.4},
        planned_rr=2.5,
    )
    assert len(vec) == len(FEATURE_NAMES) == 8

    # Seed 20 nearby winning analogs & 20 distant losing analogs
    for i in range(20):
        db.add_pattern_analog({
            "symbol": "1HZ25V",
            "direction": "BULLISH",
            "timestamp": f"2026-10-01T{i:02d}:00:00Z",
            "features": [1.0, 0.8 + i * 0.01, 1.0, 1.0, 0.4, vec[5], vec[6], 2.5],
            "outcome_r": 2.5 if i % 4 != 0 else -1.0,  # 75% win rate, +1.625R avg
            "is_win": 1 if i % 4 != 0 else 0,
            "source": "TEST_SEED",
        })
        db.add_pattern_analog({
            "symbol": "R_75",
            "direction": "BEARISH",
            "timestamp": f"2026-10-02T{i:02d}:00:00Z",
            "features": [-1.0, 0.0, 0.0, 0.0, 4.5, 0.99, 0.50, 1.5],
            "outcome_r": -1.0,
            "is_win": 0,
            "source": "TEST_SEED",
        })

    pass_eval = pm.evaluate_similarity(vec, symbol="1HZ25V")
    assert pass_eval["passed"] is True
    assert pass_eval["k_neighbors"] == 20
    assert pass_eval["analog_win_rate"] >= 0.42
    assert pass_eval["analog_avg_r"] > 0.15

    # Query near the losing cluster -> must block
    bad_vec = [-1.0, 0.0, 0.0, 0.0, 4.5, 0.99, 0.50, 1.5]
    fail_eval = pm.evaluate_similarity(bad_vec, symbol="R_75")
    assert fail_eval["passed"] is False
    assert "k-NN Analog Gate" in fail_eval["reason"]


def test_volatility_circuit_breaker_2_h1_candles(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "shock.db"))
    pm = PatternMemoryEngine(db=db, spike_atr_mult=3.0, pause_candles=2)

    df_normal = _make_ohlc(35, base=3900.0, amplitude=2.0)
    res_ok = pm.check_volatility_circuit_breaker("JD75", df_normal)
    assert res_ok["paused"] is False
    assert res_ok["status"] == "NORMAL"

    # Inject erratic 6x ATR_14 shock bar at the latest candle -> Pause for 2 H1 candles
    df_shock = df_normal.copy()
    atr_base = float(pm._compute_tr_series(df_normal).rolling(14).mean().iloc[-1])
    last_idx = df_shock.index[-1]
    df_shock.loc[last_idx, "high"] = df_shock.loc[last_idx, "open"] + (6.0 * atr_base)
    df_shock.loc[last_idx, "low"] = df_shock.loc[last_idx, "open"] - (1.0 * atr_base)

    res_shock = pm.check_volatility_circuit_breaker("JD75", df_shock)
    assert res_shock["paused"] is True
    assert res_shock["status"] == "VOLATILITY_SHOCK_PAUSE"
    assert res_shock["candles_remaining"] == 2

    # ConfluenceEngine must immediately halt new entries on JD75 during shock pause
    filters_file = os.path.join(tmp_path, "active_filters.json")
    with open(filters_file, "w") as f:
        f.write('{"require_macro_bias_alignment": false}')
    engine = ConfluenceEngine(filters_path=filters_file, pattern_memory=pm)
    tip = engine.generate_tip("JD75", df_shock)
    assert tip.direction == "NEUTRAL_NO_TRADE"
    assert any("VOLATILITY_SHOCK_PAUSE" in r for r in tip.confluence_reasons)

    # After 2 normal H1 candles elapse past the shock bar, circuit breaker clears
    df_recovered = pd.concat([df_shock, _make_ohlc(3, base=3955.0, amplitude=1.0).assign(
        epoch=[df_shock["epoch"].iloc[-1] + 3600 * k for k in (1, 2, 3)]
    )], ignore_index=True)
    res_clear = pm.check_volatility_circuit_breaker("JD75", df_recovered)
    assert res_clear["paused"] is False
    assert res_clear["status"] == "NORMAL"
