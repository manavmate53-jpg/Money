"""
Deriv Pip/Tick & Margin Calculator.
Exact mathematical implementation of Deriv Synthetic Indices tick sizes,
point values, required margin, and PnL conversions.
"""

import json
import os
from typing import Dict, Any, Optional


class PipCalculator:
    def __init__(self, specs_path: str = "config/deriv_specs.json"):
        self.specs_path = specs_path
        self.specs = self._load_specs()

    def _load_specs(self) -> Dict[str, Any]:
        if os.path.exists(self.specs_path):
            with open(self.specs_path, "r") as f:
                data = json.load(f)
                return data.get("instruments", {})
        return {}

    SYMBOL_ALIASES = {
        "STPRNG": "STEPINDEX",
    }

    def get_spec(self, symbol: str) -> Dict[str, Any]:
        resolved = self.SYMBOL_ALIASES.get(symbol.upper(), symbol)
        if resolved in self.specs:
            return self.specs[resolved]
        if symbol in self.specs:
            return self.specs[symbol]
        # Safe default spec if unknown symbol
        return {
            "display_name": symbol,
            "symbol": symbol,
            "contract_size": 1.0,
            "tick_size": 0.01,
            "point_value": 1.0,
            "min_lot": 0.01,
            "max_lot": 50.0,
            "volume_step": 0.001,
            "margin_percent": 0.1,
            "leverage": 1000,
            "average_spread_points": 0.05
        }

    def calculate_point_distance(self, price_1: float, price_2: float) -> float:
        """Calculates absolute point distance between two prices."""
        return abs(price_1 - price_2)

    def calculate_dollar_risk(self, entry_price: float, stop_loss_price: float, lot_size: float, symbol: str) -> float:
        """
        Exact Deriv Dollar Risk:
        Dollar Risk ($) = |Entry - SL| * Contract Size * Point Value * Lot Size
        """
        spec = self.get_spec(symbol)
        contract_size = spec.get("contract_size", 1.0)
        point_value = spec.get("point_value", 1.0)
        points = abs(entry_price - stop_loss_price)
        return points * contract_size * point_value * lot_size

    def calculate_raw_lot_size(self, dollar_risk: float, entry_price: float, stop_loss_price: float, symbol: str) -> float:
        """
        Raw Lot Size = Dollar Risk / (SL Distance * Contract Size * Point Value)
        """
        spec = self.get_spec(symbol)
        contract_size = spec.get("contract_size", 1.0)
        point_value = spec.get("point_value", 1.0)
        points = abs(entry_price - stop_loss_price)
        if points <= 0 or contract_size <= 0 or point_value <= 0:
            return spec.get("min_lot", 0.01)
        raw_lot = dollar_risk / (points * contract_size * point_value)
        return raw_lot

    def round_to_step(self, raw_lot: float, symbol: str) -> float:
        """
        Rounds lot size down to the nearest volume_step, respecting min_lot and max_lot.
        """
        spec = self.get_spec(symbol)
        min_lot = spec.get("min_lot", 0.005)
        max_lot = spec.get("max_lot", 50.0)
        volume_step = spec.get("volume_step", 0.001)

        if raw_lot < min_lot:
            return min_lot

        # Stepped rounding down to avoid over-risking
        steps = int(raw_lot / volume_step)
        final_lot = steps * volume_step
        final_lot = max(min_lot, min(max_lot, final_lot))

        # Precision handling for floats
        decimals = 3
        if volume_step >= 0.01:
            decimals = 2
        if volume_step >= 0.1:
            decimals = 1
        return round(final_lot, decimals)

    def calculate_required_margin(self, entry_price: float, lot_size: float, symbol: str) -> float:
        """
        Required Margin ($) = (Contract Size * Entry Price * Lot Size) / Effective Leverage
        """
        spec = self.get_spec(symbol)
        contract_size = spec.get("contract_size", 1.0)
        leverage = spec.get("leverage", 1000)
        if leverage <= 0:
            leverage = 1000
        margin = (contract_size * entry_price * lot_size) / leverage
        return round(margin, 4)

    def calculate_pnl(self, direction: str, entry_price: float, exit_price: float, lot_size: float, symbol: str) -> float:
        """
        Calculates realized dollar PnL given direction (BUY or SELL).
        """
        spec = self.get_spec(symbol)
        contract_size = spec.get("contract_size", 1.0)
        point_value = spec.get("point_value", 1.0)

        if direction.upper() in ["BUY", "BULLISH", "LONG"]:
            price_diff = exit_price - entry_price
        else:
            price_diff = entry_price - exit_price

        dollar_pnl = price_diff * contract_size * point_value * lot_size
        return round(dollar_pnl, 4)
