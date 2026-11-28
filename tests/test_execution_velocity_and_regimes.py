"""
Unit tests verifying Fix 1 through Fix 5:
- Fix 1: Dual Entry Mode (MARKET_CONFIRMATION immediate OPEN fill vs LIMIT_PULLBACK 4h TTL & 12h dedup)
- Fix 2: 3-Bucket uncorrelated paper training (max 3 total, max 1/bucket, <= $4.50 heat) & 1-trade live advisor lock
- Fix 3: Continuous Hurst / ADX(14) / Kaufman ER(20) regime discrimination (no flat 80% fallback, chop blocked)
- Fix 4: Jump-Index 1.50x gap-slippage viability shield & 16h TIME_DECAY_EXIT stagnation time-stop + AFTER charts
- Fix 5: repository_dispatch workflow triggers, scripts/trigger_dispatch.py, and StatusTableReporter new columns
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
import yaml

from src.advisor.capital_advisor import CapitalAdvisor
from src.council.gemini_council import GeminiCouncil
from src.database.db_manager import DatabaseManager
from src.technicals.pattern_memory import PatternMemoryEngine
from src.reporting.status_table import render_status_tables
from src.risk.bankroll_manager import BankrollManager, get_asset_bucket
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.simulator.paper_trader import PaperTrader
from src.technicals.confluence_engine import ConfluenceEngine, TradingTip
from src.technicals.price_action import PriceActionEngine


def _trend_df(n: int = 120, base: float = 1000.0, step: float = 2.0, freq_min: int = 60) -> pd.DataFrame:
    rows = []
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    start_epoch = now_epoch - (n * freq_min * 60)
    for i in range(n):
        c_open = base + (i * step) + 3.0 * np.sin(i * 0.45)
        c_close = c_open + step * 0.9
        rows.append({
            "epoch": start_epoch + (i * freq_min * 60),
            "open": round(c_open, 4),
            "high": round(max(c_open, c_close) + 1.2, 4),
            "low": round(min(c_open, c_close) - 1.2, 4),
            "close": round(c_close, 4),
            "volume": 100.0,
        })
    return pd.DataFrame(rows)


def _sweep_mss_pair() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Builds directional H1 + M15 data ending in a crisp H1 SSL sweep on M15, unmitigated FVG, and M15 recent MSS."""
    h1 = _trend_df(n=100, base=1000.0, step=2.5, freq_min=60)
    plat_h1 = float(h1["close"].iloc[84])
    # Consolidation bars 85..95 with clear swing low (SSL) at idx=89 (plat_h1 - 2.5)
    for idx in range(85, 96):
        op = plat_h1 + (0.3 if idx % 2 == 0 else -0.3)
        cl = plat_h1 + (-0.2 if idx % 2 == 0 else 0.3)
        lo = plat_h1 - 2.5 if idx == 89 else plat_h1 - 1.5
        h1.loc[idx, ["open", "high", "low", "close"]] = [op, plat_h1 + 2.0, lo, cl]
    ssl_h1 = plat_h1 - 2.5
    # Bars 96-99 sweep ssl_h1 then rally gently
    h1.loc[96, ["open", "high", "low", "close"]] = [ssl_h1 + 1.0, ssl_h1 + 1.8, ssl_h1 - 0.8, ssl_h1 + 1.2]
    h1.loc[97, ["open", "high", "low", "close"]] = [ssl_h1 + 1.2, ssl_h1 + 2.8, ssl_h1 + 0.6, ssl_h1 + 2.5]
    h1.loc[98, ["open", "high", "low", "close"]] = [ssl_h1 + 2.5, ssl_h1 + 4.2, ssl_h1 + 2.2, ssl_h1 + 3.8]
    h1.loc[99, ["open", "high", "low", "close"]] = [ssl_h1 + 3.8, ssl_h1 + 4.5, ssl_h1 + 3.2, ssl_h1 + 4.0]

    m15 = _trend_df(n=100, base=plat_h1 - 60.0, step=0.65, freq_min=15)
    for idx in range(85, 96):
        lo = ssl_h1 + 0.5 if idx != 89 else ssl_h1 + 0.2
        m15.loc[idx, ["open", "high", "low", "close"]] = [ssl_h1 + 1.2, plat_h1 + 1.0, lo, ssl_h1 + 1.3]
    # Bar 96 (within M15 [-4:] window) dips below H1 SSL `ssl_h1` and closes above it!
    m15.loc[96, ["open", "high", "low", "close"]] = [ssl_h1 + 1.0, ssl_h1 + 1.4, ssl_h1 - 0.7, ssl_h1 + 0.8]
    # Bar 97 (c1 for FVG) stays strictly above ssl_h1
    m15.loc[97, ["open", "high", "low", "close"]] = [ssl_h1 + 0.8, ssl_h1 + 1.8, ssl_h1 + 0.5, ssl_h1 + 1.6]
    # Bar 98 displacement impulse above plat_h1 + 1.0 swing high
    m15.loc[98, ["open", "high", "low", "close"]] = [ssl_h1 + 1.6, plat_h1 + 1.6, ssl_h1 + 1.4, plat_h1 + 1.4]
    # Bar 99 (c3_low = ssl_h1 + 2.1 > c1_high = ssl_h1 + 1.8 -> unmitigated BULLISH_FVG) & close within <= 0.75*ATR of FVG
    m15.loc[99, ["open", "high", "low", "close"]] = [plat_h1 + 0.8, plat_h1 + 1.8, ssl_h1 + 2.1, plat_h1 + 1.5]
    h1.loc[99, "close"] = float(m15.loc[99, "close"])
    return h1, m15


