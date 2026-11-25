"""
Deriv WebSocket Interceptor & Client.
Connects to Deriv's active WebSocket feed (wss://api-core.deriv.com/options/v1/ws/public
with legacy wss://ws.derivws.com/websockets/v3 fallback) for exact OHLCV candle extraction.
Includes local calibrated fallback generator for offline unit testing.
"""

import json
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np
import websockets

from src.database.db_manager import DatabaseManager

logger = logging.getLogger(__name__)


class DerivWsInterceptor:
    PRIMARY_WS_URL = "wss://api-core.deriv.com/options/v1/ws/public"
    LEGACY_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"

    # Internal symbol -> Deriv WebSocket symbol mapping
    WS_SYMBOL_MAP = {
        "STEPINDEX": "STPRNG",
        "STPRNG": "STPRNG",
        "1HZ10V": "1HZ10V",
        "1HZ25V": "1HZ25V",
        "1HZ50V": "1HZ50V",
        "1HZ75V": "1HZ75V",
        "1HZ100V": "1HZ100V",
        "R_10": "R_10",
        "R_25": "R_25",
        "R_50": "R_50",
        "R_75": "R_75",
        "R_100": "R_100",
        "BOOM1000": "BOOM1000",
        "CRASH1000": "CRASH1000",
        "JD10": "JD10",
        "JD25": "JD25",
        "JD50": "JD50",
        "JD75": "JD75",
        "JD100": "JD100",
    }

    # Timeframe mappings to seconds
    TIMEFRAME_GRANULARITY = {
        "M15": 900,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400
    }

    def __init__(self, db: Optional[DatabaseManager] = None):
        self.db = db
        self.last_source = "UNKNOWN"
        self._candle_cache: Dict[tuple, tuple[float, pd.DataFrame]] = {}

    def resolve_ws_symbol(self, symbol: str) -> str:
        return self.WS_SYMBOL_MAP.get(symbol.upper(), symbol)

    async def fetch_candles_ws(self, symbol: str, timeframe: str = "H1", count: int = 300) -> Optional[pd.DataFrame]:
        """
        Queries Deriv WebSocket API ticks_history for exact historical candles.
        Tries modern api-core.deriv.com endpoint first, then legacy derivws endpoint.
        """
        ws_symbol = self.resolve_ws_symbol(symbol)
        granularity = self.TIMEFRAME_GRANULARITY.get(timeframe.upper(), 3600)
        req = {
            "ticks_history": ws_symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "start": 1,
            "style": "candles",
            "granularity": granularity
        }

        endpoints = [
            (self.PRIMARY_WS_URL, "https://charts.deriv.com"),
            (self.LEGACY_WS_URL, "https://app.deriv.com"),
        ]

        last_err = None
        for url, origin in endpoints:
            try:
                async with websockets.connect(
                    url,
                    open_timeout=10,
                    close_timeout=5,
                    origin=origin,
                ) as ws:
                    await ws.send(json.dumps(req))
                    response_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    data = json.loads(response_raw)

                    if "error" in data:
                        last_err = data["error"].get("message", str(data["error"]))
                        continue

                    if "candles" in data and data["candles"]:
                        records = [
                            {
                                "epoch": c["epoch"],
                                "open": float(c["open"]),
                                "high": float(c["high"]),
                                "low": float(c["low"]),
                                "close": float(c["close"]),
                                "volume": float(c.get("volume", 100))
                            }
                            for c in data["candles"]
                        ]
                        df = pd.DataFrame(records)
                        df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
                        df = df.sort_values("epoch").reset_index(drop=True)
                        self.last_source = url

                        if self.db:
                            self._save_candles_to_db(symbol, timeframe, records)
                        return df
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                continue

        logger.warning(f"Deriv WebSocket candle fetch failed for {symbol} ({ws_symbol}) [{timeframe}]: {last_err}")
        return None

    def _save_candles_to_db(self, symbol: str, timeframe: str, records: List[Dict]):
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            for r in records[-50:]:
                cursor.execute("""
                INSERT OR REPLACE INTO candles (symbol, timeframe, epoch, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, timeframe, r["epoch"], r["open"], r["high"], r["low"], r["close"], r["volume"]))
            conn.commit()

    def generate_synthetic_candles(self, symbol: str, timeframe: str = "H1", count: int = 150) -> pd.DataFrame:
        """
        Generates calibrated synthetic Geometric Brownian Motion candles for offline unit testing.
        """
        np.random.seed(int(time.time()) % 100000)
        base_prices = {
            "1HZ10V": 9700.0,
            "1HZ25V": 2800.0,
            "1HZ50V": 1450.0,
            "1HZ75V": 3330.0,
            "1HZ100V": 1600.0,
            "R_10": 4835.0,
            "R_25": 2960.0,
            "R_50": 77.3,
            "R_75": 45800.0,
            "R_100": 437.0,
            "BOOM1000": 15780.0,
            "CRASH1000": 5750.0,
            "STEPINDEX": 7358.0,
            "STPRNG": 7358.0,
            "JD10": 89900.0,
            "JD25": 126600.0,
            "JD50": 79100.0,
            "JD75": 3918.0,
            "JD100": 47.3,
        }
        start_price = base_prices.get(symbol, 2500.0)
        granularity = self.TIMEFRAME_GRANULARITY.get(timeframe.upper(), 3600)

        vol_scale = 0.008 if "10" in symbol else (0.015 if "25" in symbol else 0.025)
        if symbol in ("STEPINDEX", "STPRNG"):
            vol_scale = 0.002

        current_time = int(time.time()) - (count * granularity)
        records = []
        curr_price = start_price

        for i in range(count):
            epoch = current_time + (i * granularity)
            ret = np.random.normal(0, vol_scale)
            next_close = curr_price * np.exp(ret)

            c_open = curr_price
            c_close = next_close
            spread = abs(c_close - c_open)
            wick_up = np.random.exponential(max(spread * 0.4, 0.001))
            wick_down = np.random.exponential(max(spread * 0.4, 0.001))

            c_high = max(c_open, c_close) + wick_up
            c_low = min(c_open, c_close) - wick_down

            records.append({
                "epoch": epoch,
                "open": round(c_open, 4),
                "high": round(c_high, 4),
                "low": round(c_low, 4),
                "close": round(c_close, 4),
                "volume": float(np.random.randint(50, 300))
            })
            curr_price = c_close

        self.last_source = "SYNTHETIC_FALLBACK"
        df = pd.DataFrame(records)
        df["datetime"] = pd.to_datetime(df["epoch"], unit="s")
        return df

    def get_candles(self, symbol: str, timeframe: str = "H1", count: int = 150) -> pd.DataFrame:
        """
        Attempts live Deriv WebSocket fetch first (with 120s in-memory cache per run);
        falls back gracefully to calibrated synthetic feed.
        """
        cache_key = (self.resolve_ws_symbol(symbol), timeframe.upper(), count)
        now_ts = time.time()
        cached = self._candle_cache.get(cache_key)
        if cached and (now_ts - cached[0]) < 120.0:
            return cached[1].copy()

        try:
            df = asyncio.run(self.fetch_candles_ws(symbol, timeframe, count))
        except Exception as e:
            logger.warning(f"Async candle fetch failed for {symbol} [{timeframe}]: {e}")
            df = None

        if df is not None and len(df) >= 30:
            self._candle_cache[cache_key] = (now_ts, df)
            return df.copy()
        return self.generate_synthetic_candles(symbol, timeframe, count)
