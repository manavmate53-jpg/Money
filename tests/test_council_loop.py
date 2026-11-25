"""
Unit tests for Gemini Pydantic Schemas, Council Reflexion Loop, and Readiness Gate.
"""

import os
import json
import pytest

from src.database.db_manager import DatabaseManager
from src.council.gemini_council import (
    GeminiCouncil,
    MacroAnalysisSchema,
    TradeValidationSchema,
    PostMortemSchema,
)
from src.council.post_mortem import PostMortemManager
from src.council.readiness_gate import ReadinessGate
from src.advisor.capital_advisor import CapitalAdvisor


@pytest.fixture
def council_env(tmp_path):
    db_file = os.path.join(tmp_path, "test_council.db")
    weights_file = os.path.join(tmp_path, "strategy_weights.json")
    filters_file = os.path.join(tmp_path, "active_filters.json")
    dashboard_file = os.path.join(tmp_path, "TRAINING_DASHBOARD.md")

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

    with open(filters_file, "w") as f:
        json.dump({
            "version": "1.0.0",
            "min_sweep_confirmation_candles": 2,
            "max_pending_hours": 6,
            "min_rr_ratio": 2.0,
            "blocked_regimes_by_symbol": {},
            "symbol_overrides": {}
        }, f)

    notes_file = os.path.join(tmp_path, "COUNCIL_NOTES.md")
    reports_dir = os.path.join(tmp_path, "reports")
    db = DatabaseManager(db_file)
    council = GeminiCouncil(api_key="", notes_file=notes_file)  # Offline safe unit test mode
    pm = PostMortemManager(db, council, weights_path=weights_file, filters_path=filters_file, reports_dir=reports_dir)
    gate = ReadinessGate(db, dashboard_path=dashboard_file)

    return db, pm, gate, weights_file, filters_file, dashboard_file, str(tmp_path)


def test_gemini_pydantic_schemas_no_additional_properties():
    """Gemini Structured Outputs forbid additionalProperties / Dict types."""
    for schema_cls in (MacroAnalysisSchema, TradeValidationSchema, PostMortemSchema):
        schema_str = json.dumps(schema_cls.model_json_schema())
        assert "additionalProperties" not in schema_str


def test_post_mortem_weight_mutation_and_filters(council_env):
    db, pm, gate, weights_file, filters_file, _, _ = council_env

    # Force client=None for isolated unit testing without network calls
    pm.council.client = None

    win_result = {
        "trade_id": "TRD-WIN-1",
        "symbol": "1HZ25V",
        "outcome": "WIN",
        "strategy": "SMC_ICT",
        "realized_pnl": 3.75,
        "realized_r": 2.5
    }
    review = pm.process_resolved_trade(win_result)
    assert review["outcome"] == "WIN"
    assert review["structural_diagnosis"]
    assert isinstance(review["weight_adjustment"], dict)

    with open(weights_file, "r") as f:
        updated_weights = json.load(f)
    w = updated_weights["instruments"]["1HZ25V"]["DIRECTIONAL_TREND"]["SMC_ICT"]
    assert w > 0.40

    loss_result = {
        "trade_id": "TRD-LOSS-1",
        "symbol": "1HZ75V",
        "outcome": "LOSS",
        "strategy": "SMC_ICT",
        "realized_pnl": -1.30,
        "realized_r": -1.0
    }
    pm.process_resolved_trade(loss_result)
    with open(filters_file, "r") as f:
        filters = json.load(f)
    assert filters["symbol_overrides"]["1HZ75V"]["min_sweep_confirmation_candles"] >= 2
    assert len(filters["recent_mutations"]) >= 2


def test_readiness_gate_criteria_unified_tables(council_env):
    db, pm, gate, _, _, dashboard_file, _ = council_env

    # Seed epoch #1 start date 35 days ago so calibration window passes cleanly
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO simulation_epochs (epoch_number, start_time, starting_balance, status) VALUES (1, '2026-10-01T00:00:00+00:00', 100.0, 'ACTIVE')"
        )
        conn.commit()

    # Even with 0 resolved trades, dashboard must still be written fresh
    metrics0 = gate.evaluate_metrics()
    assert metrics0["is_live_ready"] is False
    assert metrics0["total_trades"] == 0
    assert os.path.exists(dashboard_file)

    # Record 35 wins -> profitable_trades, 5 losses -> losing_trades
    for i in range(35):
        db.record_closed_trade({
            "trade_id": f"TRD-W-{i}",
            "epoch_id": 1,
            "symbol": "1HZ25V",
            "direction": "BULLISH",
            "entry_price": 2800.0,
            "exit_price": 2850.0,
            "tp_price": 2850.0,
            "sl_price": 2780.0,
            "lot_size": 0.05,
            "rr_realized": 2.0,
            "net_pnl": 3.00,
            "opened_at": "2026-10-02T00:00:00+00:00",
            "closed_at": f"2026-10-02T06:{i:02d}:00+00:00",
        })
    for j in range(5):
        db.record_closed_trade({
            "trade_id": f"TRD-L-{j}",
            "epoch_id": 1,
            "symbol": "1HZ25V",
            "direction": "BULLISH",
            "entry_price": 2800.0,
            "exit_price": 2780.0,
            "tp_price": 2850.0,
            "sl_price": 2780.0,
            "lot_size": 0.05,
            "rr_realized": -1.0,
            "net_pnl": -1.50,
            "opened_at": "2026-10-03T00:00:00+00:00",
            "closed_at": f"2026-10-03T06:{j:02d}:00+00:00",
        })

    updated_metrics = gate.evaluate_metrics()
    assert updated_metrics["total_trades"] == 40
    assert updated_metrics["wins_count"] == 35
    assert updated_metrics["losses_count"] == 5
    assert updated_metrics["profit_factor"] >= 1.5
    assert updated_metrics["win_rate"] > 0.80


def test_gemini_council_fallback_structures(council_env):
    council = GeminiCouncil(api_key="")
    council.client = None

    tactical = council.validate_tactical_sniper("1HZ25V", "BULLISH", {"direction": "BULLISH", "confidence": 0.85})
    assert tactical["approved"] is True
    assert tactical["adjusted_lot_multiplier"] > 0

    macro = council.evaluate_macro_regime("1HZ25V", {
        "current_price": 2800.0,
        "smc_ict": {"structure": {"trend": "BULLISH"}},
        "price_action": {"regime": "DIRECTIONAL_TREND", "atr_14": 25.0}
    })
    assert macro["symbol"] == "1HZ25V"
    assert macro["macro_bias"] in ["BULLISH", "BEARISH", "NEUTRAL"]
    assert "key_invalidation_level" in macro


def test_capital_advisor_blueprint(council_env):
    db, pm, gate, _, _, _, tmp_dir = council_env
    from src.browser.ws_interceptor import DerivWsInterceptor

    class OfflineWs(DerivWsInterceptor):
        def get_candles(self, symbol: str, timeframe: str = "H1", count: int = 150):
            return self.generate_synthetic_candles(symbol, timeframe, count)

    advice_path = os.path.join(tmp_dir, "LATEST_ADVICE.md")
    advisor = CapitalAdvisor(db, ws_interceptor=OfflineWs(db), readiness_gate=gate)
    result = advisor.advise(capital=100.0, report_path=advice_path)
    assert result["capital_analyzed"] == 100.0
    assert result["recommended_instrument"]
    assert "dollar_risk" in result
    assert result["dollar_risk"] <= 2.0
    assert os.path.exists(advice_path)
