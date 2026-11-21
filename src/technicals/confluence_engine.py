"""
Multi-Framework Confluence Engine.
Integrates:
- Engine A: SMC / ICT
- Engine B: Supply & Demand
- Engine C: Price Action & Auction Theory
with dynamic strategy_weights.json and knowledge_base/ rules.
Produces structured, deterministic Trading Tips.
"""

import json
import os
from typing import Dict, Any, Optional
import pandas as pd
from pydantic import BaseModel, Field

from src.technicals.smc_ict import SmcIctEngine
from src.technicals.supply_demand import SupplyDemandEngine
from src.technicals.price_action import PriceActionEngine
from src.simulator.hold_time_estimator import HoldTimeEstimator


class TradingTip(BaseModel):
    symbol: str
    direction: str # BULLISH, BEARISH, NEUTRAL_NO_TRADE
    confidence_score: float # 0.0 to 1.0
    primary_strategy: str
    secondary_strategy: Optional[str] = None
    market_regime: str
    current_price: float
    entry_min: float
    entry_max: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    sl_points: float
    tp1_points: float
    risk_reward_ratio: float
    break_even_trigger_price: float
    estimated_hold_hours: float
    estimated_hold_candles: int
    confluence_reasons: list[str] = Field(default_factory=list)
    champions_rule_match: Optional[str] = None


