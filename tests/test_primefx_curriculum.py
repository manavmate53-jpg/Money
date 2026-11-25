"""
Unit Tests for Phase 0 PrimeFX Chronological YouTube Curriculum & Phase 1 Zero-Volume Compliance.
"""

import json
import os
import pytest

from src.training.primefx_trainer import (
    PrimeFxTrainer,
    PrimeFxVideoLessonSchema,
    PrimeFxCurriculumBatchSchema,
    PrimeFxMasterPlaybookSchema,
)
from src.technicals.price_action import PriceActionEngine
from src.technicals.liquidity_engine import LiquidityEngine
from src.technicals.poi_price_action import PoiPriceActionEngine
from src.browser.ws_interceptor import DerivWsInterceptor


def test_primefx_training_sources_config_and_chronological_sort():
    assert os.path.exists("config/training_sources.json")
    with open("config/training_sources.json", "r") as f:
        cfg = json.load(f)
    assert "channel_urls" in cfg and len(cfg["channel_urls"]) >= 1

    unsorted = [
        {"id": "v3", "title": "Latest 2026 Setup", "timestamp": 1795000000, "upload_date": "2026-11-21", "playlist_index": 1},
        {"id": "v1", "title": "Oldest 2023 Lesson", "timestamp": 1700870400, "upload_date": "2023-11-25", "playlist_index": 133},
        {"id": "v2", "title": "Mid 2025 VIX Trade", "timestamp": 1750000000, "upload_date": "2025-06-15", "playlist_index": 60},
    ]
    ordered = PrimeFxTrainer.sort_videos_chronologically(unsorted)
    assert [v["id"] for v in ordered] == ["v1", "v2", "v3"]
    assert [v["chronological_rank"] for v in ordered] == [1, 2, 3]


def test_primefx_curriculum_typed_schemas_no_dict():
    for schema_cls in (PrimeFxVideoLessonSchema, PrimeFxCurriculumBatchSchema, PrimeFxMasterPlaybookSchema):
        dumped = json.dumps(schema_cls.model_json_schema())
        assert "additionalProperties" not in dumped


def test_primefx_distilled_playbook_artifacts_present():
    required_files = [
        "knowledge_base/primefx_video_catalog.json",
        "reports/PRIMEFX_VIDEO_CURRICULUM.md",
        "knowledge_base/primefx_master_playbook.json",
        "knowledge_base/PRIMEFX_LOGIC_AND_FLAWS.md",
    ]
    for path in required_files:
        assert os.path.exists(path), f"Missing deliverable: {path}"
        assert os.path.getsize(path) > 100, f"Empty deliverable: {path}"

    with open("knowledge_base/primefx_video_catalog.json", "r") as f:
        cat = json.load(f)
    assert cat["sort_order"] == "chronological_oldest_to_latest"
    ranks = [v["chronological_rank"] for v in cat["videos"]]
    assert ranks == list(range(1, len(ranks) + 1))

    with open("knowledge_base/primefx_master_playbook.json", "r") as f:
        pb = json.load(f)
    assert len(pb.get("winning_trade_checklist", [])) >= 3
    assert len(pb.get("critical_flaws_to_counter_with_risk_shield", [])) >= 3


def test_pure_price_action_zero_volume_profile_methods():
    pa = PriceActionEngine()
    assert not hasattr(pa, "compute_value_area")
    assert hasattr(pa, "compute_dealing_range")
    assert hasattr(pa, "detect_sr_polarity_flips")

    df = DerivWsInterceptor().generate_synthetic_candles("1HZ25V", count=50)
    analysis = pa.analyze(df)
    assert "value_area" not in analysis
    assert "dealing_range" in analysis
    assert "sr_polarity_flips" in analysis
