"""
Multi-Framework PrimeFX Liquidity, POI & Pure Price-Action Confluence Engine.
Zero Volume Profile (VAH/VAL), zero DOM, zero orderflow assumptions.
Integrates:
- PrimeFX LiquidityEngine (BSL/SSL, EQH/EQL, PDH/PDL, P4HH/P4HL, Sweep vs Breakout, Inducement Trap)
- PoiPriceActionEngine (H4->H1->M15 OB, FVG, IFVG, Breaker Blocks, OTE Equilibrium)
- Pure PriceActionEngine (MSS displacement, S/R polarity flips, One-Time Framing, Pin Bar/Engulfing)
- Institutional Risk & Jump-Index Volatility Guards + Executable Council Rules (active_filters.json).
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
from src.technicals.liquidity_engine import LiquidityEngine
from src.technicals.poi_price_action import PoiPriceActionEngine
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
        self.liq = LiquidityEngine()
        self.poi_engine = PoiPriceActionEngine()
        self.hold_estimator = HoldTimeEstimator()
        self.weights = self._load_weights()
        self.active_filters = self._load_filters()

    def _load_weights(self) -> Dict[str, Any]:
        if os.path.exists(self.weights_path):
            with open(self.weights_path, "r") as f:
                return json.load(f)
        return {
            "default_framework_weights": {
                "SMC_ICT": 0.45,
                "SUPPLY_DEMAND": 0.30,
                "PRICE_ACTION": 0.25
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
            "revenge_cooldown_hours": 2.0,
            "require_inducement_sweep": True,
            "jump_index_min_sl_atr_mult": 1.5,
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
                "SMC_ICT": spec_w.get("SMC_ICT", 0.45),
                "SUPPLY_DEMAND": spec_w.get("SUPPLY_DEMAND", 0.30),
                "PRICE_ACTION": spec_w.get("PRICE_ACTION", spec_w.get("PRICE_ACTION_AUCTION", 0.25)),
            }
        d = self.weights.get("default_framework_weights", {})
        return {
            "SMC_ICT": d.get("SMC_ICT", 0.45),
            "SUPPLY_DEMAND": d.get("SUPPLY_DEMAND", 0.30),
            "PRICE_ACTION": d.get("PRICE_ACTION", d.get("PRICE_ACTION_AUCTION", 0.25)),
        }

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
        h4_df: Optional[pd.DataFrame] = None,
        d1_df: Optional[pd.DataFrame] = None,
        macro_bias: Optional[str] = None,
        h4_mss_confirmed: bool = False,
        hours_since_last_loss: Optional[float] = None,
    ) -> TradingTip:
        """
        Pure Price-Action & PrimeFX Liquidity/POI Setup Generator.
        Enforces Inducement clearing, Liquidity Sweep 2-candle confirmation,
        Anti-Revenge Cooldown, H4 Macro Alignment, M15 ATR zone calibration, and Jump Index SL guard.
        """
        self.reload_filters()
        current_price = float(df["close"].iloc[-1])

        pa_res = self.pa.analyze(df)
        regime = pa_res["regime"]
        atr_14 = pa_res["atr_14"] or 1.0
        m15_atr = atr_14 * 0.5
        if m15_df is not None and len(m15_df) >= 10:
            m15_atr = float(self.pa.calculate_atr(m15_df).iloc[-1]) or m15_atr

        # Rule 1: Anti-Revenge Cooldown check (block same symbol for 2h after SL hit)
        cooldown_h = float(self.active_filters.get("revenge_cooldown_hours", 2.0))
        if hours_since_last_loss is not None and hours_since_last_loss < cooldown_h:
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [f"Anti-Revenge Shield: Blocked re-entry on {symbol} ({hours_since_last_loss:.1f}h < {cooldown_h:.1f}h post-SL cooldown)"]
            )

        # Rule 2: Blocked regimes per symbol
        blocked_regimes = self.active_filters.get("blocked_regimes_by_symbol", {}).get(symbol, [])
        if regime in blocked_regimes:
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [f"Council Filter: Regime '{regime}' blocked for {symbol} by active_filters.json"]
            )

        # Run Liquidity & Multi-TF POI Engines
        liq_res = self.liq.analyze(df, df_h4=h4_df, df_d1=d1_df, df_m15=m15_df)
        poi_res = self.poi_engine.analyze(df, df_h4=h4_df, df_m15=m15_df)
        sd_res = self.sd.analyze(df)
        smc_res = self.smc.analyze(df)

        weights = self.get_instrument_weights(symbol, regime)
        min_confirm = int(self._get_symbol_filter_val(symbol, "min_sweep_confirmation_candles", 2))
        min_rr = float(self._get_symbol_filter_val(symbol, "min_rr_ratio", 2.0))
        sl_buf_mult = float(self._get_symbol_filter_val(symbol, "atr_sl_buffer_multiplier", 0.5))
        zone_m15_mult = float(self._get_symbol_filter_val(symbol, "entry_zone_m15_atr_width", 0.25))
        require_macro = bool(self.active_filters.get("require_macro_bias_alignment", True))
        require_idm = bool(self.active_filters.get("require_inducement_sweep", True))
        is_jump = symbol.upper().startswith("JD")
        jump_sl_mult = float(self.active_filters.get("jump_index_min_sl_atr_mult", 1.5))

        bullish_score = 0.0
        bearish_score = 0.0
        reasons: List[str] = []
        champions_match = None

        # 1. PrimeFX Liquidity Sweep & 2-Candle Confirmation Scoring
        w_smc = weights["SMC_ICT"]
        bull_sweep = liq_res.get("bullish_sweep")
        bear_sweep = liq_res.get("bearish_sweep")
        smc_trend = smc_res["structure"]["trend"]

        bull_sweep_confirmed = bool(bull_sweep and bull_sweep.get("confirmation_candles", 0) >= min_confirm)
        bear_sweep_confirmed = bool(bear_sweep and bear_sweep.get("confirmation_candles", 0) >= min_confirm)

        if bull_sweep_confirmed:
            bullish_score += 1.0 * w_smc
            champions_match = f"PrimeFX Liquidity Raid: {bull_sweep['pool_type']} Swept + {bull_sweep['confirmation_candles']}-Bar Hold"
            reasons.append(f"Liquidity: {bull_sweep['pool_type']} swept at {bull_sweep['level']:.2f} ({bull_sweep['confirmation_candles']}-candle hold)")
        elif bull_sweep:
            reasons.append(f"Liquidity Watch: {bull_sweep['pool_type']} wicked at {bull_sweep['level']:.2f}; awaiting {min_confirm}-candle hold")

        if bear_sweep_confirmed:
            bearish_score += 1.0 * w_smc
            champions_match = f"PrimeFX Liquidity Raid: {bear_sweep['pool_type']} Swept + {bear_sweep['confirmation_candles']}-Bar Hold"
            reasons.append(f"Liquidity: {bear_sweep['pool_type']} swept at {bear_sweep['level']:.2f} ({bear_sweep['confirmation_candles']}-candle hold)")
        elif bear_sweep:
            reasons.append(f"Liquidity Watch: {bear_sweep['pool_type']} wicked at {bear_sweep['level']:.2f}; awaiting {min_confirm}-candle hold")

        if not bull_sweep_confirmed and not bear_sweep_confirmed:
            if smc_trend == "BULLISH":
                bullish_score += 0.6 * w_smc
                reasons.append("Structure: Bullish HH/HL structure progression")
            elif smc_trend == "BEARISH":
                bearish_score += 0.6 * w_smc
                reasons.append("Structure: Bearish LL/LH structure progression")

        # 2. PrimeFX Multi-Timeframe POI & Inducement Gate (OB / FVG / IFVG / Breaker + S&D)
        w_sd = weights["SUPPLY_DEMAND"]
        best_bull_poi = poi_res.get("best_bullish_poi")
        best_bear_poi = poi_res.get("best_bearish_poi")
        trap_bull = poi_res.get("blocked_bullish_trap")
        trap_bear = poi_res.get("blocked_bearish_trap")

        if best_bull_poi and best_bull_poi["distance_atr"] <= 1.8:
            bullish_score += 0.85 * w_sd
            reasons.append(
                f"POI: {best_bull_poi['timeframe']} {best_bull_poi['poi_kind']} [{best_bull_poi['zone_bottom']:.2f}-{best_bull_poi['zone_top']:.2f}] ({best_bull_poi['inducement_status']})"
            )
        elif trap_bull and require_idm and not bull_sweep_confirmed:
            bullish_score -= 0.15
            reasons.append(f"Inducement Warning: Unswept IDM at {trap_bull['idm_level']} resting in front of Bullish POI")

        if best_bear_poi and best_bear_poi["distance_atr"] <= 1.8:
            bearish_score += 0.85 * w_sd
            reasons.append(
                f"POI: {best_bear_poi['timeframe']} {best_bear_poi['poi_kind']} [{best_bear_poi['zone_bottom']:.2f}-{best_bear_poi['zone_top']:.2f}] ({best_bear_poi['inducement_status']})"
            )
        elif trap_bear and require_idm and not bear_sweep_confirmed:
            bearish_score -= 0.15
            reasons.append(f"Inducement Warning: Unswept IDM at {trap_bear['idm_level']} resting in front of Bearish POI")

        # S&D Fresh Zone boost
        nearest_d = sd_res["nearest_demand"]
        nearest_s = sd_res["nearest_supply"]
        if nearest_d and nearest_d["is_fresh"] and (current_price - nearest_d["base_top"]) <= (1.5 * atr_14):
            bullish_score += 0.25 * w_sd
        if nearest_s and nearest_s["is_fresh"] and (nearest_s["base_bottom"] - current_price) <= (1.5 * atr_14):
            bearish_score += 0.25 * w_sd

        # 3. Pure Price Action (One-Time Framing, S/R Polarity Flip, Candlesticks — NO Volume Profile!)
        w_pa = weights["PRICE_ACTION"]
        otf = pa_res["one_time_framing"]["status"]
        sr_flips = pa_res["sr_polarity_flips"]
        trigger = pa_res["candlestick_trigger"]
        dealing_range = pa_res["dealing_range"]

        if otf == "BULLISH_ONE_TIME_FRAMING":
            champions_match = champions_match or "Emir Gams: Strict Bullish One-Time Framing (HH+HL+HC)"
            bullish_score += 0.9 * w_pa
            reasons.append("Price Action: Strict Bullish One-Time Framing (HH+HL+HC)")
        elif otf == "BEARISH_ONE_TIME_FRAMING":
            champions_match = champions_match or "Emir Gams: Strict Bearish One-Time Framing (LL+LH+LC)"
            bearish_score += 0.9 * w_pa
            reasons.append("Price Action: Strict Bearish One-Time Framing (LL+LH+LC)")

        if sr_flips.get("bullish_flip"):
            bullish_score += 0.5 * w_pa
            reasons.append(f"Pure PA: Resistance-to-Support polarity flip @ {sr_flips['bullish_flip']:.2f}")
        if sr_flips.get("bearish_flip"):
            bearish_score += 0.5 * w_pa
            reasons.append(f"Pure PA: Support-to-Resistance polarity flip @ {sr_flips['bearish_flip']:.2f}")

        # Pure Discount/Premium 50% equilibrium alignment
        eq_50 = dealing_range["equilibrium"]
        if current_price <= eq_50:
            bullish_score += 0.2 * w_pa
        else:
            bearish_score += 0.2 * w_pa

        if trigger["bias"] == "BULLISH":
            bullish_score += 0.4 * w_pa
            reasons.append(f"Candlestick Trigger: {trigger['pattern']}")
        elif trigger["bias"] == "BEARISH":
            bearish_score += 0.4 * w_pa
            reasons.append(f"Candlestick Trigger: {trigger['pattern']}")

        # Threshold gating
        threshold = 0.45
        if regime == "RANDOM_WALK_CHOP":
            threshold = 0.65
            reasons.append("Hurst Dynamics: 0.46 <= H <= 0.54 chop detected; elevated entry barrier enforced")

        candidate_dir = "NEUTRAL_NO_TRADE"
        if bullish_score > bearish_score and bullish_score >= threshold:
            candidate_dir = "BULLISH"
        elif bearish_score > bullish_score and bearish_score >= threshold:
            candidate_dir = "BEARISH"

        # Rule 4: Block pure pre-inducement trap entries if candidate lacks IDM clearing & lacks sweep
        if require_idm:
            if candidate_dir == "BULLISH" and trap_bull and not best_bull_poi and not bull_sweep_confirmed:
                reasons.append(f"Inducement Gate: Blocked BUY trap before IDM ({trap_bull['idm_level']}) sweep")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)
            if candidate_dir == "BEARISH" and trap_bear and not best_bear_poi and not bear_sweep_confirmed:
                reasons.append(f"Inducement Gate: Blocked SELL trap before IDM ({trap_bear['idm_level']}) sweep")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        # Rule 5: Mandatory H4 Macro Bias Alignment Gate
        if candidate_dir != "NEUTRAL_NO_TRADE" and macro_bias is not None and require_macro:
            mb = macro_bias.upper()
            has_mss = h4_mss_confirmed or bool(smc_res["structure"].get("mss"))
            if mb == "NEUTRAL" and not has_mss:
                reasons.append("Macro Gate: Rejected setup because H4 macro bias is NEUTRAL without MSS")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)
            if mb in ("BULLISH", "BEARISH") and candidate_dir != mb and not has_mss:
                reasons.append(f"Macro Gate: Rejected counter-macro {candidate_dir} setup (H4 bias is {mb})")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        # Entry zone calibration: 0.25 * ATR_14(M15) around POI/current price
        atr_band_width = max(round(zone_m15_mult * m15_atr, 2), round(atr_14 * 0.15, 2), 0.10)
        pools = liq_res["pools"]

        if candidate_dir == "BULLISH":
            direction = "BULLISH"
            confidence = min(0.95, bullish_score)
            primary_strat = "SMC_ICT" if (bull_sweep_confirmed or smc_trend == "BULLISH") else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION"

            anchor_zone = best_bull_poi or ({"zone_bottom": nearest_d["base_bottom"], "zone_top": nearest_d["base_top"]} if nearest_d else None)
            if anchor_zone:
                entry_max = round(anchor_zone["zone_top"], 2)
                entry_min = round(min(anchor_zone["zone_bottom"], entry_max - atr_band_width), 2)
                raw_sl = entry_min - (sl_buf_mult * atr_14)
            else:
                entry_max = round(current_price, 2)
                entry_min = round(current_price - atr_band_width, 2)
                raw_sl = entry_max - (1.5 * atr_14)

            # Sweep-extreme & Jump-Index SL Guard (>= 1.5 * ATR_14 beyond sweep extreme)
            sweep_ext = bull_sweep["wick_extreme"] if bull_sweep else None
            if is_jump or sweep_ext is not None:
                min_jump_dist = (jump_sl_mult if is_jump else 1.0) * atr_14
                floor_candidates = [raw_sl, entry_max - min_jump_dist]
                if sweep_ext is not None:
                    floor_candidates.append(sweep_ext - (0.35 * atr_14))
                sl = round(min(floor_candidates), 2)
            else:
                sl = round(raw_sl, 2)

            sl_dist = abs(entry_max - sl)
            rr_target = max(2.5, min_rr)
            # Aim TP1 at min R:R or opposing liquidity pool (BSL/PDH/EQH)
            tp1_rr = round(entry_max + (rr_target * sl_dist), 2)
            ext_targets = [t for t in [pools["pdh"], pools["p4hh"], *pools["eqh"], *pools["bsl_pools"]] if t > tp1_rr]
            tp1 = tp1_rr
            tp2 = round(min(ext_targets), 2) if ext_targets else round(entry_max + (max(4.0, rr_target + 1.5) * sl_dist), 2)
            be_trigger = round(entry_max + (1.0 * sl_dist), 2)

        elif candidate_dir == "BEARISH":
            direction = "BEARISH"
            confidence = min(0.95, bearish_score)
            primary_strat = "SMC_ICT" if (bear_sweep_confirmed or smc_trend == "BEARISH") else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION"

            anchor_zone = best_bear_poi or ({"zone_bottom": nearest_s["base_bottom"], "zone_top": nearest_s["base_top"]} if nearest_s else None)
            if anchor_zone:
                entry_min = round(anchor_zone["zone_bottom"], 2)
                entry_max = round(max(anchor_zone["zone_top"], entry_min + atr_band_width), 2)
                raw_sl = entry_max + (sl_buf_mult * atr_14)
            else:
                entry_min = round(current_price, 2)
                entry_max = round(current_price + atr_band_width, 2)
                raw_sl = entry_min + (1.5 * atr_14)

            sweep_ext = bear_sweep["wick_extreme"] if bear_sweep else None
            if is_jump or sweep_ext is not None:
                min_jump_dist = (jump_sl_mult if is_jump else 1.0) * atr_14
                ceil_candidates = [raw_sl, entry_min + min_jump_dist]
                if sweep_ext is not None:
                    ceil_candidates.append(sweep_ext + (0.35 * atr_14))
                sl = round(max(ceil_candidates), 2)
            else:
                sl = round(raw_sl, 2)

            sl_dist = abs(sl - entry_min)
            rr_target = max(2.5, min_rr)
            tp1_rr = round(entry_min - (rr_target * sl_dist), 2)
            ext_targets = [t for t in [pools["pdl"], pools["p4hl"], *pools["eql"], *pools["ssl_pools"]] if t < tp1_rr]
            tp1 = tp1_rr
            tp2 = round(max(ext_targets), 2) if ext_targets else round(entry_min - (max(4.0, rr_target + 1.5) * sl_dist), 2)
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
            champions_rule_match=champions_match or "PrimeFX Pure Price-Action + Liquidity Playbook"
        )
