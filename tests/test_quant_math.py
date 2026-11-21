"""
Unit tests for Prop-Firm Quantitative Mathematics, Survivability Formulas,
Monte Carlo Permutation Simulator, Kelly Criterion, and Hurst Dynamics.
"""

import pytest
import numpy as np
from src.risk.quant_engine import QuantEngine


def test_survival_units_and_breakeven_win_rate():
    quant = QuantEngine()
    # $20 max drawdown / $1.50 risk per trade = 13 units
    units = quant.compute_survival_units(max_drawdown_dollar=20.0, risk_per_trade_dollar=1.50)
    assert units == 13

    # At 1:2.5 R:R, breakeven win rate is 1 / (1 + 2.5) = 28.57%
    w_be = quant.compute_breakeven_win_rate(reward_to_risk=2.5)
    assert pytest.approx(w_be, 0.001) == 0.2857


def test_mathematical_expectancy():
    quant = QuantEngine()
    # W=50%, Avg Win = 2.5R, Avg Loss = 1.0R -> EV = (0.5 * 2.5) - (0.5 * 1.0) = +0.75R
    ev = quant.compute_mathematical_expectancy(win_rate=0.50, avg_win_r=2.5, avg_loss_r=1.0)
    assert ev == 0.75


def test_losing_streak_ruin_guarantee():
    quant = QuantEngine()
    # At 45% win rate, loss rate = 55%. P(8 consecutive losses) = 0.55^8 ≈ 0.837%
    p_8 = quant.compute_losing_streak_probability(win_rate=0.45, streak_length=8)
    assert pytest.approx(p_8, 0.0001) == 0.008373

    # On $100 account with 1.5% risk ($1.50) and 15% max DD ($15.00),
    # 8 consecutive losses = $12.00 <= $15.00 -> Account survives!
    streak_eval = quant.evaluate_streak_ruin_safety(
        account_equity=100.0,
        risk_percent=1.5,
        max_drawdown_percent=15.0,
        min_safe_streak=8
    )
    assert streak_eval["is_safe"] is True
    assert streak_eval["survival_units"] == 10
    assert streak_eval["safety_margin_dollar"] == 3.00


def test_absorbing_barrier_probability():
    quant = QuantEngine()
    # $100 account, target +$20, drawdown -$15, 50% win rate, 1:2.5 R:R
    barrier = quant.compute_absorbing_barrier_probability(
        starting_capital=100.0,
        profit_target_dollar=20.0,
        max_drawdown_dollar=15.0,
        win_rate=0.50,
        reward_to_risk=2.5
    )
    assert barrier["probability_hit_target_first"] > 0.50
    assert barrier["expected_value_r"] > 0.0


def test_monte_carlo_permutation_simulator():
    quant = QuantEngine()
    # Realized R sample with positive edge
    sample_r = [2.5, -1.0, 2.5, -1.0, -1.0, 3.0, -1.0, 2.5, -1.0, 2.0]
    mc = quant.run_monte_carlo_permutation_sim(sample_r, num_simulations=1000)

    assert mc["num_simulations"] == 1000
    assert 0.0 <= mc["probability_hit_target_first"] <= 1.0
    assert mc["p95_worst_drawdown_r"] > 0.0
    assert mc["probability_hit_target_first"] >= 0.70 # Strong edge passes with high probability


def test_position_sizing_sweet_spot():
    quant = QuantEngine()
    # High win rate, low variance -> scales toward 1.5%
    sweet_high = quant.optimize_position_sizing_sweet_spot(rolling_win_rate=0.65, equity_curve_std=0.8)
    assert sweet_high >= 1.2

    # Low win rate, high variance -> compresses toward 0.5%
    sweet_low = quant.optimize_position_sizing_sweet_spot(rolling_win_rate=0.35, equity_curve_std=3.5)
    assert sweet_low <= 0.8
    assert sweet_low >= 0.5


def test_net_real_return_accounting():
    quant = QuantEngine()
    # 3 vault payouts ($10, $12, $15 = $37) and 0 epoch resets -> Net Real Return = +$37.00
    net1 = quant.compute_net_real_return(total_vaulted_payouts=37.0, epoch_resets_count=0, starting_capital=100.0)
    assert net1 == 37.0

    # If 1 epoch was reset ($100 capital burned), Net Real Return = 37 - 100 = -$63.00
    net2 = quant.compute_net_real_return(total_vaulted_payouts=37.0, epoch_resets_count=1, starting_capital=100.0)
    assert net2 == -63.0


def test_fractional_kelly_and_ratios():
    quant = QuantEngine()
    # W=0.55, R:R=2.5. Full Kelly f* = (0.55*2.5 - 0.45) / 2.5 = 0.37 -> Quarter Kelly = 0.0925 -> hard capped at 2.0%
    kelly = quant.compute_fractional_kelly(win_rate=0.55, reward_to_risk=2.5, fraction=0.25)
    assert kelly <= 0.02
    assert kelly >= 0.005

    # Sharpe, Sortino, Calmar
    returns = [0.03, -0.01, 0.04, -0.015, 0.05, 0.02]
    sharpe = quant.calculate_sharpe_ratio(returns)
    sortino = quant.calculate_sortino_ratio(returns)
    calmar = quant.calculate_calmar_ratio(annualized_return_percent=45.0, max_drawdown_percent=8.5)

    assert sharpe > 0.0
    assert sortino > 0.0
    assert calmar > 5.0


def test_hurst_exponent():
    quant = QuantEngine()
    # Generate pure trending series (random walk with positive drift)
    np.random.seed(42)
    trending_prices = np.cumprod(1 + np.random.normal(0.005, 0.002, 120))
    h_trend = quant.calculate_hurst_exponent(trending_prices.tolist(), max_lags=15)
    assert h_trend >= 0.50