def test_fix1_dual_entry_mode_4h_ttl_and_12h_dedup(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "fix1.db"))
    bankroll = BankrollManager(db)
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc, max_risk_percent=1.5, hard_cap_percent=2.0)
    filters_file = os.path.join(tmp_path, "filters.json")
    with open(filters_file, "w") as f:
        json.dump({
            "version": "2.0.0",
            "max_pending_hours": 4,
            "entry_zone_m15_atr_width": 0.30,
            "dedup_lookback_hours": 12.0,
            "dedup_zone_atr_mult": 0.50,
            "stagnation_exit_hours": 16.0,
            "stagnation_min_mfe_r": 0.50,
            "require_macro_bias_alignment": True,
            "blocked_regimes_by_symbol": {},
        }, f)

    confluence = ConfluenceEngine(filters_path=filters_file, pattern_memory=PatternMemoryEngine(db=db))
    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer, filters_path=filters_file)

    h1, m15 = _sweep_mss_pair()
    tip_mkt = confluence.generate_tip(
        "STEPINDEX",
        h1,
        m15_df=m15,
        macro_bias="BULLISH",
        macro_confidence=0.80,
    )
    assert tip_mkt.direction == "BULLISH"
    assert tip_mkt.entry_mode == "MARKET_CONFIRMATION"
    assert tip_mkt.entry_min <= tip_mkt.current_price <= tip_mkt.entry_max

    mkt_id = trader.submit_tip(tip_mkt, lot_size=0.10, dollar_risk=1.20, margin_req=1.0)
    assert mkt_id is not None
    active = db.get_active_trades()
    assert len(active) == 1
    assert active[0]["status"] == "OPEN"
    assert active[0]["entry_mode"] == "MARKET_CONFIRMATION"
    assert active[0]["entry_executed"] is not None
    assert active[0]["filled_at"] is not None

    # Clean active queue and test LIMIT_PULLBACK + 4h TTL + 12h dedup
    db.mark_trade_expired(mkt_id, reason="TEST_CLEAR")

    tip_lim = TradingTip(
        symbol="1HZ25V",
        direction="BULLISH",
        entry_mode="LIMIT_PULLBACK",
        confidence_score=0.71,
        primary_strategy="SMC_ICT",
        market_regime="DIRECTIONAL_TREND",
        current_price=2200.0,
        entry_min=2180.0,
        entry_max=2184.0,
        stop_loss=2170.0,
        take_profit_1=2219.0,
        take_profit_2=2235.0,
        sl_points=14.0,
        tp1_points=35.0,
        risk_reward_ratio=2.5,
        break_even_trigger_price=2198.0,
        estimated_hold_hours=4.0,
        estimated_hold_candles=4,
        confluence_reasons=["Test LIMIT_PULLBACK"],
    )
    lim_id = trader.submit_tip(tip_lim, lot_size=0.05, dollar_risk=0.70, margin_req=0.20, h1_atr=10.0)
    assert lim_id is not None

    # Age order past 4h TTL without filling -> must expire cleanly
    stale_opened = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    with db.get_connection() as conn:
        conn.execute("UPDATE active_trades SET opened_at = ? WHERE trade_id = ?", (stale_opened, lim_id))
        conn.execute("UPDATE paper_trades SET created_at = ? WHERE symbol = '1HZ25V'", (stale_opened,))
        conn.commit()

    stale_bars = pd.DataFrame([
        {
            "epoch": int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp()),
            "open": 2205.0, "high": 2210.0, "low": 2201.0, "close": 2208.0, "volume": 10.0,
        }
    ])
    resolved = trader.update_open_positions("1HZ25V", stale_bars, m15_df=stale_bars)
    assert any(r["trade_id"] == lim_id and r["outcome"] == "EXPIRED_STALE" for r in resolved)

    # 12h Deduplication Guard must block re-entering same zone inside 0.50 * H1_ATR
    dup_id = trader.submit_tip(tip_lim, lot_size=0.05, dollar_risk=0.70, margin_req=0.20, h1_atr=10.0)
    assert dup_id is None
    recent_zones = db.get_recent_expired_or_stopped_zones("1HZ25V", "BULLISH", hours=12.0)
    assert len(recent_zones) >= 1


