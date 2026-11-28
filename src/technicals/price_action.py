"""
Pure Price Action & Candle Structure Engine (Zero Orderflow / Zero Volume Profile).
Implements:
- Strict One-Time Framing Rule (Emir Gams: HH+HL+HC or LL+LH+LC)
- Support/Resistance Polarity Flips (Break & Retest levels from pure OHLC pivots)
- Pure Range 50% Equilibrium & Dealing Range bounds
- Candlestick Rejection Triggers (Pin Bar, Engulfing, Inside Bar Breakout)
- ATR/ADX/Hurst Regime Classification (DIRECTIONAL_TREND, ROTATIONAL_RANGE,
  RANDOM_WALK_CHOP, HIGH_VOLATILITY_EXPANSION)
"""

from typing import Dict, Any, List
import pandas as pd
import numpy as np


class PriceActionEngine:
    def __init__(self, atr_period: int = 14, adx_period: int = 14):
        self.atr_period = atr_period
        self.adx_period = adx_period

    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"].shift(1)
        tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period, min_periods=1).mean()

    def get_directional_indicators(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Computes Wilder's ADX(14), +DI, and -DI series & latest readings."""
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)

        plus_dm = high.diff()
        minus_dm = (-low.diff())

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(self.adx_period, min_periods=1).mean().replace(0, 1e-6)

        plus_di = 100.0 * (plus_dm.rolling(self.adx_period, min_periods=1).mean() / atr)
        minus_di = 100.0 * (minus_dm.rolling(self.adx_period, min_periods=1).mean() / atr)

        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1.0)
        adx = dx.rolling(self.adx_period, min_periods=1).mean().fillna(20.0)
        return {
            "adx_series": adx,
            "adx": float(adx.iloc[-1]),
            "plus_di": float(plus_di.iloc[-1]) if pd.notna(plus_di.iloc[-1]) else 20.0,
            "minus_di": float(minus_di.iloc[-1]) if pd.notna(minus_di.iloc[-1]) else 20.0,
        }

    def calculate_adx(self, df: pd.DataFrame) -> pd.Series:
        """Computes Wilder's ADX for trend strength & regime filtering."""
        return self.get_directional_indicators(df)["adx_series"]

    def calculate_efficiency_ratio(self, df: pd.DataFrame, period: int = 20) -> float:
        """
        Kaufman Efficiency Ratio (ER) = |Close_t - Close_{t-N}| / sum(|Close_i - Close_{i-1}|) in [0, 1].
        """
        if df is None or len(df) < 3:
            return 0.0
        n_eff = min(period, len(df) - 1)
        closes = df["close"].astype(float).iloc[-(n_eff + 1):].to_numpy()
        net_move = abs(float(closes[-1] - closes[0]))
        path_len = float(np.sum(np.abs(np.diff(closes))))
        if path_len <= 1e-9:
            return 0.0
        return round(min(1.0, max(0.0, net_move / path_len)), 4)

    def check_one_time_framing_streak(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Counts consecutive HH+HL+HC bullish or LL+LH+LC bearish streak bars from latest close."""
        if df is None or len(df) < 2:
            return {"status": "ROTATIONAL_NO_OTF", "streak": 0, "direction": "NEUTRAL"}

        bull_streak = 0
        for i in range(len(df) - 1, 0, -1):
            c, p = df.iloc[i], df.iloc[i - 1]
            if float(c["high"]) > float(p["high"]) and float(c["low"]) > float(p["low"]) and float(c["close"]) > float(p["close"]):
                bull_streak += 1
            else:
                break

        bear_streak = 0
        for i in range(len(df) - 1, 0, -1):
            c, p = df.iloc[i], df.iloc[i - 1]
            if float(c["low"]) < float(p["low"]) and float(c["high"]) < float(p["high"]) and float(c["close"]) < float(p["close"]):
                bear_streak += 1
            else:
                break

        if bull_streak >= bear_streak and bull_streak >= 2:
            return {"status": "BULLISH_ONE_TIME_FRAMING", "streak": bull_streak, "direction": "BULLISH"}
        if bear_streak > bull_streak and bear_streak >= 2:
            return {"status": "BEARISH_ONE_TIME_FRAMING", "streak": bear_streak, "direction": "BEARISH"}
        return {"status": "ROTATIONAL_NO_OTF", "streak": max(bull_streak, bear_streak), "direction": "NEUTRAL"}

    def check_one_time_framing(self, df: pd.DataFrame, bars_back: int = 3) -> Dict[str, Any]:
        """
        Emir Gams' Strict One-Time Framing rule:
        Bullish: Consecutive bars print Higher High, Higher Low, and Higher Close.
        Bearish: Consecutive bars print Lower Low, Lower High, and Lower Close.
        """
        if len(df) < bars_back + 1:
            return {"status": "NONE", "bars_count": 0}

        recent = df.iloc[-bars_back - 1:].reset_index(drop=True)
        bullish_otf = True
        bearish_otf = True

        for i in range(1, len(recent)):
            if not (recent.loc[i, "high"] > recent.loc[i - 1, "high"] and
                    recent.loc[i, "low"] > recent.loc[i - 1, "low"] and
                    recent.loc[i, "close"] > recent.loc[i - 1, "close"]):
                bullish_otf = False

            if not (recent.loc[i, "low"] < recent.loc[i - 1, "low"] and
                    recent.loc[i, "high"] < recent.loc[i - 1, "high"] and
                    recent.loc[i, "close"] < recent.loc[i - 1, "close"]):
                bearish_otf = False

        if bullish_otf:
            return {"status": "BULLISH_ONE_TIME_FRAMING", "bars_count": bars_back}
        elif bearish_otf:
            return {"status": "BEARISH_ONE_TIME_FRAMING", "bars_count": bars_back}
        return {"status": "ROTATIONAL_NO_OTF", "bars_count": 0}

    def compute_dealing_range(self, df: pd.DataFrame, lookback: int = 40) -> Dict[str, float]:
        """
        Pure Price-Action Dealing Range & 50% Equilibrium (replaces volume-based Value Area).
        """
        recent = df.iloc[-lookback:]
        range_high = float(recent["high"].max())
        range_low = float(recent["low"].min())
        equilibrium = (range_high + range_low) / 2.0
        return {
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
            "equilibrium": round(equilibrium, 4),
            "range_width": round(range_high - range_low, 4),
        }

    def detect_sr_polarity_flips(self, df: pd.DataFrame, lookback: int = 50) -> Dict[str, Any]:
        """
        Detects pure OHLC Support-to-Resistance and Resistance-to-Support polarity flips.
        """
        if len(df) < 15:
            return {"bullish_flip": None, "bearish_flip": None}

        recent = df.iloc[-lookback:].reset_index(drop=True)
        curr_close = float(recent["close"].iloc[-1])
        atr_val = float(self.calculate_atr(recent).iloc[-1]) or 1.0

        bullish_flip = None
        bearish_flip = None

        # Check swing pivots broken with close and currently near retest zone
        for i in range(2, len(recent) - 5):
            sh = float(recent.loc[i, "high"])
            if sh >= float(recent.loc[i - 1, "high"]) and sh >= float(recent.loc[i + 1, "high"]):
                broke_above = any(float(recent.loc[k, "close"]) > sh for k in range(i + 2, len(recent) - 1))
                if broke_above and curr_close >= sh - (0.3 * atr_val) and curr_close <= sh + (1.2 * atr_val):
                    bullish_flip = round(sh, 4)

            sl = float(recent.loc[i, "low"])
            if sl <= float(recent.loc[i - 1, "low"]) and sl <= float(recent.loc[i + 1, "low"]):
                broke_below = any(float(recent.loc[k, "close"]) < sl for k in range(i + 2, len(recent) - 1))
                if broke_below and curr_close <= sl + (0.3 * atr_val) and curr_close >= sl - (1.2 * atr_val):
                    bearish_flip = round(sl, 4)

        return {"bullish_flip": bullish_flip, "bearish_flip": bearish_flip}

    def analyze_regime_metrics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Strict quantitative regime discrimination using Hurst Exponent (100 bars),
        Wilder ADX(14) + DI spread, Kaufman Efficiency Ratio(20), and One-Time Framing streak:
        - DIRECTIONAL_TREND: Hurst > 0.55 AND ADX >= 24.0 AND ER >= 0.30
        - RANDOM_WALK_CHOP: 0.46 <= Hurst <= 0.55 AND ADX < 24.0 (forces NEUTRAL)
        - ROTATIONAL_RANGE: Hurst < 0.46 OR (ADX < 20.0 and non-trending)
        Computes a continuous non-quantized confidence score in [0.40, 0.94].
        """
        from src.risk.quant_engine import QuantEngine
        quant = QuantEngine()

        atr_series = self.calculate_atr(df)
        di_info = self.get_directional_indicators(df)
        curr_atr = float(atr_series.iloc[-1])
        avg_atr = float(atr_series.iloc[-50:].mean()) if len(atr_series) >= 50 else curr_atr
        curr_adx = di_info["adx"]
        plus_di = di_info["plus_di"]
        minus_di = di_info["minus_di"]

        er = self.calculate_efficiency_ratio(df, period=20)
        otf_streak = self.check_one_time_framing_streak(df)
        hurst = quant.calculate_hurst_exponent(df["close"].astype(float).tail(100).tolist())

        if hurst > 0.55 and curr_adx >= 24.0 and er >= 0.30:
            regime = "DIRECTIONAL_TREND"
        elif 0.46 <= hurst <= 0.55 and curr_adx < 24.0:
            regime = "RANDOM_WALK_CHOP"
        elif hurst < 0.46 or curr_adx < 20.0:
            regime = "ROTATIONAL_RANGE"
        elif curr_atr > (1.8 * avg_atr):
            regime = "HIGH_VOLATILITY_EXPANSION"
        else:
            regime = "ROTATIONAL_RANGE"

        # Continuous non-quantized confidence in [0.40, 0.94]
        h_term = min(1.0, max(0.0, (hurst - 0.42) / 0.32))
        adx_term = min(1.0, max(0.0, (curr_adx - 12.0) / 34.0))
        er_term = min(1.0, max(0.0, er / 0.65))
        di_spread = min(1.0, abs(plus_di - minus_di) / 25.0)
        otf_term = min(1.0, otf_streak["streak"] / 5.0)
        raw_blend = (0.28 * h_term) + (0.26 * adx_term) + (0.24 * er_term) + (0.12 * di_spread) + (0.10 * otf_term)

        quant_conf = 0.40 + (0.54 * raw_blend)
        if regime == "RANDOM_WALK_CHOP":
            quant_conf = min(0.52, quant_conf)
            bias_hint = "NEUTRAL"
        elif regime == "ROTATIONAL_RANGE":
            quant_conf = min(0.70, quant_conf)
            bias_hint = "NEUTRAL" if abs(plus_di - minus_di) < 5.0 else ("BULLISH" if plus_di > minus_di else "BEARISH")
        else:
            if otf_streak["direction"] in ("BULLISH", "BEARISH"):
                bias_hint = otf_streak["direction"]
            else:
                bias_hint = "BULLISH" if plus_di >= minus_di else "BEARISH"

        return {
            "regime": regime,
            "hurst": round(hurst, 3),
            "adx_14": round(curr_adx, 2),
            "plus_di": round(plus_di, 2),
            "minus_di": round(minus_di, 2),
            "efficiency_ratio": round(er, 4),
            "otf_streak": otf_streak["streak"],
            "otf_status": otf_streak["status"],
            "bias_hint": bias_hint,
            "quant_confidence": round(min(0.94, max(0.40, quant_conf)), 3),
        }

    def classify_market_regime(self, df: pd.DataFrame) -> str:
        """Returns strict regime classification via Hurst, ADX, and Efficiency Ratio."""
        return self.analyze_regime_metrics(df)["regime"]

    def detect_candlestick_triggers(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Identifies pure candlestick triggers on the most recent 2 bars:
        - Pin Bar (Hammer / Shooting Star)
        - Bullish / Bearish Engulfing
        - Inside Bar Breakout
        """
        if len(df) < 3:
            return {"pattern": None, "bias": "NEUTRAL"}

        c = df.iloc[-1]
        p = df.iloc[-2]

        candle_range = c["high"] - c["low"]
        upper_wick = c["high"] - max(c["close"], c["open"])
        lower_wick = min(c["close"], c["open"]) - c["low"]

        if candle_range > 0 and (lower_wick / candle_range) >= 0.60:
            return {"pattern": "BULLISH_PIN_BAR", "bias": "BULLISH", "strength": 0.85}
        if candle_range > 0 and (upper_wick / candle_range) >= 0.60:
            return {"pattern": "BEARISH_PIN_BAR", "bias": "BEARISH", "strength": 0.85}
        if c["close"] > c["open"] and p["close"] < p["open"] and c["close"] > p["high"] and c["open"] <= p["close"]:
            return {"pattern": "BULLISH_ENGULFING", "bias": "BULLISH", "strength": 0.80}
        if c["close"] < c["open"] and p["close"] > p["open"] and c["close"] < p["low"] and c["open"] >= p["close"]:
            return {"pattern": "BEARISH_ENGULFING", "bias": "BEARISH", "strength": 0.80}
        if p["high"] < df.iloc[-3]["high"] and p["low"] > df.iloc[-3]["low"]:
            if c["close"] > p["high"]:
                return {"pattern": "INSIDE_BAR_BULLISH_BREAKOUT", "bias": "BULLISH", "strength": 0.75}
            elif c["close"] < p["low"]:
                return {"pattern": "INSIDE_BAR_BEARISH_BREAKOUT", "bias": "BEARISH", "strength": 0.75}

        return {"pattern": "STANDARD_CANDLE", "bias": "NEUTRAL", "strength": 0.50}

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Runs full Pure Price Action analysis suite."""
        regime_info = self.analyze_regime_metrics(df)
        otf = self.check_one_time_framing(df, bars_back=3)
        dealing_range = self.compute_dealing_range(df)
        sr_flips = self.detect_sr_polarity_flips(df)
        trigger = self.detect_candlestick_triggers(df)
        atr_val = float(self.calculate_atr(df).iloc[-1])

        return {
            "regime": regime_info["regime"],
            "regime_metrics": regime_info,
            "one_time_framing": otf,
            "dealing_range": dealing_range,
            "sr_polarity_flips": sr_flips,
            "candlestick_trigger": trigger,
            "atr_14": round(atr_val, 4)
        }
