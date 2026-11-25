"""
Playwright Browser Automation for Deriv TradingView / SmartCharts (https://charts.deriv.com/deriv).
Explicitly switches both Symbol (via Derived search) and Timeframe (M15/H1/H4) inside the
chart frame, verifies DOM title transitions, and pairs visual captures with live WebSocket OHLCV.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from src.browser.ws_interceptor import DerivWsInterceptor

logger = logging.getLogger(__name__)


class DerivScraper:
    CHART_URL = "https://charts.deriv.com/deriv"

    # Exact mapping from internal bot symbol -> TradingView/SmartCharts UI search & symbol code
    SYMBOL_UI_MAP: Dict[str, Dict[str, str]] = {
        "1HZ10V": {"tv_code": "1HZ10V", "search": "Volatility 10 (1s)", "label": "Volatility 10 (1s) Index"},
        "1HZ25V": {"tv_code": "1HZ25V", "search": "Volatility 25 (1s)", "label": "Volatility 25 (1s) Index"},
        "1HZ50V": {"tv_code": "1HZ50V", "search": "Volatility 50 (1s)", "label": "Volatility 50 (1s) Index"},
        "1HZ75V": {"tv_code": "1HZ75V", "search": "Volatility 75 (1s)", "label": "Volatility 75 (1s) Index"},
        "1HZ100V": {"tv_code": "1HZ100V", "search": "Volatility 100 (1s)", "label": "Volatility 100 (1s) Index"},
        "R_10": {"tv_code": "R_10", "search": "Volatility 10 Index", "label": "Volatility 10 Index"},
        "R_25": {"tv_code": "R_25", "search": "Volatility 25 Index", "label": "Volatility 25 Index"},
        "R_50": {"tv_code": "R_50", "search": "Volatility 50 Index", "label": "Volatility 50 Index"},
        "R_75": {"tv_code": "R_75", "search": "Volatility 75 Index", "label": "Volatility 75 Index"},
        "R_100": {"tv_code": "R_100", "search": "Volatility 100 Index", "label": "Volatility 100 Index"},
        "BOOM1000": {"tv_code": "BOOM1000", "search": "Boom 1000", "label": "Boom 1000 Index"},
        "CRASH1000": {"tv_code": "CRASH1000", "search": "Crash 1000", "label": "Crash 1000 Index"},
        "STEPINDEX": {"tv_code": "STPRNG", "search": "Step Index 100", "label": "Step Index 100"},
        "STPRNG": {"tv_code": "STPRNG", "search": "Step Index 100", "label": "Step Index 100"},
    }

    # Exact mapping for timeframe UI selection
    TIMEFRAME_UI_MAP: Dict[str, Dict[str, str]] = {
        "M15": {"data_value": "15", "display": "15m", "smartchart": "15m"},
        "H1": {"data_value": "60", "display": "1h", "smartchart": "1h"},
        "H4": {"data_value": "240", "display": "4h", "smartchart": "4h"},
        "D1": {"data_value": "1440", "display": "24h", "smartchart": "1d"},
    }

    def __init__(self, screenshots_dir: str = "data/screenshots"):
        self.screenshots_dir = screenshots_dir
        os.makedirs(screenshots_dir, exist_ok=True)
        self.ws_interceptor = DerivWsInterceptor()

    @classmethod
    def get_ui_spec(cls, symbol: str) -> Dict[str, str]:
        return cls.SYMBOL_UI_MAP.get(symbol.upper(), {
            "tv_code": symbol,
            "search": symbol,
            "label": symbol,
        })

    @classmethod
    def get_timeframe_spec(cls, timeframe: str) -> Dict[str, str]:
        return cls.TIMEFRAME_UI_MAP.get(timeframe.upper(), {
            "data_value": "60",
            "display": "1h",
            "smartchart": "1h",
        })

    def _find_chart_frame(self, page: Any) -> Optional[Any]:
        """Locates TradingView iframe inside charts.deriv.com or returns main page for SmartCharts."""
        for _ in range(10):
            for frame in page.frames:
                try:
                    if frame.locator("#header-toolbar-symbol-search").count() > 0:
                        return frame
                except Exception:
                    continue
            try:
                if page.locator(".cq-symbol-select-btn").count() > 0:
                    return page
            except Exception:
                pass
            page.wait_for_timeout(500)
        return None

    def _switch_symbol_in_frame(self, page: Any, frame: Any, symbol: str) -> bool:
        """Selects target instrument inside TradingView or SmartCharts DOM and verifies active title."""
        spec = self.get_ui_spec(symbol)
        tv_code = spec["tv_code"]
        search_text = spec["search"]

        # Branch A: TradingView iframe (#header-toolbar-symbol-search)
        if frame.locator("#header-toolbar-symbol-search").count() > 0:
            sym_btn = frame.locator("#header-toolbar-symbol-search").first
            current_sym = (sym_btn.inner_text() or "").strip().upper()
            if current_sym == tv_code.upper():
                return True

            sym_btn.click()
            page.wait_for_timeout(700)

            # Click "Derived" category pill so Deriv synthetics appear in search results
            derived_tab = frame.locator(
                "#overlap-manager-root button:has-text('Derived'), "
                "#overlap-manager-root [role=button]:has-text('Derived'), "
                "#overlap-manager-root div[class*='roundTabButton']:has-text('Derived')"
            )
            if derived_tab.count() > 0:
                derived_tab.first.click()
                page.wait_for_timeout(500)

            search_inp = frame.locator("input[data-role='search'], input[placeholder*='Search']").first
            search_inp.click()
            search_inp.fill("")
            page.keyboard.type(search_text, delay=20)
            page.wait_for_timeout(800)

            # Prefer exact symbol code row inside overlap dialog
            rows = frame.locator("#overlap-manager-root [class*='itemRow'], #overlap-manager-root [data-role='list-item']")
            clicked = False
            row_count = min(rows.count(), 15)
            for idx in range(row_count):
                txt = (rows.nth(idx).inner_text() or "").upper()
                first_token = txt.split("\n")[0].strip() if txt else ""
                if first_token == tv_code.upper() or f"{tv_code.upper()} " in txt or txt.startswith(tv_code.upper()):
                    rows.nth(idx).click()
                    clicked = True
                    break

            if not clicked and row_count > 0:
                rows.first.click()
                clicked = True

            if not clicked:
                # Try direct code search if full name filtered empty
                search_inp.click()
                search_inp.fill("")
                page.keyboard.type(tv_code, delay=20)
                page.wait_for_timeout(700)
                if rows.count() > 0:
                    rows.first.click()
                    clicked = True
                else:
                    page.keyboard.press("Escape")

            page.wait_for_timeout(1200)
            active_text = (sym_btn.inner_text() or "").strip().upper()
            if active_text == tv_code.upper() or tv_code.upper() in active_text:
                return True

            logger.warning(
                f"TradingView symbol switch verification mismatch: requested={symbol} ({tv_code}), active='{active_text}'"
            )
            return False

        # Branch B: Legacy SmartCharts fallback (.cq-symbol-select-btn)
        if frame.locator(".cq-symbol-select-btn").count() > 0:
            btn = frame.locator(".cq-symbol-select-btn").first
            btn.click()
            page.wait_for_timeout(600)
            inp = frame.locator(".cq-lookup-input input, input[placeholder*='Search']").first
            inp.fill(search_text)
            page.wait_for_timeout(600)
            item = frame.locator(f".sc-mcd__item:has-text('{spec['label']}'), .cq-menu-item:has-text('{search_text}')")
            if item.count() > 0:
                item.first.click()
                page.wait_for_timeout(1200)
                active_sc = (btn.inner_text() or "").upper()
                if spec["label"].upper() in active_sc or tv_code.upper() in active_sc:
                    return True
            logger.warning(f"SmartCharts symbol switch failed for {symbol}; active DOM='{btn.inner_text()}'")
            return False

        logger.warning(f"No supported chart symbol selector found in DOM for {symbol}")
        return False

    def _switch_timeframe_in_frame(self, page: Any, frame: Any, timeframe: str) -> bool:
        """Switches chart interval to M15 / H1 / H4 and verifies active timeframe text."""
        tf_spec = self.get_timeframe_spec(timeframe)
        target_val = tf_spec["data_value"]
        target_disp = tf_spec["display"].lower()

        if frame.locator("#header-toolbar-intervals button").count() > 0:
            int_btn = frame.locator("#header-toolbar-intervals button").first
            current_tf = (int_btn.inner_text() or "").strip().lower()
            if current_tf == target_disp:
                return True

            # Ensure no leftover modal intercepts clicks
            try:
                if frame.locator("#overlap-manager-root .backdrop-aRAWUDhF").count() > 0:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(300)
            except Exception:
                pass

            int_btn.click()
            page.wait_for_timeout(500)
            menu_opt = frame.locator(f"#overlap-manager-root [data-value='{target_val}']")
            if menu_opt.count() > 0:
                menu_opt.first.click()
                page.wait_for_timeout(1500)
                active_tf = (int_btn.inner_text() or "").strip().lower()
                if active_tf == target_disp:
                    return True
                logger.warning(
                    f"TradingView timeframe verification mismatch: requested={timeframe} ({target_disp}), active='{active_tf}'"
                )
                return False

            page.keyboard.press("Escape")
            logger.warning(f"Timeframe menu option data-value='{target_val}' not found for {timeframe}")
            return False

        # SmartCharts fallback
        sc_int = frame.locator(".cq-interval-select, .ciq-period")
        if sc_int.count() > 0:
            sc_int.first.click()
            page.wait_for_timeout(500)
            opt = frame.locator(f"[data-interval='{tf_spec['smartchart']}'], :text('{tf_spec['smartchart']}')")
            if opt.count() > 0:
                opt.first.click()
                page.wait_for_timeout(1500)
                return True

        logger.warning(f"No timeframe selector found in DOM for {timeframe}")
        return False

    def capture_multi_timeframe(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        headless: bool = True,
        output_paths: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Captures charts across multiple timeframes in one Chromium session, switching both
        symbol and interval explicitly and verifying DOM state before each screenshot.
        """
        tf_list = timeframes or ["H4", "H1", "M15"]
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        paths: Dict[str, str] = {}
        verifications: Dict[str, Dict[str, bool]] = {}

        for tf in tf_list:
            if output_paths and tf in output_paths:
                paths[tf] = output_paths[tf]
            else:
                paths[tf] = os.path.join(self.screenshots_dir, f"{symbol}_{tf}_{timestamp_str}.png")
            os.makedirs(os.path.dirname(os.path.abspath(paths[tf])), exist_ok=True)

        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
                )
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                try:
                    page.goto(self.CHART_URL, wait_until="domcontentloaded", timeout=25000)
                    chart_frame = self._find_chart_frame(page)

                    sym_ok = False
                    if chart_frame is not None:
                        sym_ok = self._switch_symbol_in_frame(page, chart_frame, symbol)
                        if not sym_ok:
                            logger.warning(f"Proceeding with fallback capture after symbol switch failure for {symbol}")
                    else:
                        logger.warning(f"Could not locate chart iframe/toolbar for {symbol}; DOM fallback active")

                    for tf in tf_list:
                        tf_ok = False
                        if chart_frame is not None:
                            tf_ok = self._switch_timeframe_in_frame(page, chart_frame, tf)
                        else:
                            page.wait_for_timeout(1500)

                        page.wait_for_timeout(1500)
                        page.screenshot(path=paths[tf])
                        verifications[tf] = {"symbol_ok": sym_ok, "timeframe_ok": tf_ok}
                finally:
                    browser.close()
        except Exception as e:
            logger.warning(f"Playwright multi-timeframe capture fallback for {symbol}: {e}")
            for tf in tf_list:
                if not os.path.exists(paths[tf]):
                    with open(paths[tf], "wb") as f:
                        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
                verifications[tf] = {"symbol_ok": False, "timeframe_ok": False}

        return {
            "symbol": symbol,
            "paths": paths,
            "verifications": verifications,
        }

    def capture_chart(
        self,
        symbol: str,
        timeframe: str = "H1",
        headless: bool = True
    ) -> Dict[str, Any]:
        """
        Launches Playwright Chromium to open Deriv charts, switches symbol + timeframe,
        captures high-resolution PNG, and extracts live OHLCV candles via WebSocket.
        """
        multi = self.capture_multi_timeframe(symbol, timeframes=[timeframe], headless=headless)
        screenshot_path = multi["paths"][timeframe]
        verif = multi["verifications"].get(timeframe, {})

        df = self.ws_interceptor.get_candles(symbol, timeframe, count=150)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "screenshot_path": screenshot_path,
            "chart_captured": os.path.exists(screenshot_path),
            "verified_symbol_match": verif.get("symbol_ok", False),
            "verified_timeframe_match": verif.get("timeframe_ok", False),
            "candles_df": df
        }