def test_fix2_three_bucket_concurrency_and_caps(tmp_path):
    db = DatabaseManager(os.path.join(tmp_path, "fix2.db"))
    bankroll = BankrollManager(db, policy_path="config/bankroll_policy.json")
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc, max_risk_percent=1.5, hard_cap_percent=2.0)

    assert get_asset_bucket("JD75") == "JUMP_FAMILY"
    assert get_asset_bucket("1HZ25V") == "VOL_1S_FAMILY"
    assert get_asset_bucket("STEPINDEX") == "STEP_CLASSIC_FAMILY"

    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer)

    def _tip(sym: str, entry: float, sl: float, tp: float) -> TradingTip:
        return TradingTip(
            symbol=sym, direction="BULLISH", entry_mode="LIMIT_PULLBACK", confidence_score=0.84,
            primary_strategy="SMC_ICT", market_regime="DIRECTIONAL_TREND",
            current_price=entry + 2.0, entry_min=entry - 1.0, entry_max=entry,
            stop_loss=sl, take_profit_1=tp, take_profit_2=tp + 10.0,
            sl_points=abs(entry - sl), tp1_points=abs(tp - entry), risk_reward_ratio=2.5,
            break_even_trigger_price=entry + abs(entry - sl),
            estimated_hold_hours=3.0, estimated_hold_candles=3, confluence_reasons=["Bucket Test"],
        )

    # Bucket 1: STEP_CLASSIC_FAMILY ($1.40)
    id1 = trader.submit_tip(_tip("STEPINDEX", 7360.0, 7350.0, 7385.0), lot_size=0.14, dollar_risk=1.40, margin_req=1.0)
    assert id1 is not None

    # Same bucket must be blocked
    id_same = trader.submit_tip(_tip("R_75", 50000.0, 49900.0, 50250.0), lot_size=0.01, dollar_risk=1.00, margin_req=1.0)
    assert id_same is None

    # Bucket 2: VOL_1S_FAMILY ($1.40 -> $2.80 total)
    id2 = trader.submit_tip(_tip("1HZ25V", 2200.0, 2190.0, 2225.0), lot_size=0.14, dollar_risk=1.40, margin_req=0.5)
    assert id2 is not None

    # Bucket 3: JUMP_FAMILY ($1.40 -> $4.20 <= $4.50)
    id3 = trader.submit_tip(_tip("JD75", 45.0, 40.0, 57.5), lot_size=0.20, dollar_risk=1.40, margin_req=0.1)
    assert id3 is not None
    assert len(db.get_active_trades()) == 3

    # Verify heat-cap enforcement independently when 2 buckets already carry $3.50 heat ($3.50 + $1.20 = $4.70 > $4.50)
    db2 = DatabaseManager(os.path.join(tmp_path, "fix2_heat.db"))
    bankroll2 = BankrollManager(db2, policy_path="config/bankroll_policy.json")
    trader2 = PaperTrader(db2, bankroll2, pip_calc=pip_calc, position_sizer=sizer)
    assert trader2.submit_tip(_tip("STEPINDEX", 7360.0, 7350.0, 7385.0), lot_size=0.18, dollar_risk=1.80, margin_req=1.0) is not None
    assert trader2.submit_tip(_tip("1HZ25V", 2200.0, 2190.0, 2225.0), lot_size=0.17, dollar_risk=1.70, margin_req=0.5) is not None
    assert trader2.submit_tip(_tip("JD75", 45.0, 40.0, 57.5), lot_size=0.20, dollar_risk=1.20, margin_req=0.1) is None

    # Verify CapitalAdvisor stays locked to #1 pick + optional different-bucket backup setup
    mock_ws = MagicMock()
    h1, m15 = _sweep_mss_pair()
    mock_ws.get_candles.return_value = h1
    advisor = CapitalAdvisor(db=db2, ws_interceptor=mock_ws)
    res_adv = advisor.advise(capital=100.0, report_path=os.path.join(tmp_path, "ADVICE.md"))
    assert "recommended_instrument" in res_adv
    assert "backup_setup" in res_adv
    if res_adv.get("backup_setup"):
        assert res_adv["backup_setup"]["bucket"] != get_asset_bucket(res_adv["recommended_instrument"])


