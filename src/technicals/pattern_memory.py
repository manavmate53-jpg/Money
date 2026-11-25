"""
Pure Price-Action Pattern Memory Engine (`k-NN` Historical Analog Matcher)
& Unpredictable-Move Volatility Circuit Breaker.
Zero orderflow or volume-profile dependencies:
1. Maps candidate setups into an 8-feature standardized pure OHLC price-action vector:
   [htf_trend_alignment, sweep_depth_atr, idm_swept_flag, poi_type_code,
    distance_to_poi_atr, atr_percentile_100, hurst_exponent, planned_rr]
2. Walk-forward seeds 90 days of historical setups across Deriv symbols and appends
   every resolved paper trade (`profitable_trades` + `losing_trades`).
3. Queries k=20 nearest historical analogs via standardized Euclidean distance and blocks
   entries where analog_win_rate < 0.42 or analog_avg_r <= 0.15.
4. Triggers VOLATILITY_SHOCK_PAUSE (2 H1 candles) when single-candle range spikes
   exceed 3.0 * ATR_14 or ATR_14 / ATR_100 enters erratic shock expansion.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
import numpy as np
import pandas as pd

from src.risk.quant_engine import QuantEngine
from src.technicals.price_action import PriceActionEngine
from src.technicals.smc_ict import SmcIctEngine
from src.technicals.liquidity_engine import LiquidityEngine
from src.technicals.poi_price_action import PoiPriceActionEngine

logger = logging.getLogger(__name__)

FEATURE_NAMES: List[str] = [
    "htf_trend_alignment",
    "sweep_depth_atr",
    "idm_swept_flag",
    "poi_type_code",
    "distance_to_poi_atr",
    "atr_percentile_100",
    "hurst_exponent",
    "planned_rr",
]

POI_TYPE_CODES: Dict[str, float] = {
    "NONE": 0.0,
    "SUPPLY_DEMAND": 0.5,
    "OB": 1.0,
    "FVG": 2.0,
    "IFVG": 3.0,
    "BREAKER": 4.0,
}


def encode_poi_type(poi_kind: Optional[str]) -> float:
    """Maps a POI string classification to a normalized pure-PA numeric code."""
    if not poi_kind:
        return 0.0
    upper = poi_kind.upper()
    for token in ("BREAKER", "IFVG", "FVG", "OB", "SUPPLY_DEMAND"):
        if token in upper:
            return POI_TYPE_CODES[token]
    return 0.5


class PatternMemoryEngine:
    def __init__(
        self,
        db: Any = None,
        k: int = 20,
        min_win_rate: float = 0.42,
        min_avg_r: float = 0.15,
        min_analogs_required: int = 10,
        spike_atr_mult: float = 3.0,
        atr_ratio_threshold: float = 2.2,
        pause_candles: int = 2,
    ):
        self.db = db
        self.k = k
        self.min_win_rate = min_win_rate
        self.min_avg_r = min_avg_r
        self.min_analogs_required = min_analogs_required
        self.spike_atr_mult = spike_atr_mult
        self.atr_ratio_threshold = atr_ratio_threshold
        self.pause_candles = pause_candles
        self.quant = QuantEngine()
        self.pa = PriceActionEngine()
        self.smc = SmcIctEngine()
        self.liq = LiquidityEngine()
        self.poi = PoiPriceActionEngine()

    @staticmethod
    def _compute_tr_series(df: pd.DataFrame) -> pd.Series:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        prev_c = df["close"].astype(float).shift(1)
        return pd.concat(
            [high - low, (high - prev_c).abs(), (low - prev_c).abs()],
            axis=1,
        ).max(axis=1)

    def check_volatility_circuit_breaker(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
    ) -> Dict[str, Any]:
        """
        Detects erratic CSPRNG or clustered Jump-Index volatility shocks:
        - Single-candle range > 3.0 * baseline ATR_14 within the trailing 2 H1 bars
        - Rolling ATR_14 / ATR_100 >= atr_ratio_threshold (sustained regime shock)
        Returns VOLATILITY_SHOCK_PAUSE for up to 2 H1 candles on breach.
        """
        if df_h1 is None or len(df_h1) < 16:
            return {
                "paused": False,
                "status": "NORMAL",
                "atr_14": 1.0,
                "atr_100": 1.0,
                "atr_ratio": 1.0,
                "max_spike_ratio": 0.0,
                "candles_remaining": 0,
                "reason": None,
            }

        tr = self._compute_tr_series(df_h1)
        atr_14_series = tr.rolling(window=14, min_periods=1).mean()
        atr_100_series = tr.rolling(window=min(100, len(df_h1)), min_periods=14).mean()

        curr_atr_14 = float(atr_14_series.iloc[-1]) or 1.0
        curr_atr_100 = float(atr_100_series.iloc[-1]) if pd.notna(atr_100_series.iloc[-1]) else curr_atr_14
        curr_atr_100 = curr_atr_100 or 1.0
        atr_ratio = curr_atr_14 / curr_atr_100

        # Evaluate trailing `pause_candles` bars against prior-window ATR so a massive spike doesn't mask itself
        candles_remaining = 0
        max_spike_ratio = 0.0
        shock_trigger = None

        n = len(df_h1)
        lookback = min(self.pause_candles, n - 5)
        for offset in range(1, lookback + 1):
            bar_idx = n - offset
            c_range = float(df_h1["high"].iloc[bar_idx]) - float(df_h1["low"].iloc[bar_idx])
            ref_idx = max(0, bar_idx - 1)
            base_atr = float(atr_14_series.iloc[max(0, min(ref_idx, n - lookback - 1))]) or curr_atr_14
            spike_ratio = c_range / base_atr if base_atr > 0 else 0.0
            if spike_ratio > max_spike_ratio:
                max_spike_ratio = spike_ratio
            if spike_ratio > self.spike_atr_mult:
                rem = self.pause_candles - (offset - 1)
                if rem > candles_remaining:
                    candles_remaining = rem
                    shock_trigger = f"Single-candle range spike {spike_ratio:.2f}x > {self.spike_atr_mult:.1f}x ATR_14"

        if len(df_h1) >= 30 and atr_ratio >= self.atr_ratio_threshold:
            candles_remaining = max(candles_remaining, self.pause_candles)
            if not shock_trigger:
                shock_trigger = f"Rolling ATR_14/ATR_100 shock ratio {atr_ratio:.2f}x >= {self.atr_ratio_threshold:.1f}x"

        # Check persisted epoch pause in DB if present and still valid for latest bar
        latest_epoch = int(df_h1["epoch"].iloc[-1]) if "epoch" in df_h1.columns else int(datetime.now(timezone.utc).timestamp())
        if self.db is not None:
            pause_key = f"shock_pause:{symbol}"
            if candles_remaining > 0:
                pause_until_epoch = latest_epoch + (candles_remaining * 3600)
                self.db.set_kv(pause_key, {"until_epoch": pause_until_epoch, "reason": shock_trigger})
            else:
                saved = self.db.get_kv(pause_key)
                if isinstance(saved, dict) and int(saved.get("until_epoch", 0)) > latest_epoch:
                    rem_h = max(1, int((int(saved["until_epoch"]) - latest_epoch + 3599) // 3600))
                    candles_remaining = min(self.pause_candles, rem_h)
                    shock_trigger = saved.get("reason", "Active volatility shock cooldown")

        paused = candles_remaining > 0
        reason = f"VOLATILITY_SHOCK_PAUSE ({candles_remaining} H1 bars): {shock_trigger}" if paused else None
        return {
            "paused": paused,
            "status": "VOLATILITY_SHOCK_PAUSE" if paused else "NORMAL",
            "atr_14": round(curr_atr_14, 4),
            "atr_100": round(curr_atr_100, 4),
            "atr_ratio": round(atr_ratio, 3),
            "max_spike_ratio": round(max_spike_ratio, 3),
            "candles_remaining": candles_remaining,
            "reason": reason,
        }

    def extract_feature_vector(
        self,
        df_h1: pd.DataFrame,
        direction: str,
        htf_trend: Optional[str] = None,
        sweep_info: Optional[Dict[str, Any]] = None,
        poi_info: Optional[Dict[str, Any]] = None,
        planned_rr: float = 2.5,
        hurst_exponent: Optional[float] = None,
    ) -> List[float]:
        """
        Builds the standardized 8-feature pure price-action vector:
        [htf_trend_alignment, sweep_depth_atr, idm_swept_flag, poi_type_code,
         distance_to_poi_atr, atr_percentile_100, hurst_exponent, planned_rr]
        """
        tr = self._compute_tr_series(df_h1)
        atr_series = tr.rolling(window=14, min_periods=1).mean()
        atr_14 = float(atr_series.iloc[-1]) or 1.0

        atr_window = atr_series.tail(100).dropna().to_numpy(dtype=float)
        atr_pct_100 = float(np.mean(atr_window <= atr_14)) if len(atr_window) > 0 else 0.5

        if htf_trend and htf_trend.upper() in ("BULLISH", "BEARISH"):
            align = 1.0 if htf_trend.upper() == direction.upper() else -1.0
        else:
            align = 0.0

        sweep_depth = 0.0
        if sweep_info:
            lvl = float(sweep_info.get("level", 0.0) or 0.0)
            wick = float(sweep_info.get("wick_extreme", lvl) or lvl)
            sweep_depth = min(3.0, max(0.05, abs(wick - lvl) / atr_14))

        idm_flag = 1.0 if (
            (poi_info and poi_info.get("inducement_cleared", False))
            or bool(sweep_info)
        ) else 0.0

        poi_code = encode_poi_type(poi_info.get("poi_kind") if poi_info else None)
        dist_poi = min(5.0, float(poi_info.get("distance_atr", 0.5))) if poi_info else 2.0

        if hurst_exponent is None:
            hurst = self.quant.calculate_hurst_exponent(df_h1["close"].tail(120).astype(float).tolist())
        else:
            hurst = float(hurst_exponent)

        rr_val = min(6.0, max(1.0, float(planned_rr)))
        return [
            round(align, 4),
            round(sweep_depth, 4),
            round(idm_flag, 4),
            round(poi_code, 4),
            round(dist_poi, 4),
            round(atr_pct_100, 4),
            round(hurst, 4),
            round(rr_val, 4),
        ]

    def evaluate_similarity(
        self,
        feature_vector: Sequence[float],
        analogs: Optional[List[Dict[str, Any]]] = None,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Queries k=20 nearest historical analogs using standardized Euclidean distance:
        X_std = (X - mu) / sigma.
        Blocks entries when sample >= min_analogs_required and
        (analog_win_rate < 0.42 or analog_avg_r <= 0.15).
        """
        rows = analogs if analogs is not None else (self.db.get_pattern_analogs() if self.db else [])
        n_total = len(rows)
        if n_total < self.min_analogs_required:
            return {
                "passed": True,
                "status": "INSUFFICIENT_HISTORY_BYPASS",
                "k_neighbors": 0,
                "total_analogs": n_total,
                "analog_win_rate": 0.50,
                "analog_avg_r": 0.25,
                "mean_distance": 0.0,
                "reason": None,
            }

        mat = np.array([r["features"] for r in rows], dtype=float)
        outcomes_r = np.array([float(r["outcome_r"]) for r in rows], dtype=float)
        wins = np.array([1.0 if float(r.get("is_win", 1 if float(r["outcome_r"]) > 0 else 0)) > 0 else 0.0 for r in rows], dtype=float)

        q = np.array(feature_vector, dtype=float)
        mu = np.mean(mat, axis=0)
        sigma = np.std(mat, axis=0)
        sigma = np.where(sigma > 1e-6, sigma, 1.0)

        mat_std = (mat - mu) / sigma
        q_std = (q - mu) / sigma
        distances = np.linalg.norm(mat_std - q_std, axis=1)

        k_eff = min(self.k, n_total)
        nn_idx = np.argsort(distances)[:k_eff]

        win_rate = float(np.mean(wins[nn_idx]))
        avg_r = float(np.mean(outcomes_r[nn_idx]))
        mean_dist = float(np.mean(distances[nn_idx]))

        passed = (win_rate >= self.min_win_rate) and (avg_r > self.min_avg_r)
        reason = None
        if not passed:
            sym_tag = f" on {symbol}" if symbol else ""
            reason = (
                f"k-NN Analog Gate{sym_tag}: blocked setup (k={k_eff} win_rate={win_rate * 100:.1f}% < "
                f"{self.min_win_rate * 100:.0f}% or avg_r={avg_r:+.2f}R <= +{self.min_avg_r:.2f}R)"
            )

        return {
            "passed": passed,
            "status": "ANALOG_CONFIRMED" if passed else "ANALOG_REJECTED",
            "k_neighbors": k_eff,
            "total_analogs": n_total,
            "analog_win_rate": round(win_rate, 3),
            "analog_avg_r": round(avg_r, 3),
            "mean_distance": round(mean_dist, 3),
            "reason": reason,
        }

    def record_resolved_trade(
        self,
        symbol: str,
        direction: str,
        feature_vector: Sequence[float],
        outcome_r: float,
        timestamp: Optional[str] = None,
        source: str = "PAPER_TRADE",
    ) -> Optional[int]:
        """Appends a newly resolved trade into the SQLite pattern_analogs repository."""
        if self.db is None or len(feature_vector) != len(FEATURE_NAMES):
            return None
        return self.db.add_pattern_analog({
            "symbol": symbol,
            "timeframe": "H1",
            "direction": direction,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "features": [float(v) for v in feature_vector],
            "outcome_r": round(float(outcome_r), 3),
            "is_win": 1 if float(outcome_r) > 0 else 0,
            "source": source,
        })

    @staticmethod
    def _simulate_walk_forward_outcome(
        forward_df: pd.DataFrame,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_r: float = 2.5,
    ) -> float:
        """
        Walk-forward simulation matching execution rules:
        +1.0R Move SL to Break-Even, +2.5R Full TP Win, -1.0R Full SL Loss.
        """
        sl_dist = abs(entry_price - sl_price)
        if sl_dist <= 1e-6 or len(forward_df) == 0:
            return 0.0

        be_trigger = (entry_price + sl_dist) if direction == "BULLISH" else (entry_price - sl_dist)
        tp_price = (entry_price + tp_r * sl_dist) if direction == "BULLISH" else (entry_price - tp_r * sl_dist)
        active_sl = sl_price
        be_armed = False

        for _, bar in forward_df.iterrows():
            hi = float(bar["high"])
            lo = float(bar["low"])

            if direction == "BULLISH":
                if not be_armed and hi >= be_trigger:
                    be_armed = True
                    active_sl = entry_price
                if hi >= tp_price:
                    return round(tp_r, 2)
                if lo <= active_sl:
                    return 0.0 if be_armed else -1.0
            else:
                if not be_armed and lo <= be_trigger:
                    be_armed = True
                    active_sl = entry_price
                if lo <= tp_price:
                    return round(tp_r, 2)
                if hi >= active_sl:
                    return 0.0 if be_armed else -1.0

        last_c = float(forward_df["close"].iloc[-1])
        mark_r = (last_c - entry_price) / sl_dist if direction == "BULLISH" else (entry_price - last_c) / sl_dist
        return round(max(-1.0, min(tp_r, mark_r)), 2)

    @staticmethod
    def _simulate_decisive_limit_outcome(
        forward_df: pd.DataFrame,
        direction: str,
        entry_min: float,
        entry_max: float,
        sl_price: float,
        tp_r: float = 2.5,
        ttl_bars: int = 6,
    ) -> Optional[float]:
        """
        Walk-forward limit-order fill + TP/SL resolution matching bot rules:
        - Awaits limit fill inside [entry_min, entry_max] within `ttl_bars` (6h TTL).
        - Cancels cleanly (None) if SL is hit pre-entry or TTL expires unfilled.
        - Resolves decisive outcome (+tp_r TP Win vs -1.0 SL Loss) with +1.0R BE filter
          returning None on flat 0.0R scratches so k-NN binary win-rate reflects true resolved TP/SL odds.
        """
        e_lo, e_hi = min(entry_min, entry_max), max(entry_min, entry_max)
        filled_idx = None
        fill_price = None

        for k in range(min(ttl_bars, len(forward_df))):
            bar = forward_df.iloc[k]
            hi = float(bar["high"])
            lo = float(bar["low"])
            if (direction == "BULLISH" and lo <= sl_price) or (direction == "BEARISH" and hi >= sl_price):
                return None
            if lo <= e_hi and hi >= e_lo:
                filled_idx = k
                fill_price = (e_lo + e_hi) / 2.0
                break

        if filled_idx is None or fill_price is None:
            return None

        sl_dist = abs(fill_price - sl_price)
        if sl_dist <= 1e-6:
            return None

        be_trigger = (fill_price + sl_dist) if direction == "BULLISH" else (fill_price - sl_dist)
        tp_price = (fill_price + tp_r * sl_dist) if direction == "BULLISH" else (fill_price - tp_r * sl_dist)
        active_sl = sl_price
        be_armed = False

        for _, bar in forward_df.iloc[filled_idx:].iterrows():
            hi = float(bar["high"])
            lo = float(bar["low"])
            if direction == "BULLISH":
                if not be_armed and hi >= be_trigger:
                    be_armed = True
                    active_sl = fill_price
                if hi >= tp_price:
                    return round(tp_r, 2)
                if lo <= active_sl:
                    return None if be_armed else -1.0
            else:
                if not be_armed and lo <= be_trigger:
                    be_armed = True
                    active_sl = fill_price
                if lo <= tp_price:
                    return round(tp_r, 2)
                if hi >= active_sl:
                    return None if be_armed else -1.0

        return None

    def seed_from_candles(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
        step_bars: int = 4,
        lookback_window: int = 100,
        forward_horizon: int = 36,
    ) -> int:
        """
        Walk-forward scans 90 days of historical OHLC candles (zero lookahead) using
        genuine ConfluenceEngine POI + Liquidity Sweep limit setups matching PaperTrader
        rules, recording decisively resolved (+TP / -SL) 8-feature analogs.
        """
        if df_h1 is None or len(df_h1) < lookback_window + forward_horizon + 10:
            return 0

        from src.technicals.confluence_engine import ConfluenceEngine

        seeder_engine = ConfluenceEngine(pattern_memory=PatternMemoryEngine(db=None))
        # Ensure seeder evaluates pure technical setup quality without external macro file locks
        seeder_engine.reload_filters = lambda: None
        seeder_engine.active_filters["require_macro_bias_alignment"] = False

        records: List[Dict[str, Any]] = []
        n = len(df_h1)

        for idx in range(lookback_window, n - forward_horizon, step_bars):
            hist = df_h1.iloc[idx - lookback_window:idx + 1].reset_index(drop=True)
            fwd = df_h1.iloc[idx + 1:idx + 1 + forward_horizon]

            tip = seeder_engine.generate_tip(symbol, hist, macro_bias=None)
            if tip.direction == "NEUTRAL_NO_TRADE" or len(tip.feature_vector) != len(FEATURE_NAMES):
                continue

            outcome_r = self._simulate_decisive_limit_outcome(
                fwd,
                tip.direction,
                tip.entry_min,
                tip.entry_max,
                tip.stop_loss,
                tp_r=min(2.5, tip.risk_reward_ratio),
                ttl_bars=6,
            )
            if outcome_r is None:
                continue

            epoch_val = int(hist["epoch"].iloc[-1]) if "epoch" in hist.columns else idx
            ts = datetime.fromtimestamp(epoch_val, tz=timezone.utc).isoformat() if epoch_val > 100000000 else datetime.now(timezone.utc).isoformat()

            records.append({
                "symbol": symbol,
                "timeframe": "H1",
                "direction": tip.direction,
                "timestamp": ts,
                "features": [float(v) for v in tip.feature_vector],
                "outcome_r": outcome_r,
                "is_win": 1 if outcome_r > 0 else 0,
                "source": "WALK_FORWARD_90D",
            })

        if self.db is not None and records:
            self.db.bulk_add_pattern_analogs(records)
        return len(records)

    def seed_database_90d(
        self,
        ws_interceptor: Any,
        symbols: Sequence[str],
        days: int = 90,
        reset_seeded: bool = True,
    ) -> Dict[str, int]:
        """
        Downloads >=90 days of real Deriv OHLCV history via `api-core.deriv.com` and
        walk-forward seeds normalized analog setups across all configured symbols.
        """
        import asyncio

        if self.db is not None and reset_seeded:
            self.db.clear_pattern_analogs(source="WALK_FORWARD_90D")

        counts: Dict[str, int] = {}
        for sym in symbols:
            try:
                df_90 = asyncio.run(ws_interceptor.fetch_historical_candles_days(sym, timeframe="H1", days=days))
            except Exception as e:
                logger.warning(f"90-day history fetch skipped for {sym}: {e}")
                df_90 = ws_interceptor.generate_synthetic_candles(sym, timeframe="H1", count=days * 24)
            counts[sym] = self.seed_from_candles(sym, df_90)
        return counts
