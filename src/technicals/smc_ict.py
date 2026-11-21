"""
Smart Money Concepts (SMC) & ICT Deterministic Indicator Module.
Implements vectorized algorithms for:
- Swing High/Low fractals (N-bar pivot)
- Break of Structure (BOS) & Market Structure Shift (MSS/CHoCH)
- 3-Candle Fair Value Gaps (FVG) and Consequent Encroachment (CE)
- Bullish & Bearish Order Blocks (OB)
- Buy-Side (BSL) and Sell-Side (SSL) Liquidity Sweeps
- Premium vs Discount zones (50% equilibrium)
- Optimal Trade Entry (OTE: 0.618 - 0.786 Fibonacci retracement)
"""

from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd


class SmcIctEngine:
    def __init__(self, swing_length: int = 3):
        self.swing_length = swing_length

    def find_swing_points(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Identifies N-bar pivot Swing Highs and Swing Lows.
        """
        df = df.copy()
        n = self.swing_length
        highs = df["high"].values
        lows = df["low"].values
        num_candles = len(df)

        swing_high = np.zeros(num_candles, dtype=bool)
        swing_low = np.zeros(num_candles, dtype=bool)

        for i in range(n, num_candles - n):
            # Check high is higher than n bars left and right
            is_sh = True
            for offset in range(1, n + 1):
                if highs[i] <= highs[i - offset] or highs[i] < highs[i + offset]:
                    is_sh = False
                    break
            if is_sh:
                swing_high[i] = True

            # Check low is lower than n bars left and right
            is_sl = True
            for offset in range(1, n + 1):
                if lows[i] >= lows[i - offset] or lows[i] > lows[i + offset]:
                    is_sl = False
                    break
            if is_sl:
                swing_low[i] = True

        df["swing_high"] = swing_high
        df["swing_low"] = swing_low
        return df

    def detect_structure(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Analyzes recent swing points to detect Trend, BOS, and MSS (CHoCH).
        """
        df_swings = self.find_swing_points(df)
        sh_indices = df_swings.index[df_swings["swing_high"]].tolist()
        sl_indices = df_swings.index[df_swings["swing_low"]].tolist()

        if len(sh_indices) < 2 or len(sl_indices) < 2:
            return {
                "trend": "NEUTRAL",
                "bos": None,
                "mss": None,
                "last_swing_high": float(df["high"].iloc[-1]),
                "last_swing_low": float(df["low"].iloc[-1]),
                "prior_swing_high": None,
                "prior_swing_low": None
            }

        last_sh_idx = sh_indices[-1]
        prior_sh_idx = sh_indices[-2]
        last_sl_idx = sl_indices[-1]
        prior_sl_idx = sl_indices[-2]

        last_sh = float(df_swings.loc[last_sh_idx, "high"])
        prior_sh = float(df_swings.loc[prior_sh_idx, "high"])
        last_sl = float(df_swings.loc[last_sl_idx, "low"])
        prior_sl = float(df_swings.loc[prior_sl_idx, "low"])

        current_close = float(df["close"].iloc[-1])

        # Trend bias determination
        trend = "NEUTRAL"
        if last_sh > prior_sh and last_sl > prior_sl:
            trend = "BULLISH"
        elif last_sh < prior_sh and last_sl < prior_sl:
            trend = "BEARISH"

        bos = None
        mss = None

        # BOS: Continuation break
        if trend == "BULLISH" and current_close > last_sh:
            bos = {"type": "BULLISH_BOS", "level": last_sh, "broken_at_close": current_close}
        elif trend == "BEARISH" and current_close < last_sl:
            bos = {"type": "BEARISH_BOS", "level": last_sl, "broken_at_close": current_close}

        # MSS / CHoCH: First reversal break of opposite structure
        if trend == "BULLISH" and current_close < last_sl:
            mss = {"type": "BEARISH_MSS", "level": last_sl, "broken_at_close": current_close}
        elif trend == "BEARISH" and current_close > last_sh:
            mss = {"type": "BULLISH_MSS", "level": last_sh, "broken_at_close": current_close}

        return {
            "trend": trend,
            "bos": bos,
            "mss": mss,
            "last_swing_high": last_sh,
            "last_swing_low": last_sl,
            "prior_swing_high": prior_sh,
            "prior_swing_low": prior_sl
        }

    def detect_fair_value_gaps(self, df: pd.DataFrame, max_lookback: int = 50) -> List[Dict[str, Any]]:
        """
        Detects 3-candle Fair Value Gaps (Bullish & Bearish) and checks mitigation.
        """
        fvgs = []
        n = len(df)
        start_idx = max(2, n - max_lookback)
        current_price = float(df["close"].iloc[-1])

        for i in range(start_idx, n):
            c1_high = float(df["high"].iloc[i - 2])
            c1_low = float(df["low"].iloc[i - 2])
            c3_high = float(df["high"].iloc[i])
            c3_low = float(df["low"].iloc[i])

            # Bullish FVG: Candle 1 High < Candle 3 Low
            if c3_low > c1_high:
                gap_low = c1_high
                gap_high = c3_low
                ce = (gap_low + gap_high) / 2.0
                # Check mitigation in subsequent candles
                mitigated = False
                for j in range(i + 1, n):
                    if df["low"].iloc[j] <= gap_low:
                        mitigated = True
                        break
                    elif df["low"].iloc[j] <= ce:
                        # Partially mitigated
                        pass

                fvgs.append({
                    "type": "BULLISH_FVG",
                    "candle_idx": i - 1,
                    "gap_low": gap_low,
                    "gap_high": gap_high,
                    "consequent_encroachment": ce,
                    "is_mitigated": mitigated,
                    "distance_to_price": abs(current_price - ce)
                })

            # Bearish FVG: Candle 1 Low > Candle 3 High
            elif c1_low > c3_high:
                gap_high = c1_low
                gap_low = c3_high
                ce = (gap_low + gap_high) / 2.0
                mitigated = False
                for j in range(i + 1, n):
                    if df["high"].iloc[j] >= gap_high:
                        mitigated = True
                        break

                fvgs.append({
                    "type": "BEARISH_FVG",
                    "candle_idx": i - 1,
                    "gap_low": gap_low,
                    "gap_high": gap_high,
                    "consequent_encroachment": ce,
                    "is_mitigated": mitigated,
                    "distance_to_price": abs(current_price - ce)
                })

        return fvgs

    def detect_order_blocks(self, df: pd.DataFrame, max_lookback: int = 50) -> List[Dict[str, Any]]:
        """
        Detects Order Blocks: Last opposing candle before impulsive displacement breaking structure.
        """
        order_blocks = []
        n = len(df)
        start_idx = max(5, n - max_lookback)

        for i in range(start_idx, n - 2):
            c_open = float(df["open"].iloc[i])
            c_close = float(df["close"].iloc[i])
            c_high = float(df["high"].iloc[i])
            c_low = float(df["low"].iloc[i])

            # Check next 2 candles for displacement
            next_close = float(df["close"].iloc[i + 2])
            avg_body = float((df["high"] - df["low"]).iloc[max(0, i - 14):i].mean()) or 1.0

            # Bullish OB: Bearish candle followed by strong bullish impulse
            if c_close < c_open and (next_close - c_close) >= (1.5 * avg_body):
                # Verify mitigation
                mitigated = any(df["low"].iloc[k] < c_low for k in range(i + 3, n))
                order_blocks.append({
                    "type": "BULLISH_OB",
                    "candle_idx": i,
                    "zone_top": c_high,
                    "zone_bottom": c_low,
                    "invalidation_price": c_low,
                    "is_mitigated": mitigated
                })

            # Bearish OB: Bullish candle followed by strong bearish impulse
            elif c_close > c_open and (c_close - next_close) >= (1.5 * avg_body):
                mitigated = any(df["high"].iloc[k] > c_high for k in range(i + 3, n))
                order_blocks.append({
                    "type": "BEARISH_OB",
                    "candle_idx": i,
                    "zone_top": c_high,
                    "zone_bottom": c_low,
                    "invalidation_price": c_high,
                    "is_mitigated": mitigated
                })

        return order_blocks

    def detect_liquidity_sweeps(self, df: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
        """
        Detects sweeps of Buy-Side (BSL) or Sell-Side (SSL) Liquidity.
        Sweep occurs when price wicks beyond a swing point but closes back inside.
        """
        if len(df) < lookback:
            return {"bsl_swept": False, "ssl_swept": False, "sweep_level": None}

        recent_df = df.iloc[-lookback:].copy()
        high_level = float(recent_df["high"].iloc[:-2].max())
        low_level = float(recent_df["low"].iloc[:-2].min())

        last_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]

        bsl_swept = False
        ssl_swept = False
        sweep_level = None

        # BSL sweep: Wicked above previous high, closed below it
        if (last_candle["high"] > high_level and last_candle["close"] < high_level) or \
           (prev_candle["high"] > high_level and last_candle["close"] < high_level):
            bsl_swept = True
            sweep_level = high_level

        # SSL sweep: Wicked below previous low, closed above it
        if (last_candle["low"] < low_level and last_candle["close"] > low_level) or \
           (prev_candle["low"] < low_level and last_candle["close"] > low_level):
            ssl_swept = True
            sweep_level = low_level

        return {
            "bsl_swept": bsl_swept,
            "ssl_swept": ssl_swept,
            "sweep_level": sweep_level,
            "major_high": high_level,
            "major_low": low_level
        }

    def compute_ote_and_equilibrium(self, swing_high: float, swing_low: float, trend: str) -> Dict[str, Any]:
        """
        Computes 50% Equilibrium and Optimal Trade Entry (OTE: 0.618, 0.705, 0.786).
        """
        range_span = swing_high - swing_low
        equilibrium = swing_low + 0.5 * range_span

        if trend == "BULLISH":
            # Retracement down into discount zone
            fib_618 = swing_high - 0.618 * range_span
            fib_705 = swing_high - 0.705 * range_span
            fib_786 = swing_high - 0.786 * range_span
            return {
                "equilibrium": round(equilibrium, 4),
                "ote_min": round(min(fib_618, fib_786), 4),
                "ote_max": round(max(fib_618, fib_786), 4),
                "sweet_spot": round(fib_705, 4),
                "zone_type": "DISCOUNT"
            }
        else:
            # Retracement up into premium zone
            fib_618 = swing_low + 0.618 * range_span
            fib_705 = swing_low + 0.705 * range_span
            fib_786 = swing_low + 0.786 * range_span
            return {
                "equilibrium": round(equilibrium, 4),
                "ote_min": round(min(fib_618, fib_786), 4),
                "ote_max": round(max(fib_618, fib_786), 4),
                "sweet_spot": round(fib_705, 4),
                "zone_type": "PREMIUM"
            }

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Runs full SMC/ICT suite and returns structured signal summary."""
        structure = self.detect_structure(df)
        fvgs = self.detect_fair_value_gaps(df)
        obs = self.detect_order_blocks(df)
        sweeps = self.detect_liquidity_sweeps(df)

        unmitigated_fvgs = [f for f in fvgs if not f["is_mitigated"]]
        unmitigated_obs = [o for o in obs if not o["is_mitigated"]]

        ote = self.compute_ote_and_equilibrium(
            structure["last_swing_high"],
            structure["last_swing_low"],
            structure["trend"]
        )

        return {
            "structure": structure,
            "unmitigated_fvgs": unmitigated_fvgs,
            "unmitigated_obs": unmitigated_obs,
            "liquidity_sweeps": sweeps,
            "ote_levels": ote
        }
