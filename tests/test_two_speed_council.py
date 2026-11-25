"""
Unit Tests for Phase 3 Two-Speed Gemini Council Adaptation (n >= 5 Hard Filters),
Strict $40 Max-Drawdown ($60 Hard Equity Floor) Circuit Breaker, and 1 Concurrent Trade Limit.
"""

import json
import os
import pytest

from src.database.db_manager import DatabaseManager
from src.risk.bankroll_manager import BankrollManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.simulator.paper_trader import PaperTrader
from src.council.gemini_council import GeminiCouncil
from src.council.post_mortem import PostMortemManager
from src.technicals.confluence_engine import TradingTip


def test_two_speed_council_weights_vs_n5_hard_filters(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "two_speed.db"))
    weights_file = os.path.join(tmp_path, "weights.json")
    filters_file = os.path.join(tmp_path, "filters.json")

    with open(weights_file, "w") as f:
        json.dump({
            "version": "2.0.0",
            "instruments": {
                "1HZ25V": {
                    "DIRECTIONAL_TREND": {
                        "SMC_ICT": 0.40,
                        "SUPPLY_DEMAND": 0.35,
                        "PRICE_ACTION": 0.25,
                        "win_rate": 0.55,
                        "profit_factor": 1.70,
                        "sample_size": 10,
                    }
                }
            },
        }, f)

    with open(filters_file, "w") as f:
        json.dump({
            "version": "2.0.0",
            "min_sweep_confirmation_candles": 2,
            "max_pending_hours": 6,
            "min_rr_ratio": 2.0,
            "atr_sl_buffer_multiplier": 0.5,
            "blocked_regimes_by_symbol": {},
            "symbol_overrides": {},
        }, f)

    council = GeminiCouncil(api_key="", notes_file=os.path.join(tmp_path, "notes.md"))
    council.client = None
    pm = PostMortemManager(
        db=db,
        council=council,
        weights_path=weights_file,
        filters_path=filters_file,
        reports_dir=os.path.join(tmp_path, "reports"),
        filter_mutation_batch_size=5,
    )

    # Speed 1 & Speed 2 Gate across trades #1..#4:
    # Weights must nudge gently by <= +-0.02 per trade, while hard filters stay locked until n=5
    for idx in range(1, 5):
        review = pm.process_resolved_trade({
            "trade_id": f"TRD-L-{idx}",
            "symbol": "JD75",
            "outcome": "LOSS",
            "strategy": "SMC_ICT",
            "realized_pnl": -1.40,
            "realized_r": -1.0,
        })
        assert review["hard_filter_mutated"] is False
        assert review["weight_nudge_cap"] == 0.02

    with open(filters_file, "r") as f:
        f_mid = json.load(f)
    assert f_mid["symbol_overrides"]["JD75"]["min_sweep_confirmation_candles"] == 2
    assert f_mid["symbol_overrides"]["JD75"]["min_rr_ratio"] == 2.0
    assert "JD75" not in f_mid["blocked_regimes_by_symbol"]

    # Force a strict Speed-2 recommendation batch on the 5th trade (simulated Council upgrade)
    f_mid["filter_mutation_state"]["JD75"]["pending_recommendations"].append({
        "recommended_min_sweep_candles": 4,
        "recommended_min_rr": 3.2,
        "flaw": "JUMP_SPIKE_STOPOUT",
    })
    with open(filters_file, "w") as f:
        json.dump(f_mid, f, indent=2)

    review_5 = pm.process_resolved_trade({
        "trade_id": "TRD-L-5",
        "symbol": "JD75",
        "outcome": "LOSS",
        "strategy": "SMC_ICT",
        "realized_pnl": -1.40,
        "realized_r": -1.0,
    })
    assert review_5["hard_filter_mutated"] is True

    with open(filters_file, "r") as f:
        f_after = json.load(f)
    assert f_after["symbol_overrides"]["JD75"]["min_sweep_confirmation_candles"] == 4
    assert f_after["symbol_overrides"]["JD75"]["min_rr_ratio"] == 3.2
    assert f_after["symbol_overrides"]["JD75"]["atr_sl_buffer_multiplier"] >= 0.65
    assert "RANDOM_WALK_CHOP" in f_after["blocked_regimes_by_symbol"]["JD75"]
    assert f_after["filter_mutation_state"]["JD75"]["trades_since_mutation"] == 0


def test_40_dollar_drawdown_circuit_breaker_and_1_trade_cap(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "shield.db"))
    bankroll = BankrollManager(db, policy_path="config/bankroll_policy.json")
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc, max_risk_percent=1.5, hard_cap_percent=2.0)
    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer)

    assert bankroll.get_equity_floor() == 60.0
    assert bankroll.get_max_concurrent_positions(100.0) == 1

    # Step 1: Max 1 concurrent position on $100 account
    tip1 = TradingTip(
        symbol="STEPINDEX", direction="BULLISH", confidence_score=0.88,
        primary_strategy="SMC_ICT", market_regime="DIRECTIONAL_TREND",
        current_price=7360.0, entry_min=7357.0, entry_max=7360.0,
        stop_loss=7350.0, take_profit_1=7385.0, take_profit_2=7400.0,
        sl_points=10.0, tp1_points=25.0, risk_reward_ratio=2.5,
        break_even_trigger_price=7370.0, estimated_hold_hours=2.0,
        estimated_hold_candles=2, confluence_reasons=["Setup 1"],
    )
    tip2 = tip1.model_copy(update={"symbol": "JD100", "current_price": 47.0, "entry_min": 46.8, "entry_max": 47.0, "stop_loss": 45.0, "take_profit_1": 52.0, "sl_points": 2.0, "tp1_points": 5.0})

    id1 = trader.submit_tip(tip1, lot_size=0.15, dollar_risk=1.50, margin_req=1.10)
    assert id1 is not None
    id2 = trader.submit_tip(tip2, lot_size=0.50, dollar_risk=1.00, margin_req=0.03)
    assert id2 is None  # Blocked by 1-trade concurrent cap on $100
    assert len(db.get_active_trades()) == 1

    # Remove tip1 from active queue so we can verify DRAWDOWN_CIRCUIT_BREAKER block independently
    db.mark_trade_expired(id1, reason="TEST_CLEANUP")

    # Step 2: Drop equity from $61.00 by -$1.50 -> $59.50 <= $60.00 hard floor ($40 max drawdown)
    db.set_kv("vault_balance", 12.00)
    db.set_kv("active_bankroll", 61.00)
    breaker_res = bankroll.process_trade_result(dollar_pnl=-1.50, r_multiple=-1.0, symbol="1HZ25V")

    assert breaker_res["event"] == "DRAWDOWN_CIRCUIT_BREAKER"
    assert breaker_res["pause_trading"] is True
    assert breaker_res["council_review_logged"] is True
    assert breaker_res["vault_preserved"] == 12.00
    assert breaker_res["old_epoch"] == 1
    assert breaker_res["new_epoch"] == 2
    assert bankroll.is_drawdown_breaker_active() is True

    # While DRAWDOWN_CIRCUIT_BREAKER is active, PaperTrader refuses new orders
    blocked_after_dd = trader.submit_tip(tip1, lot_size=0.15, dollar_risk=1.50, margin_req=1.10)
    assert blocked_after_dd is None
    assert len(db.get_active_trades()) == 0