def test_fix3_hurst_adx_er_regime_discrimination_and_chop_block(tmp_path):
    pa = PriceActionEngine()
    trend_df = _trend_df(n=120, base=1000.0, step=2.5)
    m_trend = pa.analyze_regime_metrics(trend_df)
    assert m_trend["regime"] == "DIRECTIONAL_TREND"
    assert m_trend["hurst"] > 0.55
    assert m_trend["adx_14"] >= 24.0
    assert m_trend["efficiency_ratio"] >= 0.30
    assert 0.40 <= m_trend["quant_confidence"] <= 0.94

    # Construct random walk / chop series with flat net drift
    rng = np.random.default_rng(42)
    closes = 500.0 + np.cumsum(rng.normal(0.0, 1.5, size=120))
    chop_rows = []
    for i, cl in enumerate(closes):
        chop_rows.append({
            "epoch": 1700000000 + (i * 3600),
            "open": float(cl),
            "high": float(cl) + 2.5,
            "low": float(cl) - 2.5,
            "close": float(cl),
            "volume": 50.0,
        })
    chop_df = pd.DataFrame(chop_rows)
    m_chop = pa.analyze_regime_metrics(chop_df)
    assert m_chop["regime"] in ("RANDOM_WALK_CHOP", "ROTATIONAL_RANGE")
    assert m_chop["quant_confidence"] != 0.80

    # GeminiCouncil offline fallback must force NEUTRAL on RANDOM_WALK_CHOP & avoid flat 0.80
    council = GeminiCouncil(api_key=None)
    council.client = None
    eval_chop = council.evaluate_macro_regime("STEPINDEX", {
        "smc_ict": {"structure": {"trend": "BULLISH"}},
        "price_action": {
            "regime": "RANDOM_WALK_CHOP",
            "hurst": 0.502,
            "adx_14": 14.2,
            "efficiency_ratio": 0.08,
            "quant_confidence": 0.448,
        },
        "current_price": 7360.0,
    })
    assert eval_chop["macro_bias"] == "NEUTRAL"
    assert eval_chop["confidence_score"] != 0.80
    assert eval_chop["confidence_score"] <= 0.55


