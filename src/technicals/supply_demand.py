"""
Supply and Demand Zone Engine.
Detects:
- Rally-Base-Drop (RBD) & Drop-Base-Rally (DBR) reversal zones
- Rally-Base-Rally (RBR) & Drop-Base-Drop (DBD) continuation zones
- Base candle count filtering (<= 3 candles)
- Departure impulse strength (>= 2 * ATR_14)
- Zone Freshness & Mitigation Tracking
"""

from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np


class SupplyDemandEngine:
    def __init__(self, atr_period: int = 14, min_impulse_multiplier: float = 2.0, max_base_candles: int = 3):
        self.atr_period = atr_period
        self.min_impulse_multiplier = min_impulse_multiplier
        self.max_base_candles = max_base_candles

    def calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"].shift(1)
        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=self.atr_period, min_periods=1).mean()

    def detect_zones(self, df: pd.DataFrame, max_lookback: int = 60) -> List[Dict[str, Any]]:
        """
        Scans dataframe for structural Supply & Demand formations:
        - DBR (Demand Reversal)
        - RBD (Supply Reversal)
        - RBR (Demand Continuation)
        - DBD (Supply Continuation)
        """
        df = df.copy()
        df["atr"] = self.calculate_atr(df)
        zones = []
        n = len(df)
        start_idx = max(self.atr_period + 4, n - max_lookback)

        for i in range(start_idx, n - 2):
            atr_val = float(df["atr"].iloc[i])
            if atr_val <= 0:
                continue

            # Check departure impulse at candle i+1 or i+2
            departure_body = abs(df["close"].iloc[i + 1] - df["open"].iloc[i + 1])
            is_departure_impulsive = departure_body >= (self.min_impulse_multiplier * atr_val)

            if not is_departure_impulsive:
                continue

            departure_bullish = df["close"].iloc[i + 1] > df["open"].iloc[i + 1]

            # Analyze preceding 1 to max_base_candles for base formation
            # Base candle: candle body <= 0.6 * atr_val
            base_candles = []
            for b_offset in range(0, self.max_base_candles):
                idx = i - b_offset
                if idx < 1:
                    break
                b_body = abs(df["close"].iloc[idx] - df["open"].iloc[idx])
                if b_body <= (0.75 * atr_val):
                    base_candles.append(idx)
                else:
                    break

            if len(base_candles) == 0:
                continue

            base_top = float(df["high"].iloc[base_candles].max())
            base_bottom = float(df["low"].iloc[base_candles].min())

            # Look at incoming candle before the base
            incoming_idx = min(base_candles) - 1
            if incoming_idx < 0:
                continue

            incoming_bullish = df["close"].iloc[incoming_idx] > df["open"].iloc[incoming_idx]

            # Classify pattern
            pattern_type = None
            zone_category = None

            if departure_bullish:
                if incoming_bullish:
                    pattern_type = "RALLY_BASE_RALLY"
                    zone_category = "DEMAND"
                else:
                    pattern_type = "DROP_BASE_RALLY"
                    zone_category = "DEMAND"
            else:
                if incoming_bullish:
                    pattern_type = "RALLY_BASE_DROP"
                    zone_category = "SUPPLY"
                else:
                    pattern_type = "DROP_BASE_DROP"
                    zone_category = "SUPPLY"

            # Check freshness / mitigation from i+2 to current
            test_count = 0
            mitigated = False
            for test_idx in range(i + 2, n):
                t_high = float(df["high"].iloc[test_idx])
                t_low = float(df["low"].iloc[test_idx])

                if zone_category == "DEMAND":
                    if t_low <= base_top and t_low >= base_bottom:
                        test_count += 1
                    elif t_low < base_bottom:
                        mitigated = True
                        break
                elif zone_category == "SUPPLY":
                    if t_high >= base_bottom and t_high <= base_top:
                        test_count += 1
                    elif t_high > base_top:
                        mitigated = True
                        break

            current_price = float(df["close"].iloc[-1])
            dist_to_zone = (current_price - base_top) if zone_category == "DEMAND" else (base_bottom - current_price)

            zones.append({
                "pattern": pattern_type,
                "category": zone_category,
                "base_top": base_top,
                "base_bottom": base_bottom,
                "invalidation_price": base_bottom if zone_category == "DEMAND" else base_top,
                "base_candle_count": len(base_candles),
                "departure_impulse_ratio": round(departure_body / atr_val, 2),
                "test_count": test_count,
                "is_fresh": (test_count == 0 and not mitigated),
                "is_mitigated": mitigated,
                "distance_to_current_price": round(dist_to_zone, 4)
            })

        return zones

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Returns structured Supply/Demand analysis."""
        all_zones = self.detect_zones(df)
        fresh_demand = [z for z in all_zones if z["category"] == "DEMAND" and z["is_fresh"]]
        fresh_supply = [z for z in all_zones if z["category"] == "SUPPLY" and z["is_fresh"]]

        current_price = float(df["close"].iloc[-1])

        # Nearest fresh demand below price
        valid_demand = [d for d in fresh_demand if d["base_top"] <= current_price]
        nearest_demand = max(valid_demand, key=lambda x: x["base_top"]) if valid_demand else None

        # Nearest fresh supply above price
        valid_supply = [s for s in fresh_supply if s["base_bottom"] >= current_price]
        nearest_supply = min(valid_supply, key=lambda x: x["base_bottom"]) if valid_supply else None

        return {
            "all_zones": all_zones,
            "fresh_demand_zones": fresh_demand,
            "fresh_supply_zones": fresh_supply,
            "nearest_demand": nearest_demand,
            "nearest_supply": nearest_supply
        }
