"""
Unit tests for Deriv chart symbol/timeframe UI mappings and non-overlapping 36px screenshot header banner.
"""

import os
from PIL import Image
import pytest

from src.browser.deriv_scraper import DerivScraper
from src.browser.screenshot_manager import ScreenshotManager, HEADER_HEIGHT_PX


def test_symbol_and_timeframe_mappings():
    spec_75 = DerivScraper.get_ui_spec("1HZ75V")
    assert spec_75["tv_code"] == "1HZ75V"
    assert "75 (1s)" in spec_75["search"]

    spec_step = DerivScraper.get_ui_spec("STEPINDEX")
    assert spec_step["tv_code"] == "STPRNG"
    assert "Step Index" in spec_step["search"]

    assert DerivScraper.get_timeframe_spec("M15")["data_value"] == "15"
    assert DerivScraper.get_timeframe_spec("H1")["data_value"] == "60"
    assert DerivScraper.get_timeframe_spec("H4")["data_value"] == "240"


def test_top_banner_expansion_and_zero_overlap(tmp_path):
    mgr = ScreenshotManager(base_dir=str(tmp_path), max_file_size_kb=250)
    # Create mock 1280x720 bright white chart image so top border addition is unmistakable
    raw_chart = Image.new("RGB", (1280, 720), color=(255, 255, 255))

    badged = mgr.stamp_metadata_badge(
        raw_chart,
        trade_id="TRD-TEST-BANNER",
        symbol="1HZ25V",
        timeframe="H1",
        stage="BEFORE",
        entry_price=2800.0,
        sl_price=2780.0,
        tp_price=2850.0,
    )

    assert badged.width == 1280
    assert badged.height == 720 + HEADER_HEIGHT_PX
    # Top corner of banner is dark #0B0F19 (11, 15, 25); original chart area starts untouched at y >= HEADER_HEIGHT_PX
    assert badged.getpixel((0, 0)) == (11, 15, 25)
    assert badged.getpixel((640, HEADER_HEIGHT_PX + 10)) == (255, 255, 255)

    out_png = os.path.join(str(tmp_path), "banner_test.png")
    mgr.optimize_and_save_png(badged, out_png)
    assert os.path.exists(out_png)
    assert (os.path.getsize(out_png) / 1024.0) <= 250.0