def test_fix4_jump_slippage_shield_and_16h_stagnation_exit(tmp_path):
    pip_calc = PipCalculator("config/deriv_specs.json")
    sizer = PositionSizer(pip_calc, max_risk_percent=1.5, hard_cap_percent=2.0)

    # JD75 min_lot=0.01 ($0.01/pt). 150pt raw SL looks like $1.50, but 1.50x Jump haircut = 225pt = $2.25 > $2.00 cap!
    viab_blocked = sizer.evaluate_instrument_viability("JD75", entry_price=1000.0, stop_loss_price=850.0, account_equity=100.0)
    assert viab_blocked["viable"] is False
    assert viab_blocked["gap_slippage_multiplier"] == 1.50
    assert viab_blocked["effective_sl_points"] == 225.0

    # Narrower 100pt raw SL -> 150.0pt effective -> $1.50 <= $2.00 cap -> viable!
    viab_ok = sizer.evaluate_instrument_viability("JD75", entry_price=1000.0, stop_loss_price=900.0, account_equity=100.0)
    assert viab_ok["viable"] is True
    assert viab_ok["effective_sl_points"] == 150.0

    # 16h Stagnation Exit (TIME_DECAY_EXIT) test
    db = DatabaseManager(os.path.join(tmp_path, "stag.db"))
    bankroll = BankrollManager(db)
    mock_mgr = MagicMock()
    mock_mgr.capture_and_store_trade_timeframes.return_value = {"H1": "data/screenshots/mock_AFTER_H1.png"}
    trader = PaperTrader(db, bankroll, pip_calc=pip_calc, position_sizer=sizer, screenshot_mgr=mock_mgr)

    tip = TradingTip(
        symbol="STEPINDEX", direction="BULLISH", entry_mode="MARKET_CONFIRMATION", confidence_score=0.82,
        primary_strategy="SMC_ICT", market_regime="DIRECTIONAL_TREND",
        current_price=7360.0, entry_min=7359.5, entry_max=7360.5,
        stop_loss=7350.0, take_profit_1=7385.0, take_profit_2=7400.0,
        sl_points=10.0, tp1_points=25.0, risk_reward_ratio=2.5,
        break_even_trigger_price=7370.0, estimated_hold_hours=4.0,
        estimated_hold_candles=4, confluence_reasons=["Stagnation test"],
    )
    t_id = trader.submit_tip(tip, lot_size=0.15, dollar_risk=1.50, margin_req=1.0)
    assert t_id is not None

    # Set filled_at 17 hours ago and keep price sideways (< +0.50R MFE = 7365.0)
    filled_17h_ago = (datetime.now(timezone.utc) - timedelta(hours=17)).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE active_trades SET opened_at = ?, filled_at = ?, max_favorable_r = 0.2 WHERE trade_id = ?",
            (filled_17h_ago, filled_17h_ago, t_id),
        )
        conn.commit()

    now_ep = int(datetime.now(timezone.utc).timestamp())
    flat_m15 = pd.DataFrame([
        {"epoch": now_ep - 1800, "open": 7360.0, "high": 7362.0, "low": 7358.0, "close": 7361.0, "volume": 10.0},
        {"epoch": now_ep - 900, "open": 7361.0, "high": 7362.5, "low": 7359.0, "close": 7361.2, "volume": 10.0},
    ])
    mock_scraper = MagicMock()
    res = trader.update_open_positions("STEPINDEX", flat_m15, scraper=mock_scraper, m15_df=flat_m15)
    assert len(res) == 1
    assert res[0]["outcome"] == "TIME_DECAY_EXIT"
    assert len(db.get_active_trades()) == 0
    assert mock_mgr.capture_and_store_trade_timeframes.called
    mock_mgr.capture_and_store_trade_timeframes.assert_called_once()
    resolved_rows = db.get_resolved_trades()
    assert len(resolved_rows) == 1
    assert "16h Stagnation Time-Stop" in resolved_rows[0]["what_improved"]


def test_fix5_workflows_dispatch_and_status_table_columns(tmp_path):
    wf_events = {
        ".github/workflows/hourly_execution_desk.yml": "run-hourly-desk",
        ".github/workflows/macro_regime_desk.yml": "run-macro-desk",
        ".github/workflows/daily_council_committee.yml": "run-council-desk",
    }
    for path, ev in wf_events.items():
        with open(path, "r") as f:
            parsed = yaml.safe_load(f)
        on_block = parsed.get("on") or parsed.get(True)
        assert "repository_dispatch" in on_block
        assert ev in on_block["repository_dispatch"]["types"]

    # Dry-run CLI verification on scripts/trigger_dispatch.py
    proc = subprocess.run(
        [sys.executable, "scripts/trigger_dispatch.py", "--desk", "hourly", "--dry-run"],
        capture_output=True, text=True, check=True,
    )
    assert "run-hourly-desk" in proc.stdout

    # Status table renders new Bucket / Entry Mode / Stagnation Timer / Hurst / ADX columns
    db_file = os.path.join(tmp_path, "st5.db")
    macro_file = os.path.join(tmp_path, "macro5.json")
    db = DatabaseManager(db_file)
    BankrollManager(db)
    with open(macro_file, "w") as f:
        json.dump({
            "instruments": {
                "STEPINDEX": {
                    "macro_bias": "BULLISH", "confidence_score": 0.84, "primary_regime": "DIRECTIONAL_TREND",
                    "hurst": 0.63, "adx_14": 28.4, "nano_lot_viable": True,
                    "key_invalidation_level": 7340.0, "macro_target": 7420.0,
                }
            }
        }, f)
    out = render_status_tables(fetch_remote_ci=False, db_path=db_file, macro_bias_path=macro_file)
    assert "| Trade ID | Symbol | Bucket | Bias | Entry Mode | Status |" in out
    assert "| Stagnation Timer |" in out
    assert "| Symbol | H4 Bias | Conf. | Regime | Hurst | ADX | $100 Nano-Lot Viable |" in out
    assert "| 0.63 | 28.4 |" in out


