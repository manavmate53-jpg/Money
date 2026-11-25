"""
PrimeFX Point of Interest (POI) & Pure Price-Action Engine.
Zero orderflow, DOM, or Volume Profile (VAH/VAL) code — strictly pure price action:
- Order Blocks (OB) leaving FVG displacement
- Fair Value Gaps (FVG) & Inversion FVGs (IFVG)
- Breaker Blocks (failed swept OBs retested on flip)
- Equilibrium 50% & OTE (0.618 - 0.786) alignment
- Inducement (IDM) Trap gating via LiquidityEngine
"""

from typing import Dict, Any, List, Optional
import pandas as pd

from src.technicals.smc_ict import SmcIctEngine
from src.technicals.liquidity_engine import LiquidityEngine
from src.technicals.price_action import PriceActionEngine


class PoiPriceActionEngine:
    def __init__(self, swing_length: int = 3):
        self.smc = SmcIctEngine(swing_length=swing_length)
        self.liq = LiquidityEngine(swing_length=swing_length)
        self.pa = PriceActionEngine()

    def detect_inversion_fvgs(self, df: pd.DataFrame, max_lookback: int = 60) -> List[Dict[str, Any]]:
        """
        Detects Inversion FVGs (IFVG): Prior Fair Value Gaps that price closed completely
        through, converting former support imbalance into Bearish IFVG resistance (and vice versa).
        """
        ifvg_list: List[Dict[str, Any]] = []
        n = len(df)
        fvgs = self.smc.detect_fair_value_gaps(df, max_lookback=max_lookback)

        for fvg in fvgs:
            idx = fvg["candle_idx"]
            gap_low = fvg["gap_low"]
            gap_high = fvg["gap_high"]
            ce = fvg["consequent_encroachment"]

            if idx + 2 >= n:
                continue

            if fvg["type"] == "BULLISH_FVG":
                # Inverted into Bearish IFVG when a subsequent candle closes strictly BELOW gap_low
                inv_idx = None
                for k in range(idx + 2, n):
                    if float(df["close"].iloc[k]) < gap_low:
                        inv_idx = k
                        break
                if inv_idx is not None:
                    # Check if invalidated again upward after inversion
                    re_broken = any(float(df["close"].iloc[m]) > gap_high for m in range(inv_idx + 1, n))
                    ifvg_list.append({
                        "type": "BEARISH_IFVG",
                        "direction": "BEARISH",
                        "zone_bottom": gap_low,
                        "zone_top": gap_high,
                        "consequent_encroachment": ce,
                        "candle_idx": inv_idx,
                        "is_mitigated": re_broken,
                    })
            else:
                # Bearish FVG closed strictly ABOVE gap_high becomes Bullish IFVG
                inv_idx = None
                for k in range(idx + 2, n):
                    if float(df["close"].iloc[k]) > gap_high:
                        inv_idx = k
                        break
                if inv_idx is not None:
                    re_broken = any(float(df["close"].iloc[m]) < gap_low for m in range(inv_idx + 1, n))
                    ifvg_list.append({
                        "type": "BULLISH_IFVG",
                        "direction": "BULLISH",
                        "zone_bottom": gap_low,
                        "zone_top": gap_high,
                        "consequent_encroachment": ce,
                        "candle_idx": inv_idx,
                        "is_mitigated": re_broken,
                    })

        return [item for item in ifvg_list if not item["is_mitigated"]]

    def detect_breaker_blocks(self, df: pd.DataFrame, max_lookback: int = 60) -> List[Dict[str, Any]]:
        """
        Detects Breaker Blocks: Swept Order Blocks subsequently closed through with
        displacement, acting as high-probability reversal POIs upon retest.
        """
        breakers: List[Dict[str, Any]] = []
        n = len(df)
        obs = self.smc.detect_order_blocks(df, max_lookback=max_lookback)

        for ob in obs:
            idx = ob["candle_idx"]
            z_top = ob["zone_top"]
            z_bot = ob["zone_bottom"]
            if idx + 3 >= n:
                continue

            if ob["type"] == "BULLISH_OB":
                # Closed below bottom -> flips to BEARISH_BREAKER
                brk_idx = None
                for k in range(idx + 3, n):
                    if float(df["close"].iloc[k]) < z_bot:
                        brk_idx = k
                        break
                if brk_idx is not None:
                    invalidated = any(float(df["close"].iloc[m]) > z_top for m in range(brk_idx + 1, n))
                    if not invalidated:
                        breakers.append({
                            "type": "BEARISH_BREAKER",
                            "direction": "BEARISH",
                            "zone_bottom": z_bot,
                            "zone_top": z_top,
                            "candle_idx": brk_idx,
                            "is_mitigated": False,
                        })
            else:
                # Closed above top -> flips to BULLISH_BREAKER
                brk_idx = None
                for k in range(idx + 3, n):
                    if float(df["close"].iloc[k]) > z_top:
                        brk_idx = k
                        break
                if brk_idx is not None:
                    invalidated = any(float(df["close"].iloc[m]) < z_bot for m in range(brk_idx + 1, n))
                    if not invalidated:
                        breakers.append({
                            "type": "BULLISH_BREAKER",
                            "direction": "BULLISH",
                            "zone_bottom": z_bot,
                            "zone_top": z_top,
                            "candle_idx": brk_idx,
                            "is_mitigated": False,
                        })
        return breakers

    def _collect_timeframe_pois(self, df: pd.DataFrame, tf_label: str, weight_mult: float) -> List[Dict[str, Any]]:
        """Gathers OB, FVG, IFVG, and Breaker POIs on a given timeframe with Inducement validation."""
        if df is None or len(df) < 20:
            return []

        current_price = float(df["close"].iloc[-1])
        atr_14 = float(self.pa.calculate_atr(df).iloc[-1]) or 1.0
        smc_res = self.smc.analyze(df)
        ifvgs = self.detect_inversion_fvgs(df)
        breakers = self.detect_breaker_blocks(df)

        raw_pois: List[Dict[str, Any]] = []

        for ob in smc_res["unmitigated_obs"]:
            p_dir = "BULLISH" if ob["type"] == "BULLISH_OB" else "BEARISH"
            raw_pois.append({
                "timeframe": tf_label,
                "poi_kind": ob["type"],
                "direction": p_dir,
                "zone_bottom": float(ob["zone_bottom"]),
                "zone_top": float(ob["zone_top"]),
                "candle_idx": int(ob["candle_idx"]),
                "base_priority": 1.0 * weight_mult,
            })

        for fvg in smc_res["unmitigated_fvgs"]:
            p_dir = "BULLISH" if fvg["type"] == "BULLISH_FVG" else "BEARISH"
            raw_pois.append({
                "timeframe": tf_label,
                "poi_kind": fvg["type"],
                "direction": p_dir,
                "zone_bottom": float(fvg["gap_low"]),
                "zone_top": float(fvg["gap_high"]),
                "candle_idx": int(fvg["candle_idx"]),
                "base_priority": 0.9 * weight_mult,
            })

        for ifvg in ifvgs:
            raw_pois.append({
                "timeframe": tf_label,
                "poi_kind": ifvg["type"],
                "direction": ifvg["direction"],
                "zone_bottom": float(ifvg["zone_bottom"]),
                "zone_top": float(ifvg["zone_top"]),
                "candle_idx": int(ifvg["candle_idx"]),
                "base_priority": 0.95 * weight_mult,
            })

        for brk in breakers:
            raw_pois.append({
                "timeframe": tf_label,
                "poi_kind": brk["type"],
                "direction": brk["direction"],
                "zone_bottom": float(brk["zone_bottom"]),
                "zone_top": float(brk["zone_top"]),
                "candle_idx": int(brk["candle_idx"]),
                "base_priority": 0.95 * weight_mult,
            })

        enriched: List[Dict[str, Any]] = []
        for p in raw_pois:
            # Directional sanity: Bullish POI sits below/at price, Bearish POI above/at price
            if p["direction"] == "BULLISH" and p["zone_bottom"] > current_price + atr_14:
                continue
            if p["direction"] == "BEARISH" and p["zone_top"] < current_price - atr_14:
                continue

            idm = self.liq.check_inducement_for_poi(
                df,
                poi_type=p["direction"],
                zone_bottom=p["zone_bottom"],
                zone_top=p["zone_top"],
                poi_candle_idx=p["candle_idx"],
            )
            dist = abs(current_price - p["zone_top"]) if p["direction"] == "BULLISH" else abs(p["zone_bottom"] - current_price)
            prox_bonus = max(0.0, 1.0 - (dist / max(atr_14 * 4.0, 1e-6)))
            p["inducement_cleared"] = idm["inducement_cleared"]
            p["inducement_status"] = idm["status"]
            p["idm_level"] = idm["idm_level"]
            p["distance_to_price"] = round(dist, 4)
            p["distance_atr"] = round(dist / atr_14, 2)
            p["score"] = round(p["base_priority"] + (0.3 * prox_bonus), 3) if idm["inducement_cleared"] else 0.0
            enriched.append(p)

        return enriched

    def analyze(
        self,
        df_h1: pd.DataFrame,
        df_h4: Optional[pd.DataFrame] = None,
        df_m15: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Synthesizes H4 -> H1 -> M15 POIs (OB, FVG, IFVG, Breaker) with Inducement gating
        and Equilibrium/OTE structure.
        """
        pois: List[Dict[str, Any]] = []
        if df_h4 is not None and len(df_h4) >= 20:
            pois.extend(self._collect_timeframe_pois(df_h4, "H4", weight_mult=1.2))
        pois.extend(self._collect_timeframe_pois(df_h1, "H1", weight_mult=1.0))
        if df_m15 is not None and len(df_m15) >= 20:
            pois.extend(self._collect_timeframe_pois(df_m15, "M15", weight_mult=0.9))

        valid_pois = sorted([p for p in pois if p["inducement_cleared"]], key=lambda x: x["score"], reverse=True)
        trap_pois = [p for p in pois if not p["inducement_cleared"]]

        smc_h1 = self.smc.analyze(df_h1)
        pa_h1 = self.pa.analyze(df_h1)

        return {
            "valid_pois": valid_pois,
            "trap_pois": trap_pois,
            "best_bullish_poi": next((p for p in valid_pois if p["direction"] == "BULLISH"), None),
            "best_bearish_poi": next((p for p in valid_pois if p["direction"] == "BEARISH"), None),
            "blocked_bullish_trap": next((p for p in trap_pois if p["direction"] == "BULLISH"), None),
            "blocked_bearish_trap": next((p for p in trap_pois if p["direction"] == "BEARISH"), None),
            "ote_levels": smc_h1["ote_levels"],
            "structure": smc_h1["structure"],
            "price_action": pa_h1,
        }
