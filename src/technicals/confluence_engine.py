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
from src.technicals.pattern_memory import PatternMemoryEngine
from src.simulator.hold_time_estimator import HoldTimeEstimator

logger = logging.getLogger(__name__)


class TradingTip(BaseModel):
    symbol: str
    direction: str  # BULLISH, BEARISH, NEUTRAL_NO_TRADE
    entry_mode: str = "LIMIT_PULLBACK"  # MARKET_CONFIRMATION | LIMIT_PULLBACK | NONE
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
    feature_vector: list[float] = Field(default_factory=list)
    analog_win_rate: Optional[float] = None
    analog_avg_r: Optional[float] = None


class ConfluenceEngine:
    def __init__(
        self,
        weights_path: str = "config/strategy_weights.json",
        filters_path: str = "config/active_filters.json",
        pattern_memory: Optional[PatternMemoryEngine] = None,
    ):
        self.weights_path = weights_path
        self.filters_path = filters_path
        self.smc = SmcIctEngine()
        self.sd = SupplyDemandEngine()
        self.pa = PriceActionEngine()
        self.liq = LiquidityEngine()
        self.poi_engine = PoiPriceActionEngine()
        self.hold_estimator = HoldTimeEstimator()
        if pattern_memory is not None:
            self.pattern_memory = pattern_memory
        else:
            default_db = None
            if filters_path == "config/active_filters.json" and os.path.exists("data/trading_state.db"):
                from src.database.db_manager import DatabaseManager
                default_db = DatabaseManager("data/trading_state.db")
            self.pattern_memory = PatternMemoryEngine(db=default_db)
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
            entry_mode="NONE",
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

    @staticmethod
    def _pick_m15_pullback_zone(
        m15_smc: Optional[Dict[str, Any]],
        direction: str,
        current_price: float,
        atr_14: float,
    ) -> Optional[Dict[str, float]]:
        """Finds the closest unmitigated M15 FVG or Order Block on the pullback side."""
        if not m15_smc or direction not in ("BULLISH", "BEARISH"):
            return None
        fvg_type = f"{direction}_FVG"
        ob_type = f"{direction}_OB"
        candidates: List[Dict[str, float]] = []

        for fvg in m15_smc.get("unmitigated_fvgs", []):
            if fvg.get("type") == fvg_type and not fvg.get("is_mitigated", False):
                z_bot = float(fvg["gap_low"])
                z_top = float(fvg["gap_high"])
                mid = float(fvg.get("consequent_encroachment", (z_bot + z_top) / 2.0))
                if direction == "BULLISH" and -0.15 * atr_14 <= (current_price - mid) <= 1.8 * atr_14:
                    candidates.append({"zone_bottom": z_bot, "zone_top": z_top, "midpoint": mid, "dist": abs(current_price - mid)})
                elif direction == "BEARISH" and -0.15 * atr_14 <= (mid - current_price) <= 1.8 * atr_14:
                    candidates.append({"zone_bottom": z_bot, "zone_top": z_top, "midpoint": mid, "dist": abs(current_price - mid)})

        for ob in m15_smc.get("unmitigated_obs", []):
            if ob.get("type") == ob_type and not ob.get("is_mitigated", False):
                z_bot = float(ob["zone_bottom"])
                z_top = float(ob["zone_top"])
                mid = (z_bot + z_top) / 2.0
                if direction == "BULLISH" and -0.15 * atr_14 <= (current_price - mid) <= 1.8 * atr_14:
                    candidates.append({"zone_bottom": z_bot, "zone_top": z_top, "midpoint": mid, "dist": abs(current_price - mid)})
                elif direction == "BEARISH" and -0.15 * atr_14 <= (mid - current_price) <= 1.8 * atr_14:
                    candidates.append({"zone_bottom": z_bot, "zone_top": z_top, "midpoint": mid, "dist": abs(current_price - mid)})

        if not candidates:
            return None
        return min(candidates, key=lambda c: c["dist"])

    def generate_tip(
        self,
        symbol: str,
        df: pd.DataFrame,
        m15_df: Optional[pd.DataFrame] = None,
        h4_df: Optional[pd.DataFrame] = None,
        d1_df: Optional[pd.DataFrame] = None,
        macro_bias: Optional[str] = None,
        macro_confidence: Optional[float] = None,
        h4_mss_confirmed: bool = False,
        hours_since_last_loss: Optional[float] = None,
        recent_expired_or_stopped: Optional[List[Dict[str, Any]]] = None,
    ) -> TradingTip:
        """
        Pure Price-Action & PrimeFX Liquidity/POI Setup Generator.
        Enforces Hurst/ADX/ER regime gating, Inducement clearing, Liquidity Sweep confirmation,
        Dual-Mode Entry (MARKET_CONFIRMATION vs 50%-eq LIMIT_PULLBACK), and 12h Zone Deduplication.
        """
        self.reload_filters()
        current_price = float(df["close"].iloc[-1])

        pa_res = self.pa.analyze(df)
        regime = pa_res["regime"]
        regime_metrics = pa_res.get("regime_metrics", {})
        quant_conf = float(regime_metrics.get("quant_confidence", 0.55))
        atr_14 = pa_res["atr_14"] or 1.0
        m15_atr = atr_14 * 0.5
        if m15_df is not None and len(m15_df) >= 10:
            m15_atr = float(self.pa.calculate_atr(m15_df).iloc[-1]) or m15_atr

        # Rule 0: Unpredictable Move Volatility Circuit Breaker (VOLATILITY_SHOCK_PAUSE for 2 H1 bars)
        shock = self.pattern_memory.check_volatility_circuit_breaker(symbol, df)
        if shock["paused"]:
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [str(shock["reason"])]
            )

        # Rule 1: Anti-Revenge Cooldown check (block same symbol for 2h after SL hit)
        cooldown_h = float(self.active_filters.get("revenge_cooldown_hours", 2.0))
        if hours_since_last_loss is not None and hours_since_last_loss < cooldown_h:
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [f"Anti-Revenge Shield: Blocked re-entry on {symbol} ({hours_since_last_loss:.1f}h < {cooldown_h:.1f}h post-SL cooldown)"]
            )

        # Rule 2: Blocked regimes per symbol + Mandatory Hurst/ADX RANDOM_WALK_CHOP block
        blocked_regimes = self.active_filters.get("blocked_regimes_by_symbol", {}).get(symbol, [])
        if regime in blocked_regimes:
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [f"Council Filter: Regime '{regime}' blocked for {symbol} by active_filters.json"]
            )
        if regime == "RANDOM_WALK_CHOP":
            return self._neutral_tip(
                symbol, regime, current_price, atr_14,
                [
                    f"Hurst/ADX Chop Gate: Blocked entry in RANDOM_WALK_CHOP "
                    f"(H={regime_metrics.get('hurst', 0.5):.3f}, ADX={regime_metrics.get('adx_14', 0.0):.1f}, ER={regime_metrics.get('efficiency_ratio', 0.0):.2f})"
                ]
            )

        # Run Liquidity & Multi-TF POI Engines + M15 Market Structure Shift (body close)
        liq_res = self.liq.analyze(df, df_h4=h4_df, df_d1=d1_df, df_m15=m15_df)
        poi_res = self.poi_engine.analyze(df, df_h4=h4_df, df_m15=m15_df)
        sd_res = self.sd.analyze(df)
        smc_res = self.smc.analyze(df)
        m15_smc = self.smc.analyze(m15_df) if (m15_df is not None and len(m15_df) >= 20) else None

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

        # 1b. M15 Execution Market Structure Shift (MSS on candle body close)
        if m15_smc is not None:
            m15_mss = m15_smc["structure"].get("mss")
            if m15_mss and m15_mss.get("type") == "BULLISH_MSS":
                bullish_score += 0.35 * w_smc
                reasons.append(f"M15 MSS: Bullish body close shift above {m15_mss['level']:.2f}")
            elif m15_mss and m15_mss.get("type") == "BEARISH_MSS":
                bearish_score += 0.35 * w_smc
                reasons.append(f"M15 MSS: Bearish body close shift below {m15_mss['level']:.2f}")

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

        # Rule 3b: Rotational range blocks pure trend continuation (liquidity-sweep fade only)
        if candidate_dir != "NEUTRAL_NO_TRADE" and regime == "ROTATIONAL_RANGE":
            if candidate_dir == "BULLISH" and not bull_sweep_confirmed:
                reasons.append("Regime Filter: ROTATIONAL_RANGE blocks trend continuation; sweep fade required")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)
            if candidate_dir == "BEARISH" and not bear_sweep_confirmed:
                reasons.append("Regime Filter: ROTATIONAL_RANGE blocks trend continuation; sweep fade required")
                return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

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

        # Entry zone calibration: max 0.30 * ATR_14(M15) around 50% equilibrium OR tight band for MARKET_CONFIRMATION
        zone_m15_mult = float(self._get_symbol_filter_val(symbol, "entry_zone_m15_atr_width", 0.30))
        atr_band_width = max(round(zone_m15_mult * m15_atr, 2), 0.10)
        pools = liq_res["pools"]
        eff_macro_conf = float(macro_confidence) if macro_confidence is not None else quant_conf
        m15_recent_mss = m15_smc["structure"].get("recent_mss") if m15_smc else None

        entry_mode = "LIMIT_PULLBACK"
        if candidate_dir in ("BULLISH", "BEARISH"):
            direction = candidate_dir
            raw_score = bullish_score if direction == "BULLISH" else bearish_score
            confidence = round(
                min(0.94, max(0.42, (0.58 * min(0.96, raw_score / 1.55)) + (0.42 * eff_macro_conf))),
                3,
            )
            dir_sweep = bull_sweep if direction == "BULLISH" else bear_sweep
            dir_sweep_ok = bull_sweep_confirmed if direction == "BULLISH" else bear_sweep_confirmed
            dir_poi = best_bull_poi if direction == "BULLISH" else best_bear_poi
            dir_sd = nearest_d if direction == "BULLISH" else nearest_s

            primary_strat = "SMC_ICT" if (dir_sweep_ok or smc_trend == direction) else "SUPPLY_DEMAND"
            secondary_strat = "PRICE_ACTION"

            m15_pb_zone = self._pick_m15_pullback_zone(m15_smc, direction, current_price, atr_14)
            anchor_zone = m15_pb_zone or dir_poi or (
                {"zone_bottom": dir_sd["base_bottom"], "zone_top": dir_sd["base_top"]} if dir_sd else None
            )
            dist_to_zone = (
                max(0.0, float(anchor_zone["zone_bottom"]) - current_price, current_price - float(anchor_zone["zone_top"]))
                if anchor_zone else 0.0
            )
            has_m15_disp = bool(
                m15_recent_mss
                and m15_recent_mss.get("type") == f"{direction}_MSS"
                and (m15_pb_zone is not None or bool(m15_smc and (m15_smc.get("unmitigated_fvgs") or m15_smc.get("unmitigated_obs"))) or dir_poi is not None)
            )
            macro_aligned = (macro_bias is None or macro_bias.upper() == direction)

            if dir_sweep is not None and has_m15_disp and dist_to_zone <= (0.75 * m15_atr) and macro_aligned and eff_macro_conf >= 0.72:
                entry_mode = "MARKET_CONFIRMATION"
                half_band = max(round(0.15 * m15_atr, 2), 0.05)
                entry_min = round(current_price - half_band, 2)
                entry_max = round(current_price + half_band, 2)
                entry_ref = round(current_price, 2)
                reasons.append(
                    f"Entry Mode: MARKET_CONFIRMATION (sweep + M15 MSS {m15_recent_mss['bars_ago']}b ago, "
                    f"dist={dist_to_zone:.2f} <= 0.75*M15_ATR, H4 conf={eff_macro_conf:.2f})"
                )
            else:
                entry_mode = "LIMIT_PULLBACK"
                if anchor_zone:
                    eq_mid = (float(anchor_zone["zone_bottom"]) + float(anchor_zone["zone_top"])) / 2.0
                    entry_min = round(eq_mid - (0.5 * atr_band_width), 2)
                    entry_max = round(eq_mid + (0.5 * atr_band_width), 2)
                else:
                    if direction == "BULLISH":
                        entry_max = round(current_price, 2)
                        entry_min = round(current_price - atr_band_width, 2)
                    else:
                        entry_min = round(current_price, 2)
                        entry_max = round(current_price + atr_band_width, 2)
                entry_ref = entry_max if direction == "BULLISH" else entry_min

            if direction == "BULLISH":
                raw_sl = (entry_min - (sl_buf_mult * atr_14)) if anchor_zone else (entry_max - (1.5 * atr_14))
                sweep_ext = bull_sweep["wick_extreme"] if bull_sweep else None
                if is_jump or sweep_ext is not None:
                    min_jump_dist = (jump_sl_mult if is_jump else 1.0) * atr_14
                    floor_candidates = [raw_sl, entry_ref - min_jump_dist]
                    if sweep_ext is not None:
                        floor_candidates.append(sweep_ext - (0.35 * atr_14))
                    sl = round(min(floor_candidates), 2)
                else:
                    sl = round(raw_sl, 2)

                sl_dist = abs(entry_ref - sl)
                rr_target = max(2.5, min_rr)
                tp1_rr = round(entry_ref + (rr_target * sl_dist), 2)
                ext_targets = [t for t in [pools["pdh"], pools["p4hh"], *pools["eqh"], *pools["bsl_pools"]] if t > tp1_rr]
                tp1 = tp1_rr
                tp2 = round(min(ext_targets), 2) if ext_targets else round(entry_ref + (max(4.0, rr_target + 1.5) * sl_dist), 2)
                be_trigger = round(entry_ref + (1.0 * sl_dist), 2)
            else:
                raw_sl = (entry_max + (sl_buf_mult * atr_14)) if anchor_zone else (entry_min + (1.5 * atr_14))
                sweep_ext = bear_sweep["wick_extreme"] if bear_sweep else None
                if is_jump or sweep_ext is not None:
                    min_jump_dist = (jump_sl_mult if is_jump else 1.0) * atr_14
                    ceil_candidates = [raw_sl, entry_ref + min_jump_dist]
                    if sweep_ext is not None:
                        ceil_candidates.append(sweep_ext + (0.35 * atr_14))
                    sl = round(max(ceil_candidates), 2)
                else:
                    sl = round(raw_sl, 2)

                sl_dist = abs(sl - entry_ref)
                rr_target = max(2.5, min_rr)
                tp1_rr = round(entry_ref - (rr_target * sl_dist), 2)
                ext_targets = [t for t in [pools["pdl"], pools["p4hl"], *pools["eql"], *pools["ssl_pools"]] if t < tp1_rr]
                tp1 = tp1_rr
                tp2 = round(max(ext_targets), 2) if ext_targets else round(entry_ref - (max(4.0, rr_target + 1.5) * sl_dist), 2)
                be_trigger = round(entry_ref - (1.0 * sl_dist), 2)
        else:
            return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        # 12-Hour Expired/Stopped Zone Deduplication Guard
        if recent_expired_or_stopped:
            dedup_mult = float(self.active_filters.get("dedup_zone_atr_mult", 0.50))
            prop_mid = (entry_min + entry_max) / 2.0
            for prev in recent_expired_or_stopped:
                prev_dir = prev.get("direction")
                if prev_dir and prev_dir != direction:
                    continue
                prev_mid = prev.get("midpoint")
                if prev_mid is None:
                    prev_mid = (float(prev.get("entry_min", 0.0)) + float(prev.get("entry_max", 0.0))) / 2.0
                if abs(prop_mid - float(prev_mid)) <= (dedup_mult * atr_14):
                    reasons.append(
                        f"Deduplication Guard: Setup zone [{entry_min:.2f}-{entry_max:.2f}] within "
                        f"{dedup_mult:.2f}*H1_ATR ({dedup_mult*atr_14:.2f}) of recent {prev.get('status', 'PRIOR')} zone {float(prev_mid):.2f}"
                    )
                    return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        sl_points = abs(entry_ref - sl)
        tp1_points = abs(tp1 - entry_ref)
        rr = round(tp1_points / sl_points, 2) if sl_points > 0 else 0.0

        if rr < min_rr:
            reasons.append(f"Filter: Planned R:R {rr:.2f} below active_filters min_rr_ratio {min_rr:.2f}")
            return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)

        # Rule 6: k-NN Historical Analog Pattern Memory Gate
        chosen_sweep = bull_sweep if direction == "BULLISH" else bear_sweep
        chosen_poi = best_bull_poi if direction == "BULLISH" else best_bear_poi
        f_vec = self.pattern_memory.extract_feature_vector(
            df_h1=df,
            direction=direction,
            htf_trend=macro_bias or smc_trend,
            sweep_info=chosen_sweep,
            poi_info=chosen_poi,
            planned_rr=rr,
        )
        sim_eval = self.pattern_memory.evaluate_similarity(f_vec, symbol=symbol)
        if not sim_eval["passed"]:
            reasons.append(str(sim_eval["reason"]))
            return self._neutral_tip(symbol, regime, current_price, atr_14, reasons)
        if sim_eval["k_neighbors"] > 0:
            reasons.append(
                f"k-NN Analogs (k={sim_eval['k_neighbors']}): {sim_eval['analog_win_rate']*100:.1f}% win rate, "
                f"{sim_eval['analog_avg_r']:+.2f}R avg"
            )

        hold_est = self.hold_estimator.estimate_hold_time(
            entry_price=entry_ref,
            tp_price=tp1,
            atr_14=atr_14,
            timeframe_minutes=60
        )

        return TradingTip(
            symbol=symbol,
            direction=direction,
            entry_mode=entry_mode,
            confidence_score=confidence,
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
            champions_rule_match=champions_match or "PrimeFX Pure Price-Action + Liquidity Playbook",
            feature_vector=f_vec,
            analog_win_rate=sim_eval["analog_win_rate"],
            analog_avg_r=sim_eval["analog_avg_r"],
        )
