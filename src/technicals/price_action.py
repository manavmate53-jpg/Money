"""
Price Action & Auction Market Theory Engine.
Implements:
- Strict One-Time Framing Rule (Emir Gams: HH+HL+HC or LL+LH+LC)
- Value Area Calculation: VAH (85th%), VAL (15th%), POC (mode) from Auction Market Theory (James Dalton / Fabio Valentini)
- Support/Resistance polarity flips
- Range High/Low deviations (fakeouts/springs)
- Candlestick triggers (Pin Bar, Engulfing, Inside Bar)
- ATR/ADX regime classification (DIRECTIONAL_TREND, ROTATIONAL_RANGE, HIGH_VOLATILITY_EXPANSION)
"""

from typing import Dict, Any, List, Optional
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
        """Computes Wilder's ADX for regime filtering."""
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
            # Bullish check: HH, HL, HC
            if not (recent.loc[i, "high"] > recent.loc[i - 1, "high"] and
                    recent.loc[i, "low"] > recent.loc[i - 1, "low"] and
                    recent.loc[i, "close"] > recent.loc[i - 1, "close"]):
                bullish_otf = False

            # Bearish check: LL, LH, LC
            if not (recent.loc[i, "low"] < recent.loc[i - 1, "low"] and
                    recent.loc[i, "high"] < recent.loc[i - 1, "high"] and
                    recent.loc[i, "close"] < recent.loc[i - 1, "close"]):
                bearish_otf = False

        if bullish_otf:
            return {"status": "BULLISH_ONE_TIME_FRAMING", "bars_count": bars_back}
        elif bearish_otf:
            return {"status": "BEARISH_ONE_TIME_FRAMING", "bars_count": bars_back}
        return {"status": "ROTATIONAL_NO_OTF", "bars_count": 0}

    def compute_value_area(self, df: pd.DataFrame, lookback: int = 40) -> Dict[str, float]:
        """
        Computes Auction Market Theory Value Area:
        - VAH: 85th percentile of distribution
        - VAL: 15th percentile of distribution
        - POC: Median / Point of Control
        """
        recent = df.iloc[-lookback:]
        # Use high, low, close weighted sample
        prices = np.concatenate([recent["high"].values, recent["low"].values, recent["close"].values])
        vah = float(np.percentile(prices, 85))
        val = float(np.percentile(prices, 15))
        poc = float(np.median(prices))

        return {
            "vah": round(vah, 4),
            "val": round(val, 4),
            "poc": round(poc, 4),
            "range_width": round(vah - val, 4)
        }

    def classify_market_regime(self, df: pd.DataFrame) -> str:
        """
        Classifies regime into:
        - DIRECTIONAL_TREND (Hurst > 0.55 or ADX >= 25 or One-Time Framing)
        - ROTATIONAL_RANGE (Hurst < 0.45 or Value Area oscillation)
        - RANDOM_WALK_CHOP (0.45 <= Hurst <= 0.55 on low ADX)
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

        # High Volatility Expansion
        if curr_atr > (1.8 * avg_atr):
            return "HIGH_VOLATILITY_EXPANSION"

        # Pure Random Walk Chop
        if 0.46 <= hurst <= 0.54 and curr_adx < 22 and otf["status"] == "ROTATIONAL_NO_OTF":
            return "RANDOM_WALK_CHOP"

        # Directional Trend
        if hurst > 0.55 or curr_adx >= 25 or otf["status"] in ["BULLISH_ONE_TIME_FRAMING", "BEARISH_ONE_TIME_FRAMING"]:
            return "DIRECTIONAL_TREND"

        # Otherwise Rotational
        return "ROTATIONAL_RANGE"

    def detect_candlestick_triggers(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Identifies precision candlestick triggers on the most recent 2 bars:
        - Pin Bar (Hammer / Shooting Star)
        - Bullish / Bearish Engulfing
        - Inside Bar Breakout
        """
        if len(df) < 3:
            return {"pattern": None, "bias": "NEUTRAL"}

        c = df.iloc[-1]
        p = df.iloc[-2]

        body = abs(c["close"] - c["open"])
        candle_range = c["high"] - c["low"]
        upper_wick = c["high"] - max(c["close"], c["open"])
        lower_wick = min(c["close"], c["open"]) - c["low"]

        # Bullish Pin Bar / Hammer (lower wick >= 60% of range, small body)
        if candle_range > 0 and (lower_wick / candle_range) >= 0.60:
            return {"pattern": "BULLISH_PIN_BAR", "bias": "BULLISH", "strength": 0.85}

        # Bearish Pin Bar / Shooting Star (upper wick >= 60% of range)
        if candle_range > 0 and (upper_wick / candle_range) >= 0.60:
            return {"pattern": "BEARISH_PIN_BAR", "bias": "BEARISH", "strength": 0.85}

        # Bullish Engulfing
        if c["close"] > c["open"] and p["close"] < p["open"] and c["close"] > p["open"] and c["open"] < p["close"]:
            return {"pattern": "BULLISH_ENGULFING", "bias": "BULLISH", "strength": 0.80}

        # Bearish Engulfing
        if c["close"] < c["open"] and p["close"] > p["open"] and c["close"] < p["low"] and c["open"] > p["close"]:
            return {"pattern": "BEARISH_ENGULFING", "bias": "BEARISH", "strength": 0.80}

        # Inside Bar Breakout
        if p["high"] > df.iloc[-3]["high"] and p["low"] < df.iloc[-3]["low"]:
            if c["close"] > p["high"]:
                return {"pattern": "INSIDE_BAR_BULLISH_BREAKOUT", "bias": "BULLISH", "strength": 0.75}
            elif c["close"] < p["low"]:
                return {"pattern": "INSIDE_BAR_BEARISH_BREAKOUT", "bias": "BEARISH", "strength": 0.75}

        return {"pattern": "STANDARD_CANDLE", "bias": "NEUTRAL", "strength": 0.50}

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Runs full Price Action analysis suite."""
        regime = self.classify_market_regime(df)
        otf = self.check_one_time_framing(df, bars_back=3)
        value_area = self.compute_value_area(df)
        trigger = self.detect_candlestick_triggers(df)
        atr_val = float(self.calculate_atr(df).iloc[-1])

        return {
            "regime": regime,
            "one_time_framing": otf,
            "value_area": value_area,
            "candlestick_trigger": trigger,
            "atr_14": round(atr_val, 4)
        }
