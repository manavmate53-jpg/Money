"""
PrimeFX Pure Price-Action Liquidity Pool, Inducement & Sweep Engine.
Zero orderflow, zero DOM, and zero volume-profile assumptions (tailored 100%
for Deriv Synthetic Volatility & Jump indices).
Maps BSL/SSL, Equal Highs/Lows (EQH/EQL), PDH/PDL, Previous 4H High/Low (P4HH/P4HL),
Inducement (IDM) traps in front of POIs, and classifies true Sweeps vs Breakouts.
"""

from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from src.technicals.smc_ict import SmcIctEngine


class LiquidityEngine:
    def __init__(self, swing_length: int = 3, eq_tolerance_atr_mult: float = 0.10):
        self.smc = SmcIctEngine(swing_length=swing_length)
        self.eq_tolerance_atr_mult = eq_tolerance_atr_mult

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
        if df is None or len(df) < 2:
            return 1.0
        high = df["high"]
        low = df["low"]
        prev_c = df["close"].shift(1)
        tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
        val = float(tr.rolling(window=period, min_periods=1).mean().iloc[-1])
        return val if val > 0 else 1.0

    def detect_equal_levels(self, df: pd.DataFrame, atr_14: float) -> Dict[str, List[float]]:
        """
        Detects Equal Highs (EQH) and Equal Lows (EQL) within 0.10 * ATR_14 tolerance.
        These act as PrimeFX's highest-probability liquidity magnets.
        """
        swings = self.smc.find_swing_points(df)
        sh_prices = swings.loc[swings["swing_high"], "high"].tolist()
        sl_prices = swings.loc[swings["swing_low"], "low"].tolist()
        tol = max(1e-6, atr_14 * self.eq_tolerance_atr_mult)

        eq_highs: List[float] = []
        for i in range(len(sh_prices)):
            for j in range(i + 1, len(sh_prices)):
                if abs(sh_prices[i] - sh_prices[j]) <= tol:
                    avg_lvl = round((sh_prices[i] + sh_prices[j]) / 2.0, 4)
                    if not any(abs(avg_lvl - existing) <= tol for existing in eq_highs):
                        eq_highs.append(avg_lvl)

        eq_lows: List[float] = []
        for i in range(len(sl_prices)):
            for j in range(i + 1, len(sl_prices)):
                if abs(sl_prices[i] - sl_prices[j]) <= tol:
                    avg_lvl = round((sl_prices[i] + sl_prices[j]) / 2.0, 4)
                    if not any(abs(avg_lvl - existing) <= tol for existing in eq_lows):
                        eq_lows.append(avg_lvl)

        return {"eqh": eq_highs, "eql": eq_lows}

    def map_liquidity_pools(
        self,
        df_h1: pd.DataFrame,
        df_h4: Optional[pd.DataFrame] = None,
        df_d1: Optional[pd.DataFrame] = None,
        df_m15: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """
        Maps all active resting Buy-Side & Sell-Side liquidity pools across timeframes:
        - Major Swing BSL/SSL
        - Equal Highs (EQH) & Equal Lows (EQL) within 0.10 * ATR_14
        - Previous Day High/Low (PDH/PDL)
        - Previous 4H Candle High/Low (P4HH/P4HL) for Candle Range Theory (CRT)
        """
        atr_h1 = self._compute_atr(df_h1)
        eq_levels = self.detect_equal_levels(df_h1, atr_h1)

        swings_h1 = self.smc.find_swing_points(df_h1)
        bsl_pools = [float(p) for p in swings_h1.loc[swings_h1["swing_high"], "high"].tolist()[-5:]]
        ssl_pools = [float(p) for p in swings_h1.loc[swings_h1["swing_low"], "low"].tolist()[-5:]]

        # Previous Day High/Low (PDH / PDL)
        if df_d1 is not None and len(df_d1) >= 2:
            pdh = float(df_d1["high"].iloc[-2])
            pdl = float(df_d1["low"].iloc[-2])
        elif len(df_h1) >= 25:
            prior_day_slice = df_h1.iloc[-48:-24] if len(df_h1) >= 48 else df_h1.iloc[:-1]
            pdh = float(prior_day_slice["high"].max())
            pdl = float(prior_day_slice["low"].min())
        else:
            pdh = float(df_h1["high"].iloc[:-1].max()) if len(df_h1) > 1 else float(df_h1["high"].iloc[-1])
            pdl = float(df_h1["low"].iloc[:-1].min()) if len(df_h1) > 1 else float(df_h1["low"].iloc[-1])

        # Previous 4H Candle High/Low (P4HH / P4HL)
        if df_h4 is not None and len(df_h4) >= 2:
            p4hh = float(df_h4["high"].iloc[-2])
            p4hl = float(df_h4["low"].iloc[-2])
        elif len(df_h1) >= 8:
            p4h_slice = df_h1.iloc[-8:-4]
            p4hh = float(p4h_slice["high"].max())
            p4hl = float(p4h_slice["low"].min())
        else:
            p4hh, p4hl = pdh, pdl

        m15_eq = self.detect_equal_levels(df_m15, self._compute_atr(df_m15)) if (df_m15 is not None and len(df_m15) >= 20) else {"eqh": [], "eql": []}

        return {
            "atr_h1": round(atr_h1, 4),
            "bsl_pools": bsl_pools,
            "ssl_pools": ssl_pools,
            "eqh": eq_levels["eqh"],
            "eql": eq_levels["eql"],
            "m15_eqh": m15_eq["eqh"],
            "m15_eql": m15_eq["eql"],
            "pdh": round(pdh, 4),
            "pdl": round(pdl, 4),
            "p4hh": round(p4hh, 4),
            "p4hl": round(p4hl, 4),
        }

    def classify_sweep_vs_breakout(
        self,
        df: pd.DataFrame,
        level: float,
        side: str,  # "HIGH" (BSL/EQH/PDH) or "LOW" (SSL/EQL/PDL)
        lookback_bars: int = 4,
    ) -> Dict[str, Any]:
        """
        Deterministically separates a true Liquidity Sweep / Raid from a Breakout Continuation:
        - Sweep (HIGH): Candle wicks strictly above `level` AND closes back below `level` (or prints
          immediate next-bar bearish close below `level`).
        - Breakout (HIGH): Body closes strictly above `level` and holds outside range.
        """
        if len(df) < 3:
            return {"event": "NONE", "level": level, "confirmation_candles": 0}

        recent = df.tail(lookback_bars).reset_index(drop=True)
        for idx in range(len(recent)):
            bar = recent.iloc[idx]
            following = recent.iloc[idx:]

            if side == "HIGH":
                if bar["high"] > level and bar["close"] < level:
                    # Wick swept above high and closed inside
                    if all(following["close"] < level):
                        return {
                            "event": "BEARISH_SWEEP",
                            "level": round(level, 4),
                            "confirmation_candles": len(following),
                            "wick_extreme": float(bar["high"])
                        }
                elif bar["high"] > level and bar["close"] > level:
                    # Check if immediate next candle slammed back inside (2-bar turtle soup)
                    if idx + 1 < len(recent) and recent.iloc[idx + 1]["close"] < level:
                        sub_f = recent.iloc[idx + 1:]
                        if all(sub_f["close"] < level):
                            return {
                                "event": "BEARISH_SWEEP",
                                "level": round(level, 4),
                                "confirmation_candles": len(sub_f),
                                "wick_extreme": float(bar["high"])
                            }
                    elif idx == len(recent) - 1 or all(following["close"] > level):
                        return {
                            "event": "BULLISH_BREAKOUT",
                            "level": round(level, 4),
                            "confirmation_candles": len(following),
                            "wick_extreme": float(bar["high"])
                        }
            else:
                if bar["low"] < level and bar["close"] > level:
                    if all(following["close"] > level):
                        return {
                            "event": "BULLISH_SWEEP",
                            "level": round(level, 4),
                            "confirmation_candles": len(following),
                            "wick_extreme": float(bar["low"])
                        }
                elif bar["low"] < level and bar["close"] < level:
                    if idx + 1 < len(recent) and recent.iloc[idx + 1]["close"] > level:
                        sub_f = recent.iloc[idx + 1:]
                        if all(sub_f["close"] > level):
                            return {
                                "event": "BULLISH_SWEEP",
                                "level": round(level, 4),
                                "confirmation_candles": len(sub_f),
                                "wick_extreme": float(bar["low"])
                            }
                    elif idx == len(recent) - 1 or all(following["close"] < level):
                        return {
                            "event": "BEARISH_BREAKOUT",
                            "level": round(level, 4),
                            "confirmation_candles": len(following),
                            "wick_extreme": float(bar["low"])
                        }

        return {"event": "NONE", "level": level, "confirmation_candles": 0}

    def check_inducement_for_poi(
        self,
        df: pd.DataFrame,
        poi_type: str,  # "BULLISH" or "BEARISH"
        zone_bottom: float,
        zone_top: float,
        poi_candle_idx: int,
    ) -> Dict[str, Any]:
        """
        PrimeFX Inducement (IDM) Rule:
        Identifies the first minor internal pullback pivot resting directly in front of the POI
        after `poi_candle_idx`. If this IDM exists and has NOT been swept yet, marks the POI as
        `TRAP_PRE_INDUCEMENT` (`inducement_cleared=False`) to block premature retail entries.
        """
        n = len(df)
        if poi_candle_idx >= n - 3:
            return {"inducement_cleared": True, "idm_level": None, "status": "NO_IDM_NEEDED"}

        sub = df.iloc[poi_candle_idx + 1:].reset_index(drop=True)
        # Find internal 1-bar pivots in front of POI before latest bar
        idm_candidates: List[tuple[int, float]] = []
        for k in range(1, len(sub) - 1):
            if poi_type == "BULLISH":
                lvl = float(sub.loc[k, "low"])
                if lvl <= sub.loc[k - 1, "low"] and lvl <= sub.loc[k + 1, "low"] and lvl > zone_top:
                    idm_candidates.append((k, lvl))
            else:
                lvl = float(sub.loc[k, "high"])
                if lvl >= sub.loc[k - 1, "high"] and lvl >= sub.loc[k + 1, "high"] and lvl < zone_bottom:
                    idm_candidates.append((k, lvl))

        if not idm_candidates:
            return {"inducement_cleared": True, "idm_level": None, "status": "CLEAR_DIRECT_POI"}

        # Evaluate closest minor internal pivot sitting in front of the POI
        idm_idx, idm_level = min(
            idm_candidates,
            key=lambda x: abs(x[1] - (zone_top if poi_type == "BULLISH" else zone_bottom))
        )

        # Check if price AFTER idm_idx already raided idm_level
        after_idm = sub.iloc[idm_idx + 1:]
        if poi_type == "BULLISH":
            raided = bool((after_idm["low"] < idm_level).any())
        else:
            raided = bool((after_idm["high"] > idm_level).any())

        if not raided:
            return {
                "inducement_cleared": False,
                "idm_level": round(idm_level, 4),
                "status": "TRAP_PRE_INDUCEMENT",
            }
        return {
            "inducement_cleared": True,
            "idm_level": round(idm_level, 4),
            "status": "IDM_SWEPT_CONFIRMED",
        }

    def analyze(
        self,
        df_h1: pd.DataFrame,
        df_h4: Optional[pd.DataFrame] = None,
        df_d1: Optional[pd.DataFrame] = None,
        df_m15: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Runs complete PrimeFX Liquidity pool + sweep vs breakout analysis."""
        pools = self.map_liquidity_pools(df_h1, df_h4=df_h4, df_d1=df_d1, df_m15=df_m15)
        target_df = df_m15 if (df_m15 is not None and len(df_m15) >= 10) else df_h1

        sweeps: List[Dict[str, Any]] = []
        breakouts: List[Dict[str, Any]] = []

        # Deduplicated high & low magnets
        high_magnets = [("PDH", pools["pdh"]), ("P4HH", pools["p4hh"])]
        for lvl in pools["eqh"]:
            high_magnets.append(("EQH", lvl))
        for lvl in pools["bsl_pools"][-3:]:
            high_magnets.append(("BSL", lvl))

        low_magnets = [("PDL", pools["pdl"]), ("P4HL", pools["p4hl"])]
        for lvl in pools["eql"]:
            low_magnets.append(("EQL", lvl))
        for lvl in pools["ssl_pools"][-3:]:
            low_magnets.append(("SSL", lvl))

        for label, lvl in high_magnets:
            ev = self.classify_sweep_vs_breakout(target_df, lvl, side="HIGH")
            if ev["event"] == "BEARISH_SWEEP":
                sweeps.append({**ev, "pool_type": label, "bias": "BEARISH"})
            elif ev["event"] == "BULLISH_BREAKOUT":
                breakouts.append({**ev, "pool_type": label})

        for label, lvl in low_magnets:
            ev = self.classify_sweep_vs_breakout(target_df, lvl, side="LOW")
            if ev["event"] == "BULLISH_SWEEP":
                sweeps.append({**ev, "pool_type": label, "bias": "BULLISH"})
            elif ev["event"] == "BEARISH_BREAKOUT":
                breakouts.append({**ev, "pool_type": label})

        # Fallback standard H1 sweep check if M15 only showed continuation
        if not sweeps:
            smc_sw = self.smc.detect_liquidity_sweeps(df_h1)
            if smc_sw.get("ssl_swept"):
                sweeps.append({
                    "event": "BULLISH_SWEEP",
                    "pool_type": "H1_SSL",
                    "bias": "BULLISH",
                    "level": smc_sw["sweep_level"],
                    "confirmation_candles": smc_sw.get("confirmation_candles", 1),
                    "wick_extreme": smc_sw["sweep_level"],
                })
            elif smc_sw.get("bsl_swept"):
                sweeps.append({
                    "event": "BEARISH_SWEEP",
                    "pool_type": "H1_BSL",
                    "bias": "BEARISH",
                    "level": smc_sw["sweep_level"],
                    "confirmation_candles": smc_sw.get("confirmation_candles", 1),
                    "wick_extreme": smc_sw["sweep_level"],
                })

        return {
            "pools": pools,
            "active_sweeps": sweeps,
            "active_breakouts": breakouts,
            "bullish_sweep": next((s for s in sweeps if s["bias"] == "BULLISH"), None),
            "bearish_sweep": next((s for s in sweeps if s["bias"] == "BEARISH"), None),
        }
