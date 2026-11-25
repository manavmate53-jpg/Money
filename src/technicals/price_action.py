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

    def calculate_adx(self, df: pd.DataFrame) -> pd.Series:
        """Computes Wilder's ADX for trend strength & regime filtering."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = high.diff()
        minus_dm = low.diff().abs()

        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        plus_dm[plus_dm <= minus_dm] = 0
        minus_dm[minus_dm <= plus_dm] = 0

        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(self.adx_period, min_periods=1).mean()

        plus_di = 100 * (plus_dm.rolling(self.adx_period, min_periods=1).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(self.adx_period, min_periods=1).mean() / atr)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        adx = dx.rolling(self.adx_period, min_periods=1).mean()
        return adx.fillna(20.0)

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

    def classify_market_regime(self, df: pd.DataFrame) -> str:
        """
        Classifies pure price regime into:
        - DIRECTIONAL_TREND (Hurst > 0.55 or ADX >= 25 or One-Time Framing)
        - ROTATIONAL_RANGE (Hurst < 0.45 or range-bound oscillation)
        - RANDOM_WALK_CHOP (0.46 <= Hurst <= 0.54 on low ADX)
        - HIGH_VOLATILITY_EXPANSION (ATR > 1.8 * avg ATR)
        """
        from src.risk.quant_engine import QuantEngine
        quant = QuantEngine()

        atr_series = self.calculate_atr(df)
        adx_series = self.calculate_adx(df)

        curr_atr = float(atr_series.iloc[-1])
        avg_atr = float(atr_series.iloc[-50:].mean()) if len(atr_series) >= 50 else curr_atr
        curr_adx = float(adx_series.iloc[-1])

        otf = self.check_one_time_framing(df, bars_back=2)
        hurst = quant.calculate_hurst_exponent(df["close"].tolist())

        if curr_atr > (1.8 * avg_atr):
            return "HIGH_VOLATILITY_EXPANSION"
        if 0.46 <= hurst <= 0.54 and curr_adx < 22 and otf["status"] == "ROTATIONAL_NO_OTF":
            return "RANDOM_WALK_CHOP"
        if hurst > 0.55 or curr_adx >= 25 or otf["status"] in ["BULLISH_ONE_TIME_FRAMING", "BEARISH_ONE_TIME_FRAMING"]:
            return "DIRECTIONAL_TREND"
        return "ROTATIONAL_RANGE"

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
        regime = self.classify_market_regime(df)
        otf = self.check_one_time_framing(df, bars_back=3)
        dealing_range = self.compute_dealing_range(df)
        sr_flips = self.detect_sr_polarity_flips(df)
        trigger = self.detect_candlestick_triggers(df)
        atr_val = float(self.calculate_atr(df).iloc[-1])

        return {
            "regime": regime,
            "one_time_framing": otf,
            "dealing_range": dealing_range,
            "sr_polarity_flips": sr_flips,
            "candlestick_trigger": trigger,
            "atr_14": round(atr_val, 4)
        }
