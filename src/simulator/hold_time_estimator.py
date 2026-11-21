"""
Trade Hold Duration Estimator.
Predicts trade hold duration using ATR_14 velocity, target distance,
and momentum decay factors.
"""

from typing import Dict, Any


class HoldTimeEstimator:
    def __init__(self, momentum_compression_factor: float = 0.85):
        self.momentum_compression_factor = momentum_compression_factor

    def estimate_hold_time(
        self,
        entry_price: float,
        tp_price: float,
        atr_14: float,
        timeframe_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        Estimated Candles = (|TP - Entry| / ATR_14) * momentum_factor
        Estimated Hours = (Estimated Candles * timeframe_minutes) / 60
        """
        if atr_14 <= 0:
            atr_14 = 1.0

        distance = abs(tp_price - entry_price)
        raw_candles = distance / atr_14
        adjusted_candles = max(1.0, raw_candles * self.momentum_compression_factor)

        total_minutes = adjusted_candles * timeframe_minutes
        estimated_hours = round(total_minutes / 60.0, 1)

        # Human-readable formatted string
        if estimated_hours < 1.0:
            formatted = f"{int(total_minutes)} minutes ({int(round(adjusted_candles))} candles)"
        elif estimated_hours <= 24.0:
            formatted = f"{estimated_hours:.1f} hours ({int(round(adjusted_candles))} H1 candles)"
        else:
            days = round(estimated_hours / 24.0, 1)
            formatted = f"{days} days (~{estimated_hours:.0f} hrs)"

        return {
            "estimated_candles": int(round(adjusted_candles)),
            "estimated_hours": estimated_hours,
            "estimated_minutes": int(total_minutes),
            "formatted": formatted
        }
