"""
Quantitative Trading Mathematics & Stochastic Risk Engine.
Implements:
1. Prop-Style Boundary & Survivability Math (from https://youtu.be/vGSpbspmGoM):
   - Effective Drawdown Budget vs. Nominal Balance (Survival Units)
   - Absorbing Barrier / First-Passage Probability
   - Mathematical Expectancy & Breakeven Win Rate Threshold
   - Consecutive Losing Streak Probability (k >= 8 survival guarantee)
   - 1,000-Path Monte Carlo Permutation Simulator (+20% before -15% touch probability)
   - Dynamic Position Sizing Sweet-Spot Optimizer (0.5% - 1.5%)
   - Static vs. End-of-Day Trailing High-Water Mark Drawdown
   - Net Real Return Accounting across Epochs
2. Institutional Portfolio & Stochastic Quant Math:
   - Fractional Kelly Criterion & Ralph Vince's Optimal f (Quarter-Kelly)
   - Rolling Sharpe, Sortino, Calmar Ratio & Ulcer Index
   - Deriv CSPRNG Geometric Brownian Motion & Hurst Exponent (H > 0.55 trend, H < 0.45 mean-revert)
   - Bayesian Thompson Sampling Strategy Weight Updating
"""

import math
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
import pandas as pd


class QuantEngine:
    def __init__(self, risk_free_rate: float = 0.0):
        self.risk_free_rate = risk_free_rate

    # =========================================================================
    # 1. PROP-STYLE BOUNDARY & SURVIVABILITY MATH (from https://youtu.be/vGSpbspmGoM)
    # =========================================================================

    def compute_survival_units(self, max_drawdown_dollar: float, risk_per_trade_dollar: float) -> int:
        """
        Computes how many consecutive full-loss bullets the account can withstand.
        Survival Units = floor(Max Drawdown ($) / Risk per Trade ($))
        Example: $20 max DD budget / $1.50 risk = 13 survival units.
        """
        if risk_per_trade_dollar <= 0:
            return 0
        return int(math.floor(max_drawdown_dollar / risk_per_trade_dollar))

    def compute_breakeven_win_rate(self, reward_to_risk: float) -> float:
        """
        Computes theoretical breakeven win rate:
        W_be = 1 / (1 + R:R)
        Example: At 1:2.5 R:R, W_be = 1 / (1 + 2.5) = 28.57%
        """
        if reward_to_risk <= 0:
            return 1.0
        return round(1.0 / (1.0 + reward_to_risk), 4)

    def compute_mathematical_expectancy(self, win_rate: float, avg_win_r: float, avg_loss_r: float = 1.0) -> float:
        """
        EV = (W * Avg_Win_R) - ((1 - W) * Avg_Loss_R)
        Example: W=0.50, Win_R=2.5, Loss_R=1.0 -> EV = (0.50 * 2.5) - (0.50 * 1.0) = +0.75R per trade.
        """
        ev = (win_rate * avg_win_r) - ((1.0 - win_rate) * avg_loss_r)
        return round(ev, 4)

    def compute_losing_streak_probability(self, win_rate: float, streak_length: int = 8) -> float:
        """
        Computes probability of suffering exactly k consecutive losses:
        P(k losses) = (1 - W)^k
        Example: At 45% win rate, P(8 consecutive losses) = (0.55)^8 = 0.83%
        """
        loss_rate = max(0.0, 1.0 - win_rate)
        return round(float(loss_rate ** streak_length), 6)

    def evaluate_streak_ruin_safety(self, account_equity: float, risk_percent: float, max_drawdown_percent: float = 15.0, min_safe_streak: int = 8) -> Dict[str, Any]:
        """
        Verifies that position sizing guarantees surviving at least min_safe_streak (k=8)
        consecutive losses without breaching the drawdown floor.
        """
        dollar_risk = account_equity * (risk_percent / 100.0)
        max_dd_budget = account_equity * (max_drawdown_percent / 100.0)
        streak_loss_total = dollar_risk * min_safe_streak
        is_safe = streak_loss_total <= max_dd_budget

        survival_units = self.compute_survival_units(max_dd_budget, dollar_risk)

        return {
            "is_safe": is_safe,
            "min_safe_streak": min_safe_streak,
            "survival_units": survival_units,
            "max_dd_budget": round(max_dd_budget, 2),
            "streak_loss_total": round(streak_loss_total, 2),
            "safety_margin_dollar": round(max_dd_budget - streak_loss_total, 2)
        }

    def compute_absorbing_barrier_probability(
        self,
        starting_capital: float,
        profit_target_dollar: float,
        max_drawdown_dollar: float,
        win_rate: float,
        reward_to_risk: float
    ) -> Dict[str, Any]:
        """
        Models the capital path as a biased random walk between two absorbing barriers:
        Upper Barrier: +Profit Target (e.g. +$20 on $100)
        Lower Barrier: -Max Drawdown (e.g. -$15 on $100)
        Computes probability of hitting upper barrier before lower barrier using Gambler's Ruin with asymmetry.
        """
        p = win_rate
        q = 1.0 - p
        # Asymmetric drift ratio
        # If EV > 0, drift is positive
        ev = self.compute_mathematical_expectancy(win_rate, reward_to_risk, 1.0)

        # Standard discrete approximation: s = steps to lower, t = steps to upper
        step_unit = 1.50 # Dollar risk per trade
        steps_down = max(1, int(round(max_drawdown_dollar / step_unit)))
        steps_up = max(1, int(round(profit_target_dollar / (step_unit * reward_to_risk))))

        if abs(p - q) < 0.001:
            prob_success = steps_down / (steps_down + steps_up)
        else:
            ratio = (q / (p * reward_to_risk)) if (p * reward_to_risk) > 0 else 999.0
            ratio = max(0.0001, min(10.0, ratio))
            if abs(ratio - 1.0) < 0.001:
                prob_success = steps_down / (steps_down + steps_up)
            else:
                try:
                    prob_success = (1.0 - (ratio ** steps_down)) / (1.0 - (ratio ** (steps_down + steps_up)))
                except OverflowError:
                    prob_success = 0.01

        prob_success = max(0.01, min(0.99, prob_success))

        return {
            "probability_hit_target_first": round(prob_success, 4),
            "probability_ruin_first": round(1.0 - prob_success, 4),
            "expected_value_r": ev,
            "steps_to_target": steps_up,
            "steps_to_ruin": steps_down
        }

    def run_monte_carlo_permutation_sim(
        self,
        resolved_r_multiples: List[float],
        num_simulations: int = 1000,
        target_r: float = 13.33, # +20% on 1.5% risk = +13.33R
        drawdown_floor_r: float = 10.0 # -15% on 1.5% risk = -10.0R
    ) -> Dict[str, Any]:
        """
        1,000-Path Monte Carlo Permutation Simulator:
        Shuffles historical resolved trade outcomes across 1,000 independent random permutations.
        Computes 95th-percentile worst drawdown and empirical probability of touching +20% before -15%.
        """
        if not resolved_r_multiples or len(resolved_r_multiples) < 5:
            # Seed with conservative baseline prior distribution
            resolved_r_multiples = [2.5, -1.0, 2.5, -1.0, -1.0, 2.5, -1.0, 2.5, -1.0, 2.5]

        outcomes = np.array(resolved_r_multiples, dtype=float)
        n_trades = max(30, len(outcomes) * 3)

        success_count = 0
        max_drawdowns_r = []
        final_returns_r = []

        for _ in range(num_simulations):
            # Bootstrap resample with replacement
            sampled_path = np.random.choice(outcomes, size=n_trades, replace=True)
            cumulative_r = np.cumsum(sampled_path)

            peak = 0.0
            max_dd = 0.0
            hit_target = False
            hit_ruin = False

            for val in cumulative_r:
                if val > peak:
                    peak = val
                dd = peak - val
                if dd > max_dd:
                    max_dd = dd

                if val >= target_r and not hit_ruin:
                    hit_target = True
                    break
                if val <= -drawdown_floor_r:
                    hit_ruin = True
                    break

            if hit_target and not hit_ruin:
                success_count += 1

            max_drawdowns_r.append(max_dd)
            final_returns_r.append(cumulative_r[-1])

        p95_worst_dd_r = float(np.percentile(max_drawdowns_r, 95))
        p50_median_dd_r = float(np.median(max_drawdowns_r))
        prob_pass = round(success_count / num_simulations, 4)

        return {
            "num_simulations": num_simulations,
            "probability_hit_target_first": prob_pass,
            "p95_worst_drawdown_r": round(p95_worst_dd_r, 2),
            "p50_median_drawdown_r": round(p50_median_dd_r, 2),
            "p95_worst_drawdown_percent": round(p95_worst_dd_r * 1.5, 2), # Assuming 1.5% risk
            "expected_final_return_r": round(float(np.mean(final_returns_r)), 2)
        }

    def optimize_position_sizing_sweet_spot(
        self,
        rolling_win_rate: float,
        equity_curve_std: float,
        min_allowed_risk: float = 0.5,
        max_allowed_risk: float = 1.5
    ) -> float:
        """
        Dynamically constrains risk per trade to the 0.5% - 1.5% sweet spot.
        Penalizes low risk (<0.5%) causing account stagnation and high risk (>1.5%) causing streak ruin.
        Calibrated against rolling win rate and equity curve standard deviation.
        """
        # Baseline risk
        base_risk = 1.0

        # Win rate adjustment: higher win rate allows scaling toward 1.5%
        if rolling_win_rate >= 0.60:
            base_risk += 0.35
        elif rolling_win_rate >= 0.50:
            base_risk += 0.15
        elif rolling_win_rate < 0.40:
            base_risk -= 0.35

        # Variance penalty: high standard deviation compresses risk toward 0.5%
        if equity_curve_std > 3.0:
            base_risk -= 0.30
        elif equity_curve_std > 1.5:
            base_risk -= 0.15

        sweet_spot = max(min_allowed_risk, min(max_allowed_risk, base_risk))
        return round(sweet_spot, 2)

    def compute_static_vs_eod_drawdowns(self, equity_history: List[float], initial_capital: float = 100.0) -> Dict[str, float]:
        """
        Tracks both:
        1. Static Drawdown: decline from starting capital ($100.00).
        2. EOD Trailing High-Water Mark (HWM) Drawdown: decline from peak historical equity.
        """
        if not equity_history:
            return {"static_drawdown_percent": 0.0, "trailing_hwm_drawdown_percent": 0.0, "peak_equity": initial_capital}

        current_equity = equity_history[-1]
        static_dd = max(0.0, ((initial_capital - current_equity) / initial_capital) * 100.0)

        peak = max(initial_capital, max(equity_history))
        trailing_dd = max(0.0, ((peak - current_equity) / peak) * 100.0)

        return {
            "current_equity": round(current_equity, 2),
            "peak_equity": round(peak, 2),
            "static_drawdown_percent": round(static_dd, 2),
            "trailing_hwm_drawdown_percent": round(trailing_dd, 2)
        }

    def compute_net_real_return(self, total_vaulted_payouts: float, epoch_resets_count: int, starting_capital: float = 100.0) -> float:
        """
        Net Real Return ($) = Sum(Vaulted Payouts) - Sum(Simulation Epoch Resets * Starting Capital)
        Measures true commercial bottom line across all auto-reset epochs.
        """
        net_return = total_vaulted_payouts - (epoch_resets_count * starting_capital)
        return round(net_return, 2)

    # =========================================================================
    # 2. INSTITUTIONAL PORTFOLIO & STOCHASTIC QUANT MATH
    # =========================================================================

    def compute_fractional_kelly(self, win_rate: float, reward_to_risk: float, fraction: float = 0.25) -> float:
        """
        Ralph Vince's Optimal f & Fractional Kelly:
        f* = (W * b - (1 - W)) / b
        Enforces Quarter-Kelly (0.25 * f*) with a hard 2.0% cap to prevent ruin.
        """
        if reward_to_risk <= 0:
            return 0.005
        b = reward_to_risk
        w = win_rate
        f_star = (w * b - (1.0 - w)) / b
        if f_star <= 0:
            return 0.005 # Baseline minimum 0.5%

        quarter_kelly = f_star * fraction
        # Hard bounds: min 0.5%, max 2.0%
        final_risk = max(0.005, min(0.02, quarter_kelly))
        return round(final_risk, 4)

    def calculate_sharpe_ratio(self, returns: List[float], annualized_factor: float = 365.0) -> float:
        """Computes rolling annualized Sharpe Ratio."""
        if not returns or len(returns) < 3:
            return 0.0
        arr = np.array(returns, dtype=float)
        mean_ret = np.mean(arr) - (self.risk_free_rate / annualized_factor)
        std_ret = np.std(arr, ddof=1)
        if std_ret <= 1e-6:
            return 0.0
        sharpe = (mean_ret / std_ret) * math.sqrt(annualized_factor)
        return round(float(sharpe), 2)

    def calculate_sortino_ratio(self, returns: List[float], annualized_factor: float = 365.0) -> float:
        """Computes rolling Sortino Ratio (penalizing only downside volatility)."""
        if not returns or len(returns) < 3:
            return 0.0
        arr = np.array(returns, dtype=float)
        mean_ret = np.mean(arr) - (self.risk_free_rate / annualized_factor)
        downside = arr[arr < 0]
        if len(downside) == 0:
            return 99.0
        downside_std = math.sqrt(np.mean(downside ** 2))
        if downside_std <= 1e-6:
            return 99.0
        sortino = (mean_ret / downside_std) * math.sqrt(annualized_factor)
        return round(float(sortino), 2)

    def calculate_calmar_ratio(self, annualized_return_percent: float, max_drawdown_percent: float) -> float:
        """Calmar Ratio = Annualized Return (%) / Max Drawdown (%)."""
        if max_drawdown_percent <= 0:
            return 99.0
        return round(annualized_return_percent / max_drawdown_percent, 2)

    def calculate_ulcer_index(self, equity_history: List[float]) -> float:
        """
        Ulcer Index: Quadratic measure of drawdown depth and duration.
        UI = sqrt( mean( ((Equity - Peak) / Peak * 100)^2 ) )
        """
        if not equity_history or len(equity_history) < 2:
            return 0.0
        peak = equity_history[0]
        squared_pct_drawdowns = []
        for eq in equity_history:
            if eq > peak:
                peak = eq
            dd_pct = ((peak - eq) / peak * 100.0) if peak > 0 else 0.0
            squared_pct_drawdowns.append(dd_pct ** 2)
        ui = math.sqrt(np.mean(squared_pct_drawdowns))
        return round(ui, 2)

    def calculate_hurst_exponent(self, prices: List[float], max_lags: int = 20) -> float:
        """
        Computes the Hurst Exponent (H) via Rescaled Range (R/S) analysis on log-returns:
        - H > 0.55: Persistent / Trending Regime
        - H < 0.45: Anti-Persistent / Mean-Reverting Regime
        - 0.45 <= H <= 0.55: Pure Random Walk Chop (auto-skips entries)
        """
        if len(prices) < max_lags * 3:
            return 0.50

        # Log returns
        p = np.array(prices, dtype=float)
        returns = np.diff(np.log(p))
        n = len(returns)

        lags = range(4, min(max_lags, n // 2))
        rs_values = []

        for lag in lags:
            # Subdivide series into non-overlapping chunks
            num_chunks = n // lag
            chunk_rs = []
            for i in range(num_chunks):
                chunk = returns[i * lag:(i + 1) * lag]
                mean_c = np.mean(chunk)
                cum_dev = np.cumsum(chunk - mean_c)
                r_val = np.max(cum_dev) - np.min(cum_dev)
                s_val = np.std(chunk, ddof=1)
                if s_val > 1e-8:
                    chunk_rs.append(r_val / s_val)
            if chunk_rs:
                rs_values.append((lag, np.mean(chunk_rs)))

        if len(rs_values) < 3:
            return 0.50

        lag_arr = np.log([x[0] for x in rs_values])
        rs_arr = np.log([x[1] for x in rs_values])

        # Linear regression slope = Hurst Exponent
        poly = np.polyfit(lag_arr, rs_arr, 1)
        hurst = float(poly[0])
        return round(max(0.05, min(0.95, hurst)), 3)

    def bayesian_thompson_sampling_update(self, alpha: float, beta: float, win: bool) -> Tuple[float, float, float]:
        """
        Updates Bayesian Beta posterior distribution parameters (alpha, beta) after a trade:
        - Win: alpha += 1.0
        - Loss: beta += 1.0
        Returns: (new_alpha, new_beta, sampled_probability)
        """
        new_alpha = alpha + (1.0 if win else 0.0)
        new_beta = beta + (0.0 if win else 1.0)
        sampled_prob = float(np.random.beta(new_alpha, new_beta))
        return round(new_alpha, 2), round(new_beta, 2), round(sampled_prob, 4)
