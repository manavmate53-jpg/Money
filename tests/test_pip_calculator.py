"""
Unit tests for Deriv Pip/Tick Calculator and Small-Account ($100) Viability Filter.
"""

import pytest
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer


def test_pip_calculator_specs():
    calc = PipCalculator("config/deriv_specs.json")
    v25_1s = calc.get_spec("1HZ25V")
    assert v25_1s["min_lot"] == 0.005
    assert v25_1s["contract_size"] == 1.0

    v75 = calc.get_spec("R_75")
    assert v75["min_lot"] == 0.01


def test_dollar_risk_and_lot_rounding():
    calc = PipCalculator("config/deriv_specs.json")
    # 1HZ25V: Entry 2800.0, SL 2780.0 (20 pts), Contract Size 1.0, Point Value 1.0
    # Dollar risk for 0.05 lot = 20 * 1 * 1 * 0.05 = $1.00
    risk = calc.calculate_dollar_risk(2800.0, 2780.0, 0.05, "1HZ25V")
    assert pytest.approx(risk, 0.001) == 1.00

    # Stepped lot rounding
    raw_lot = 0.05432
    stepped = calc.round_to_step(raw_lot, "1HZ25V")
    assert stepped == 0.054


def test_small_account_viability_filter():
    calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(calc, max_risk_percent=1.5, hard_cap_percent=2.0)

    # Test 1: Volatility 75 (R_75) with wide 500-point structural stop
    # Min lot 0.01 * 500 pts * 1 = $5.00 dollar risk. On a $100 account, this is 5.0% (> 2.0% hard cap).
    # MUST be disqualified.
    res_v75 = sizer.evaluate_instrument_viability("R_75", entry_price=450000.0, stop_loss_price=449500.0, account_equity=100.0)
    assert not res_v75["viable"]
    assert "DISQUALIFIED" in res_v75["rejection_reason"]
    assert res_v75["min_lot_dollar_risk"] == 5.00

    # Test 2: Volatility 25 (1s) with 200-point stop loss
    # Min lot is 0.005. Target risk is $1.50 (1.5% of $100).
    # Raw lot = $1.50 / 200 = 0.0075 -> rounded to step = 0.007.
    # Dollar risk = 200 * 0.007 = $1.40 <= $2.00 hard cap.
    # MUST be viable.
    res_v25 = sizer.evaluate_instrument_viability("1HZ25V", entry_price=2800.0, stop_loss_price=2600.0, account_equity=100.0)
    assert res_v25["viable"]
    assert res_v25["actual_dollar_risk"] <= 2.00
    assert res_v25["recommended_lot"] >= 0.005


def test_margin_and_pnl_math():
    calc = PipCalculator("config/deriv_specs.json")
    # Margin on 1HZ25V at price 2800.0 with 0.05 lot and 1:1000 leverage = (1 * 2800 * 0.05) / 1000 = $0.14
    margin = calc.calculate_required_margin(2800.0, 0.05, "1HZ25V")
    assert pytest.approx(margin, 0.01) == 0.14

    # PnL on BUY trade
    pnl_buy = calc.calculate_pnl("BUY", 2800.0, 2850.0, 0.05, "1HZ25V")
    assert pytest.approx(pnl_buy, 0.01) == 2.50 # 50 pts * 0.05 = $2.50

    # PnL on SELL trade
    pnl_sell = calc.calculate_pnl("SELL", 2800.0, 2750.0, 0.05, "1HZ25V")
    assert pytest.approx(pnl_sell, 0.01) == 2.50