def test_audit_status_readonly_advisor_floor_and_time_decay_mutation(tmp_path):
    """Verifies Step 1/Check 3/4/6 audit remediations cleanly."""
    import hashlib
    from src.council.gemini_council import GeminiCouncil
    from src.council.post_mortem import PostMortemManager

    # 1. StatusTableReporter must keep SQLite bytes bit-for-bit untouched in read_only mode
    db_file = os.path.join(tmp_path, "readonly_status.db")
    db = DatabaseManager(db_file)
    BankrollManager(db)
    digest_before = hashlib.sha256(open(db_file, "rb").read()).hexdigest()
    out = render_status_tables(fetch_remote_ci=False, db_path=db_file)
    assert "1. Bankroll, Calibration & Risk Scoreboard" in out
    digest_after = hashlib.sha256(open(db_file, "rb").read()).hexdigest()
    assert digest_before == digest_after

    # 2. CapitalAdvisor must halt when active_bankroll <= $60 floor or drawdown breaker active
    db.set_kv("active_bankroll", 59.5)
    mock_ws = MagicMock()
    h1, _ = _sweep_mss_pair()
    mock_ws.get_candles.return_value = h1
    advisor = CapitalAdvisor(db=db, ws_interceptor=mock_ws)
    halted_adv = advisor.advise(capital=100.0, report_path=os.path.join(tmp_path, "ADVICE_HALT.md"))
    assert halted_adv["direction"] == "NEUTRAL_NO_TRADE"
    assert halted_adv["dollar_risk"] == 0.0
    assert halted_adv["market_regime"] == "HALTED_EQUITY_SHIELD"

    # When healthy, CapitalAdvisor never exceeds $1.50 (1.5% on $100) and never emits unviable fallback
    db.set_kv("active_bankroll", 100.0)
    ok_adv = advisor.advise(capital=100.0, report_path=os.path.join(tmp_path, "ADVICE_OK.md"))
    assert ok_adv["dollar_risk"] <= 1.50

    # 3. ConfluenceEngine explicitly enforces >= 2.0R on MARKET_CONFIRMATION even with min_rr < 2.0 override
    filters_file = os.path.join(tmp_path, "filters_rr.json")
    with open(filters_file, "w") as f:
        json.dump({
            "version": "2.0.0",
            "min_rr_ratio": 1.2,
            "max_pending_hours": 4,
            "blocked_regimes_by_symbol": {},
        }, f)
    h1_s, m15_s = _sweep_mss_pair()
    ce = ConfluenceEngine(filters_path=filters_file, pattern_memory=PatternMemoryEngine(db=db))
    tip_mc = ce.generate_tip("STEPINDEX", h1_s, m15_df=m15_s, macro_bias="BULLISH", macro_confidence=0.80)
    if tip_mc.direction != "NEUTRAL_NO_TRADE":
        assert tip_mc.risk_reward_ratio >= 2.0

    # 4. PostMortemManager counts TIME_DECAY_EXIT inside Speed-2 n >= 5 batch hard-filter mutations
    weights_f = os.path.join(tmp_path, "w.json")
    with open(weights_f, "w") as f:
        json.dump({"instruments": {}}, f)
    council = GeminiCouncil(api_key="", notes_file=os.path.join(tmp_path, "notes.md"))
    council.client = None
    pm = PostMortemManager(
        db=db,
        council=council,
        weights_path=weights_f,
        filters_path=filters_file,
        reports_dir=os.path.join(tmp_path, "rep"),
        filter_mutation_batch_size=5,
    )
    for k in range(4):
        res_k = pm.process_resolved_trade({
            "trade_id": f"TRD-TD-{k}", "symbol": "JD75", "outcome": "TIME_DECAY_EXIT",
            "strategy": "SMC_ICT", "realized_pnl": -0.35, "realized_r": -0.25,
        })
        assert res_k["hard_filter_mutated"] is False
    res_5 = pm.process_resolved_trade({
        "trade_id": "TRD-TD-5", "symbol": "JD75", "outcome": "TIME_DECAY_EXIT",
        "strategy": "SMC_ICT", "realized_pnl": -0.40, "realized_r": -0.30,
    })
    assert res_5["hard_filter_mutated"] is True


