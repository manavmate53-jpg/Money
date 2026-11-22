"""
Unit tests for Gemini Council Reflexion Loop and Readiness Gate.
"""

import os
import json
import pytest

from src.database.db_manager import DatabaseManager
from src.council.gemini_council import GeminiCouncil
from src.council.post_mortem import PostMortemManager
from src.council.readiness_gate import ReadinessGate


@pytest.fixture
def council_env(tmp_path):
    db_file = os.path.join(tmp_path, "test_council.db")
    weights_file = os.path.join(tmp_path, "strategy_weights.json")

    # Seed strategy weights file
    weights_data = {
        "version": "1.0.0",
        "instruments": {
            "1HZ25V": {
                "DIRECTIONAL_TREND": {
                    "SMC_ICT": 0.40,
                    "SUPPLY_DEMAND": 0.35,
                    "PRICE_ACTION_AUCTION": 0.25,
                    "win_rate": 0.55,
                    "profit_factor": 1.70,
                    "sample_size": 10
                }
            }
        }
    }
    with open(weights_file, "w") as f:
        json.dump(weights_data, f)

    db = DatabaseManager(db_file)
    council = GeminiCouncil()
    pm = PostMortemManager(db, council, weights_path=weights_file)
    gate = ReadinessGate(db)

    return db, pm, gate, weights_file


def test_post_mortem_weight_mutation(council_env):
    db, pm, gate, weights_file = council_env

    # 1. Simulate WIN outcome
    win_result = {
        "trade_id": 1,
        "symbol": "1HZ25V",
        "outcome": "WIN",
        "strategy": "SMC_ICT",
        "realized_pnl": 3.75,
        "realized_r": 2.5
    }
    review = pm.process_resolved_trade(win_result)
    assert review["outcome"] == "WIN"
    assert review["structural_diagnosis"]

    # Verify weight changed in weights_file
    with open(weights_file, "r") as f:
        updated_weights = json.load(f)
    w = updated_weights["instruments"]["1HZ25V"]["DIRECTIONAL_TREND"]["SMC_ICT"]
    assert w > 0.40 # Bayesian update increased weight!


def test_readiness_gate_criteria(council_env):
    db, pm, gate, _ = council_env

    # Initial state: 0 trades -> TRAINING_CALIBRATION
    metrics = gate.evaluate_metrics()
    assert metrics["is_live_ready"] is False
    assert "TRAINING_CALIBRATION" in metrics["status"]

    # Insert simulated trades: 35 wins, 5 losses (Profit Factor > 1.5, Trade Count >= 30)
    with db.get_connection() as conn:
        for i in range(35):
            conn.execute("""
            INSERT INTO paper_trades (
                epoch_number, symbol, direction, strategy_primary,
                entry_min, entry_max, stop_loss, original_stop_loss,
                take_profit_1, lot_size, dollar_risk, margin_required,
                status, created_at, closed_at, realized_pnl, realized_r
            ) VALUES (1, '1HZ25V', 'BUY', 'SMC_ICT', 2800, 2810, 2790, 2790, 2850, 0.05, 1.50, 0.15, 'CLOSED_TP',
                      '2026-10-01T00:00:00', '2026-10-01T06:00:00', 3.00, 2.0)
            """)
        for j in range(5):
            conn.execute("""
            INSERT INTO paper_trades (
                epoch_number, symbol, direction, strategy_primary,
                entry_min, entry_max, stop_loss, original_stop_loss,
                take_profit_1, lot_size, dollar_risk, margin_required,
                status, created_at, closed_at, realized_pnl, realized_r
            ) VALUES (1, '1HZ25V', 'BUY', 'SMC_ICT', 2800, 2810, 2790, 2790, 2850, 0.05, 1.50, 0.15, 'CLOSED_SL',
                      '2026-10-02T00:00:00', '2026-10-02T06:00:00', -1.50, -1.0)
            """)
        conn.commit()

    updated_metrics = gate.evaluate_metrics()
    assert updated_metrics["total_trades"] == 40
    assert updated_metrics["profit_factor"] >= 1.5
    assert updated_metrics["win_rate"] > 0.80


def test_gemini_council_macro_and_tactical_schemas(council_env):
    db, pm, gate, _ = council_env
    council = GeminiCouncil()

    # Test tactical sniper schema
    tactical = council.validate_tactical_sniper("1HZ25V", "BULLISH", {"direction": "BULLISH", "confidence": 0.85})
    assert "approved" in tactical
    assert "adjusted_lot_multiplier" in tactical
    assert tactical["adjusted_lot_multiplier"] > 0

    # Test macro regime schema
    macro = council.evaluate_macro_regime("1HZ25V", {
        "current_price": 2800.0,
        "smc_ict": {"structure": {"trend": "BULLISH"}},
        "price_action": {"regime": "DIRECTIONAL_TREND", "atr_14": 25.0}
    })
    assert macro["symbol"] == "1HZ25V"
    assert macro["macro_bias"] in ["BULLISH", "BEARISH", "NEUTRAL"]
    assert "key_invalidation_level" in macro


def test_capital_advisor_blueprint(council_env):
    db, pm, gate, _ = council_env
    from src.advisor.capital_advisor import CapitalAdvisor
    advisor = CapitalAdvisor(db)
    result = advisor.advise(capital=100.0)
    assert result["capital_analyzed"] == 100.0
    assert result["recommended_instrument"]
    assert "dollar_risk" in result
    assert result["dollar_risk"] <= 2.0  # Safe for $100 account
    assert os.path.exists("reports/LATEST_ADVICE.md")