class ConfluenceEngine:
    def __init__(self, weights_path: str = "config/strategy_weights.json"):
        self.weights_path = weights_path
        self.smc = SmcIctEngine()
        self.sd = SupplyDemandEngine()
        self.pa = PriceActionEngine()
        self.hold_estimator = HoldTimeEstimator()
        self.weights = self._load_weights()

    def _load_weights(self) -> Dict[str, Any]:
        if os.path.exists(self.weights_path):
            with open(self.weights_path, "r") as f:
                return json.load(f)
        return {
            "default_framework_weights": {
                "SMC_ICT": 0.40,
                "SUPPLY_DEMAND": 0.35,
                "PRICE_ACTION_AUCTION": 0.25
            }
        }

    def get_instrument_weights(self, symbol: str, regime: str) -> Dict[str, float]:
        instruments = self.weights.get("instruments", {})
        if symbol in instruments and regime in instruments[symbol]:
            spec_w = instruments[symbol][regime]
            return {
                "SMC_ICT": spec_w.get("SMC_ICT", 0.40),
                "SUPPLY_DEMAND": spec_w.get("SUPPLY_DEMAND", 0.35),
                "PRICE_ACTION_AUCTION": spec_w.get("PRICE_ACTION_AUCTION", 0.25)
            }
        return self.weights.get("default_framework_weights", {
            "SMC_ICT": 0.40,
            "SUPPLY_DEMAND": 0.35,
            "PRICE_ACTION_AUCTION": 0.25
        })

    def generate_tip(self, symbol: str, df: pd.DataFrame) -> TradingTip:
        """
        Runs SMC, Supply/Demand, and Price Action engines on dataframe.
        Calculates confluence score, structural entry/SL/TP, and hold duration.
        """
        current_price = float(df["close"].iloc[-1])

        # Run 3 engines independently
        smc_res = self.smc.analyze(df)
        sd_res = self.sd.analyze(df)
        pa_res = self.pa.analyze(df)

        regime = pa_res["regime"]
        weights = self.get_instrument_weights(symbol, regime)
        atr_14 = pa_res["atr_14"] or 1.0

        bullish_score = 0.0
        bearish_score = 0.0
        reasons = []
        champions_match = None

        # 1. Evaluate SMC / ICT
        smc_trend = smc_res["structure"]["trend"]
        sweeps = smc_res["liquidity_sweeps"]
        w_smc = weights["SMC_ICT"]

        if sweeps["ssl_swept"]:
            bullish_score += 1.0 * w_smc
            reasons.append(f"ICT: Sell-Side Liquidity (SSL) swept at {sweeps['sweep_level']:.2f}")
        elif sweeps["bsl_swept"]:
            bearish_score += 1.0 * w_smc
            reasons.append(f"ICT: Buy-Side Liquidity (BSL) swept at {sweeps['sweep_level']:.2f}")
        elif smc_trend == "BULLISH":
            bullish_score += 0.6 * w_smc
            reasons.append("ICT: Bullish market structure with higher swing highs and higher swing lows")
        elif smc_trend == "BEARISH":
            bearish_score += 0.6 * w_smc
            reasons.append("ICT: Bearish market structure with lower swing highs and lower swing lows")

        # 2. Evaluate Supply & Demand
        w_sd = weights["SUPPLY_DEMAND"]
        nearest_d = sd_res["nearest_demand"]
        nearest_s = sd_res["nearest_supply"]

        if nearest_d and nearest_d["is_fresh"]:
            dist_to_d = current_price - nearest_d["base_top"]
            if dist_to_d <= (1.5 * atr_14):
                bullish_score += 0.8 * w_sd
                reasons.append(f"S&D: Price near unmitigated {nearest_d['pattern']} demand zone [{nearest_d['base_bottom']:.2f} - {nearest_d['base_top']:.2f}]")
        if nearest_s and nearest_s["is_fresh"]:
            dist_to_s = nearest_s["base_bottom"] - current_price
            if dist_to_s <= (1.5 * atr_14):
                bearish_score += 0.8 * w_sd
                reasons.append(f"S&D: Price near unmitigated {nearest_s['pattern']} supply zone [{nearest_s['base_bottom']:.2f} - {nearest_s['base_top']:.2f}]")

        # 3. Evaluate Price Action & Auction Theory
        w_pa = weights["PRICE_ACTION_AUCTION"]
        otf = pa_res["one_time_framing"]["status"]
        va = pa_res["value_area"]
        trigger = pa_res["candlestick_trigger"]

        # Fabio Valentini Rotational vs Directional filter
        if regime == "ROTATIONAL_RANGE":
            champions_match = "Fabio Valentini: Rotational Range Value Area Mean Reversion"
            if current_price <= va["val"] + (0.3 * va["range_width"]):
                bullish_score += 0.9 * w_pa
                reasons.append(f"Auction: Price in absorption zone at Value Area Low ({va['val']:.2f})")
            elif current_price >= va["vah"] - (0.3 * va["range_width"]):
                bearish_score += 0.9 * w_pa
                reasons.append(f"Auction: Price in absorption zone at Value Area High ({va['vah']:.2f})")
        else:
            # Emir Gams One-Time Framing
            if otf == "BULLISH_ONE_TIME_FRAMING":
                champions_match = "Emir Gams: Strict Bullish One-Time Framing (HH+HL+HC)"
                bullish_score += 1.0 * w_pa
                reasons.append("Price Action: Emir Gams Strict Bullish One-Time Framing confirmed")
            elif otf == "BEARISH_ONE_TIME_FRAMING":
                champions_match = "Emir Gams: Strict Bearish One-Time Framing (LL+LH+LC)"
                bearish_score += 1.0 * w_pa
                reasons.append("Price Action: Emir Gams Strict Bearish One-Time Framing confirmed")

        if trigger["bias"] == "BULLISH":
            bullish_score += 0.4 * w_pa
            reasons.append(f"Candlestick Trigger: {trigger['pattern']}")
        elif trigger["bias"] == "BEARISH":
            bearish_score += 0.4 * w_pa
            reasons.append(f"Candlestick Trigger: {trigger['pattern']}")

        # Determine directional bias
        threshold = 0.45
        if bullish_score > bearish_score and bullish_score >= threshold:
            direction = "BULLISH"
            confidence = min(0.95, bullish_score)
            primary_strat = "SMC_ICT" if sweeps["ssl_swept"] or smc_trend == "BULLISH" else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION_AUCTION"

            # Structural Entry & Invalidation
            if nearest_d:
                entry_min = round(nearest_d["base_bottom"], 2)
                entry_max = round(nearest_d["base_top"], 2)
                sl = round(nearest_d["base_bottom"] - (0.5 * atr_14), 2)
            else:
                entry_min = round(current_price - (0.3 * atr_14), 2)
                entry_max = round(current_price, 2)
                sl = round(current_price - (1.5 * atr_14), 2)

            sl_dist = abs(entry_max - sl)
            # Planned R:R >= 1:2.5
            tp1 = round(entry_max + (2.5 * sl_dist), 2)
            tp2 = round(entry_max + (4.0 * sl_dist), 2)
            be_trigger = round(entry_max + (1.0 * sl_dist), 2)

        elif bearish_score > bullish_score and bearish_score >= threshold:
            direction = "BEARISH"
            confidence = min(0.95, bearish_score)
            primary_strat = "SMC_ICT" if sweeps["bsl_swept"] or smc_trend == "BEARISH" else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION_AUCTION"

            if nearest_s:
                entry_min = round(nearest_s["base_bottom"], 2)
                entry_max = round(nearest_s["base_top"], 2)
                sl = round(nearest_s["base_top"] + (0.5 * atr_14), 2)
            else:
                entry_min = round(current_price, 2)
                entry_max = round(current_price + (0.3 * atr_14), 2)
                sl = round(current_price + (1.5 * atr_14), 2)

            sl_dist = abs(sl - entry_min)
            tp1 = round(entry_min - (2.5 * sl_dist), 2)
            tp2 = round(entry_min - (4.0 * sl_dist), 2)
            be_trigger = round(entry_min - (1.0 * sl_dist), 2)

        else:
            direction = "NEUTRAL_NO_TRADE"
            confidence = 0.30
            primary_strat = "NONE"
            secondary_strat = None
            entry_min = current_price
            entry_max = current_price
            sl = current_price
            tp1 = current_price
            tp2 = current_price
            be_trigger = current_price
            sl_dist = 0.0

        sl_points = abs(entry_max - sl) if direction != "NEUTRAL_NO_TRADE" else 0.0
        tp1_points = abs(tp1 - entry_max) if direction != "NEUTRAL_NO_TRADE" else 0.0
        rr = round(tp1_points / sl_points, 2) if sl_points > 0 else 0.0

        hold_est = self.hold_estimator.estimate_hold_time(
            entry_price=entry_max if direction == "BULLISH" else entry_min,
            tp_price=tp1,
            atr_14=atr_14,
            timeframe_minutes=60
        )

        return TradingTip(
            symbol=symbol,
            direction=direction,
            confidence_score=round(confidence, 2),
            primary_strategy=primary_strat,
            secondary_strategy=secondary_strat,
            market_regime=regime,
            current_price=round(current_price, 4),
            entry_min=entry_min,
            entry_max=entry_max,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2,
            sl_points=round(sl_points, 2),
            tp1_points=round(tp1_points, 2),
            risk_reward_ratio=rr,
            break_even_trigger_price=be_trigger,
            estimated_hold_hours=hold_est["estimated_hours"],
            estimated_hold_candles=hold_est["estimated_candles"],
            confluence_reasons=reasons,
            champions_rule_match=champions_match
        )