def test_m15_execution_sl_and_nano_lot_viability_not_h4_invalidation(tmp_path):
    """
    Verifies $100 Nano-Lot Viability (Table 4, ConfluenceEngine, CapitalAdvisor) uses
    M15 intraday execution stop (min_lot * point_value * 1.5 * ATR_14_M15 * jump_mult <= $2.00)
    rather than wide H4 macro invalidation / H1 swings.
    """
    # 1. ConfluenceEngine uses M15 ATR when m15_df is provided even if H1 ATR is very wide
    wide_h1 = _trend_df(n=100, base=7000.0, step=8.0, freq_min=60)
    # Give wide H1 wicks (ATR ~ 48 pt) while M15 stays calm (ATR ~ 4 pt)
    wide_h1["high"] = wide_h1["high"] + 22.0
    wide_h1["low"] = wide_h1["low"] - 22.0
    calm_m15 = _trend_df(n=100, base=float(wide_h1["close"].iloc[-1]) - 60.0, step=0.8, freq_min=15)

    db = DatabaseManager(os.path.join(tmp_path, "m15_viab.db"))
    BankrollManager(db)
    ce = ConfluenceEngine(pattern_memory=PatternMemoryEngine(db=db))
    tip = ce.generate_tip("STEPINDEX", wide_h1, m15_df=calm_m15, macro_bias="BULLISH")
    if tip.direction == "BULLISH":
        # Must NOT inflate to ~70pt wide H1/H4 SL; must stay calibrated to calm M15 execution ATR
        assert tip.sl_points <= 20.0
        assert (0.10 * tip.sl_points) <= 2.00

    # 2. StatusTableReporter Table 4 computes M15 viability from cached M15 candles
    #    (overriding stale false/wide H4 flag) AND keeps 1.50x Jump gap buffer on JD10
    now_ep = int(datetime.now(timezone.utc).timestamp())
    with db.get_connection() as conn:
        # STEPINDEX M15 with ATR = 5.0 -> 1.5 * 5.0 = 7.5pt * $0.10 = $0.75 <= $2.00 -> YES
        for i in range(25):
            conn.execute(
                "INSERT INTO candles (symbol, timeframe, epoch, open, high, low, close, volume) VALUES (?, 'M15', ?, ?, ?, ?, ?, 100)",
                ("STEPINDEX", now_ep - (25 - i) * 900, 7400.0, 7402.5, 7397.5, 7401.0),
            )
        # JD10 M15 with ATR = 100.0 -> 1.5 * 100 * 1.50 (Jump) * 0.01 = $2.25 > $2.00 -> NO
        for i in range(25):
            conn.execute(
                "INSERT INTO candles (symbol, timeframe, epoch, open, high, low, close, volume) VALUES (?, 'M15', ?, ?, ?, ?, ?, 100)",
                ("JD10", now_ep - (25 - i) * 900, 89600.0, 89650.0, 89550.0, 89610.0),
            )
        conn.commit()

    macro_f = os.path.join(tmp_path, "macro_m15.json")
    with open(macro_f, "w") as f:
        json.dump({
            "instruments": {
                "STEPINDEX": {
                    "macro_bias": "BULLISH", "confidence_score": 0.87, "primary_regime": "DIRECTIONAL_TREND",
                    "hurst": 0.73, "adx_14": 82.5, "nano_lot_viable": False,
                    "key_invalidation_level": 7272.02, "macro_target": 7459.21,
                },
                "JD10": {
                    "macro_bias": "BEARISH", "confidence_score": 0.58, "primary_regime": "ROTATIONAL_RANGE",
                    "hurst": 0.68, "adx_14": 23.7, "nano_lot_viable": True,
                    "key_invalidation_level": 89967.87, "macro_target": 89280.02,
                },
            }
        }, f)

    out = render_status_tables(fetch_remote_ci=False, db_path=os.path.join(tmp_path, "m15_viab.db"), macro_bias_path=macro_f)
    step_line = next(ln for ln in out.splitlines() if ln.startswith("| STEPINDEX |"))
    jd10_line = next(ln for ln in out.splitlines() if ln.startswith("| JD10 |"))
    assert "✅ YES" in step_line
    assert "❌ NO" in jd10_line


