"""
Multi-Framework Confluence Engine.
Integrates SMC/ICT, Supply & Demand, and Price Action / Auction Theory
with machine-enforced Council rules (config/active_filters.json), Bayesian
weights (config/strategy_weights.json), mandatory H4 macro bias alignment,
2-candle liquidity sweep confirmation, and 0.25 * ATR_14(M15) entry bands.
"""

import json
import logging
import os
from typing import Dict, Any, Optional, List
import pandas as pd
from pydantic import BaseModel, Field

from src.technicals.smc_ict import SmcIctEngine
from src.technicals.supply_demand import SupplyDemandEngine
from src.technicals.price_action import PriceActionEngine
from src.simulator.hold_time_estimator import HoldTimeEstimator

logger = logging.getLogger(__name__)


class TradingTip(BaseModel):
    symbol: str
    direction: str  # BULLISH, BEARISH, NEUTRAL_NO_TRADE
    confidence_score: float
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
    def __init__(
        self,
        weights_path: str = "config/strategy_weights.json",
        filters_path: str = "config/active_filters.json",
    ):
        self.weights_path = weights_path
        self.filters_path = filters_path
        self.smc = SmcIctEngine()
        self.sd = SupplyDemandEngine()
        self.pa = PriceActionEngine()
        self.hold_estimator = HoldTimeEstimator()
        self.weights = self._load_weights()
        self.active_filters = self._load_filters()

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

    def _load_filters(self) -> Dict[str, Any]:
        if os.path.exists(self.filters_path):
            try:
                with open(self.filters_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed loading {self.filters_path}: {e}")
        return {
            "min_sweep_confirmation_candles": 2,
            "max_pending_hours": 6,
            "min_rr_ratio": 2.0,
            "atr_sl_buffer_multiplier": 0.5,
            "entry_zone_m15_atr_width": 0.25,
            "require_macro_bias_alignment": True,
            "blocked_regimes_by_symbol": {},
            "symbol_overrides": {}
        }

    def reload_filters(self):
        self.active_filters = self._load_filters()
        self.weights = self._load_weights()

    def _get_symbol_filter_val(self, symbol: str, key: str, default: Any) -> Any:
        overrides = self.active_filters.get("symbol_overrides", {}).get(symbol, {})
        if key in overrides:
            return overrides[key]
        return self.active_filters.get(key, default)

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

    def _neutral_tip(self, symbol: str, regime: str, current_price: float, atr_14: float, reasons: List[str]) -> TradingTip:
        hold_est = self.hold_estimator.estimate_hold_time(current_price, current_price, atr_14, 60)
        return TradingTip(
            symbol=symbol,
            direction="NEUTRAL_NO_TRADE",
            confidence_score=0.30,
            primary_strategy="NONE",
            secondary_strategy=None,
            market_regime=regime,
            current_price=round(current_price, 4),
            entry_min=round(current_price, 2),
            entry_max=round(current_price, 2),
            stop_loss=round(current_price, 2),
            take_profit_1=round(current_price, 2),
            take_profit_2=round(current_price, 2),
            sl_points=0.0,
            tp1_points=0.0,
            risk_reward_ratio=0.0,
            break_even_trigger_price=round(current_price, 2),
            estimated_hold_hours=hold_est["estimated_hours"],
            estimated_hold_candles=hold_est["estimated_candles"],
            confluence_reasons=reasons,
            champions_rule_match=None
        )

    def generate_tip(
        self,
        symbol: str,
        df: pd.DataFrame,
        m15_df: Optional[pd.DataFrame] = None,
        macro_bias: Optional[str] = None,
        h4_mss_confirmed: bool = False,
    ) -> TradingTip:
        """
        Runs SMC, Supply/Demand, and Price Action engines on dataframe, enforcing active
        Council rules, H4 macro bias alignment, 2-candle sweep confirmation, and M15 ATR entry bands.
        """
        self.reload_filters()
        current_price = float(df["close"].iloc[-1])

        smc_res = self.smc.analyze(df)
        sd_res = self.sd.analyze(df)
        pa_res = self.pa.analyze(df)

        regime = pa_res["regime"]
        atr_14 = pa_res["atr_14"] or 1.0
        m15_atr = atr_14 * 0.5
        if m15_df is not None and len(m15_df) >= 10:
            m15_atr_series = self.pa.calculate_atr(m15_df)
            m15_atr = float(m15_atr_series.iloc[-1]) or m15_atr

        # Rule check: Blocked regimes per symbol from Council active_filters.json
        blocked_regimes = self.active_filters.get("blocked_regimes_by_symbol", {}).get(symbol, [])
        if regime in blocked_regimes:
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [f"Council Filter: Regime '{regime}' blocked for {symbol} by active_filters.json"]
            )

        weights = self.get_instrument_weights(symbol, regime)
        min_confirm = int(self._get_symbol_filter_val(symbol, "min_sweep_confirmation_candles", 2))
        min_rr = float(self._get_symbol_filter_val(symbol, "min_rr_ratio", 2.0))
        sl_buf_mult = float(self._get_symbol_filter_val(symbol, "atr_sl_buffer_multiplier", 0.5))
        zone_m15_mult = float(self._get_symbol_filter_val(symbol, "entry_zone_m15_atr_width", 0.25))
        require_macro = bool(self.active_filters.get("require_macro_bias_alignment", True))

        bullish_score = 0.0
        bearish_score = 0.0
        reasons: List[str] = []
        champions_match = None

        # 1. SMC / ICT + Sweep Confirmation Check
        smc_trend = smc_res["structure"]["trend"]
        sweeps = smc_res["liquidity_sweeps"]
        # Use M15 sweep confirmation if M15 candles are available alongside H1 sweep
        m15_sweeps = self.smc.detect_liquidity_sweeps(m15_df) if (m15_df is not None and len(m15_df) >= 20) else None
        w_smc = weights["SMC_ICT"]

        ssl_confirmed = sweeps["ssl_swept"] and (
            sweeps.get("confirmation_candles", 0) >= min_confirm
            or (m15_sweeps and m15_sweeps.get("ssl_swept") and m15_sweeps.get("confirmation_candles", 0) >= min_confirm)
        )
        bsl_confirmed = sweeps["bsl_swept"] and (
            sweeps.get("confirmation_candles", 0) >= min_confirm
            or (m15_sweeps and m15_sweeps.get("bsl_swept") and m15_sweeps.get("confirmation_candles", 0) >= min_confirm)
        )

        if ssl_confirmed:
            bullish_score += 1.0 * w_smc
            conf_cnt = max(sweeps.get("confirmation_candles", 0), m15_sweeps.get("confirmation_candles", 0) if m15_sweeps else 0)
            reasons.append(f"ICT: SSL swept at {sweeps['sweep_level']:.2f} with {conf_cnt}-candle displacement confirmation")
        elif sweeps["ssl_swept"]:
            reasons.append(f"ICT Watch: SSL wicked at {sweeps['sweep_level']:.2f}; awaiting {min_confirm}-candle confirmation")

        if bsl_confirmed:
            bearish_score += 1.0 * w_smc
            conf_cnt = max(sweeps.get("confirmation_candles", 0), m15_sweeps.get("confirmation_candles", 0) if m15_sweeps else 0)
            reasons.append(f"ICT: BSL swept at {sweeps['sweep_level']:.2f} with {conf_cnt}-candle displacement confirmation")
        elif sweeps["bsl_swept"]:
            reasons.append(f"ICT Watch: BSL wicked at {sweeps['sweep_level']:.2f}; awaiting {min_confirm}-candle confirmation")

        if not ssl_confirmed and not bsl_confirmed:
            if smc_trend == "BULLISH":
                bullish_score += 0.6 * w_smc
                reasons.append("ICT: Bullish market structure with higher swing highs and higher swing lows")
            elif smc_trend == "BEARISH":
                bearish_score += 0.6 * w_smc
                reasons.append("ICT: Bearish market structure with lower swing highs and lower swing lows")

        # 2. Supply & Demand
        w_sd = weights["SUPPLY_DEMAND"]
        nearest_d = sd_res["nearest_demand"]
        nearest_s = sd_res["nearest_supply"]

        if nearest_d and nearest_d["is_fresh"]:
            if (current_price - nearest_d["base_top"]) <= (1.5 * atr_14):
                bullish_score += 0.8 * w_sd
                reasons.append(f"S&D: Price near unmitigated {nearest_d['pattern']} demand zone [{nearest_d['base_bottom']:.2f} - {nearest_d['base_top']:.2f}]")
        if nearest_s and nearest_s["is_fresh"]:
            if (nearest_s["base_bottom"] - current_price) <= (1.5 * atr_14):
                bearish_score += 0.8 * w_sd
                reasons.append(f"S&D: Price near unmitigated {nearest_s['pattern']} supply zone [{nearest_s['base_bottom']:.2f} - {nearest_s['base_top']:.2f}]")

        # 3. Price Action & Auction Theory
        w_pa = weights["PRICE_ACTION_AUCTION"]
        otf = pa_res["one_time_framing"]["status"]
        va = pa_res["value_area"]
        trigger = pa_res["candlestick_trigger"]

        if regime == "ROTATIONAL_RANGE":
            champions_match = "Fabio Valentini: Rotational Range Value Area Mean Reversion"
            if current_price <= va["val"] + (0.3 * va["range_width"]):
                bullish_score += 0.9 * w_pa
                reasons.append(f"Auction: Price in absorption zone at Value Area Low ({va['val']:.2f})")
            elif current_price >= va["vah"] - (0.3 * va["range_width"]):
                bearish_score += 0.9 * w_pa
                reasons.append(f"Auction: Price in absorption zone at Value Area High ({va['vah']:.2f})")
        else:
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

        # Threshold evaluation
        threshold = 0.45
        if regime == "RANDOM_WALK_CHOP":
            threshold = 0.65
            reasons.append("Hurst Dynamics: 0.46 <= H <= 0.54 Brownian chop detected; elevated threshold enforced")

        candidate_dir = "NEUTRAL_NO_TRADE"
        if bullish_score > bearish_score and bullish_score >= threshold:
            candidate_dir = "BULLISH"
        elif bearish_score > bullish_score and bearish_score >= threshold:
            candidate_dir = "BEARISH"

        # Mandatory H4 Macro Bias Alignment Gate
        if candidate_dir != "NEUTRAL_NO_TRADE" and macro_bias is not None and require_macro:
            mb = macro_bias.upper()
            has_mss = h4_mss_confirmed or bool(smc_res["structure"].get("mss"))
            if mb == "NEUTRAL" and not has_mss:
                reasons.append("Macro Gate: Rejected setup because H4 macro bias is NEUTRAL without MSS")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)
            if mb in ("BULLISH", "BEARISH") and candidate_dir != mb and not has_mss:
                reasons.append(f"Macro Gate: Rejected counter-macro {candidate_dir} setup (H4 bias is {mb})")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        # ATR-calibrated zone width >= 0.25 * ATR_14(M15) to avoid micro-thin unfillable limit bands
        atr_band_width = max(round(zone_m15_mult * m15_atr, 2), round(atr_14 * 0.15, 2), 0.10)

        if candidate_dir == "BULLISH":
            direction = "BULLISH"
            confidence = min(0.95, bullish_score)
            primary_strat = "SMC_ICT" if ssl_confirmed or smc_trend == "BULLISH" else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION_AUCTION"

            if nearest_d:
                anchor_top = nearest_d["base_top"]
                raw_bottom = nearest_d["base_bottom"]
                entry_max = round(anchor_top, 2)
                entry_min = round(min(raw_bottom, anchor_top - atr_band_width), 2)
                sl = round(entry_min - (sl_buf_mult * atr_14), 2)
            else:
                # Size pullback band around current price using 0.25 * ATR_14(M15)
                entry_max = round(current_price, 2)
                entry_min = round(current_price - atr_band_width, 2)
                sl = round(entry_max - (1.5 * atr_14), 2)

            sl_dist = abs(entry_max - sl)
            rr_target = max(2.5, min_rr)
            tp1 = round(entry_max + (rr_target * sl_dist), 2)
            tp2 = round(entry_max + (max(4.0, rr_target + 1.5) * sl_dist), 2)
            be_trigger = round(entry_max + (1.0 * sl_dist), 2)

        elif candidate_dir == "BEARISH":
            direction = "BEARISH"
            confidence = min(0.95, bearish_score)
            primary_strat = "SMC_ICT" if bsl_confirmed or smc_trend == "BEARISH" else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION_AUCTION"

            if nearest_s:
                anchor_bottom = nearest_s["base_bottom"]
                raw_top = nearest_s["base_top"]
                entry_min = round(anchor_bottom, 2)
                entry_max = round(max(raw_top, anchor_bottom + atr_band_width), 2)
                sl = round(entry_max + (sl_buf_mult * atr_14), 2)
            else:
                entry_min = round(current_price, 2)
                entry_max = round(current_price + atr_band_width, 2)
                sl = round(entry_min + (1.5 * atr_14), 2)

            sl_dist = abs(sl - entry_min)
            rr_target = max(2.5, min_rr)
            tp1 = round(entry_min - (rr_target * sl_dist), 2)
            tp2 = round(entry_min - (max(4.0, rr_target + 1.5) * sl_dist), 2)
            be_trigger = round(entry_min - (1.0 * sl_dist), 2)

        else:
            return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        entry_ref = entry_max if direction == "BULLISH" else entry_min
        sl_points = abs(entry_ref - sl)
        tp1_points = abs(tp1 - entry_ref)
        rr = round(tp1_points / sl_points, 2) if sl_points > 0 else 0.0

        if rr < min_rr:
            reasons.append(f"Filter: Planned R:R {rr:.2f} below active_filters min_rr_ratio {min_rr:.2f}")
            return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        hold_est = self.hold_estimator.estimate_hold_time(
            entry_price=entry_ref,
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
