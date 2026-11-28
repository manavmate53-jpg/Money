#!/usr/bin/env python3
"""
Autonomous Deriv Synthetic Indices Trading Bot & Hedge-Fund Multi-Cadence System.
Unified CLI supporting:
- --cycle hourly   (Execution & Risk Desk: H1/M15 candles, 6h TTL expiry, viability filter, LATEST_ADVICE.md)
- --cycle macro    (Macro & Regime Desk: TradingView H1 visual + live WS telemetry, writes config/macro_bias.json)
- --cycle council  (Investment Council Desk: 24h post-mortem, active_filters.json, Vault sweep, TRAINING_DASHBOARD.md)
- --cycle advise   (Personalized Capital Advisor ticket & LATEST_ADVICE.md refresh)
"""

import sys
import os
import argparse
import time
import json
import logging
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markup import escape

# Configure clean logging so Gemini/WebSocket/Playwright diagnostics are always visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database.db_manager import DatabaseManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.risk.bankroll_manager import BankrollManager, get_asset_bucket
from src.technicals.confluence_engine import ConfluenceEngine
from src.technicals.smc_ict import SmcIctEngine
from src.technicals.supply_demand import SupplyDemandEngine
from src.technicals.price_action import PriceActionEngine
from src.technicals.liquidity_engine import LiquidityEngine
from src.technicals.poi_price_action import PoiPriceActionEngine
from src.technicals.pattern_memory import PatternMemoryEngine
from src.browser.deriv_scraper import DerivScraper
from src.browser.ws_interceptor import DerivWsInterceptor
from src.simulator.paper_trader import PaperTrader
from src.council.gemini_council import GeminiCouncil
from src.council.post_mortem import PostMortemManager
from src.council.readiness_gate import ReadinessGate
from src.advisor.capital_advisor import CapitalAdvisor

console = Console()


def load_environment():
    """Loads environment variables from .env file if present."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() not in os.environ or not os.environ[k.strip()]:
                        os.environ[k.strip()] = v.strip().strip("'\"")


def cycle_hourly(dry_run: bool = False, capital: float = 100.0) -> dict:
    """
    Cadence 1: Execution & Risk Desk (Every 1 Hour - 0 * * * * UTC)
    - Parses charts + live WebSocket OHLCV (H1 & M15).
    - Expires stale PENDING_ENTRY orders (>= 6h TTL or pre-entry SL invalidation).
    - Resolves open paper trades against intrabar price paths and ratchets SL to Break-Even (+1.0R).
    - Aligns precision entries strictly with config/macro_bias.json & small-account viability gates.
    - Regenerates reports/LATEST_ADVICE.md.
    """
    console.print("[bold cyan]══════ EXECUTION & RISK DESK (1-HOUR CYCLE) ══════[/bold cyan]")
    load_environment()
    db = DatabaseManager()
    pip_calc = PipCalculator()
    bankroll = BankrollManager(db)
    gate = ReadinessGate(db)
    pa = PriceActionEngine()
    position_sizer = PositionSizer(pip_calc)
    pattern_memory = PatternMemoryEngine(db=db)
    confluence = ConfluenceEngine(pattern_memory=pattern_memory)
    paper_trader = PaperTrader(db, bankroll, pip_calc, position_sizer=position_sizer, readiness_gate=gate)
    post_mortem = PostMortemManager(db, pattern_memory=pattern_memory)
    scraper = DerivScraper()
    ws = DerivWsInterceptor(db)
    advisor = CapitalAdvisor(db)
    council = GeminiCouncil()

    macro_bias_file = "config/macro_bias.json"
    macro_data = {}
    if os.path.exists(macro_bias_file):
        try:
            with open(macro_bias_file, "r") as f:
                macro_data = json.load(f).get("instruments", {})
        except Exception:
            macro_data = {}

    symbols = ["STEPINDEX", "JD75", "JD100", "1HZ25V", "1HZ50V", "1HZ10V", "1HZ75V", "R_75"]
    results = {"resolved_trades": 0, "expired_trades": 0, "new_trades": 0}

    # First resolve/expire ALL open positions across all active symbols BEFORE scanning new entries
    active_trades_all = db.get_active_trades()
    active_symbols = {t["symbol"] for t in active_trades_all}
    for act_sym in sorted(active_symbols):
        sym_trades = [t for t in active_trades_all if t["symbol"] == act_sym]
        max_age_h = 8.0
        now_utc = datetime.now(timezone.utc)
        for st in sym_trades:
            try:
                odt = datetime.fromisoformat(st["opened_at"])
                if odt.tzinfo is None:
                    odt = odt.replace(tzinfo=timezone.utc)
                max_age_h = max(max_age_h, (now_utc - odt).total_seconds() / 3600.0 + 2.0)
            except Exception:
                pass
        m15_bars_needed = min(1000, max(150, int(max_age_h * 4) + 16))
        df_act = ws.get_candles(act_sym, timeframe="H1", count=150)
        df_act_m15 = ws.get_candles(act_sym, timeframe="M15", count=m15_bars_needed)
        resolved_act = paper_trader.update_open_positions(act_sym, df_act, scraper=scraper, m15_df=df_act_m15)
        for res in resolved_act:
            if res["outcome"] == "EXPIRED_STALE":
                results["expired_trades"] += 1
                console.print(f"[yellow]⏱ Expired Pending Order {res['trade_id']} on {act_sym}: {escape(res.get('reason', ''))}[/yellow]")
            else:
                results["resolved_trades"] += 1
                console.print(f"[bold green]✓ Resolved Trade on {act_sym}: {res['outcome']} (${res['realized_pnl']:+.2f} / {res['realized_r']:+.2f}R)[/bold green]")
                post_mortem.process_resolved_trade(
                    res,
                    before_screenshots=res.get("before_screenshots"),
                    after_screenshots=res.get("after_screenshots")
                )

    # Enforce 3-bucket uncorrelated paper training (<= $4.50 combined heat) during TRAINING_CALIBRATION, or 1 live slot
    equity_status = bankroll.get_status()
    is_live_ready = bool(gate.evaluate_metrics(write_report=False).get("is_live_ready", False))
    if bankroll.is_drawdown_breaker_active() or equity_status["active_bankroll"] <= bankroll.get_equity_floor():
        console.print(
            f"[bold red]🛑 DRAWDOWN_CIRCUIT_BREAKER ACTIVE (Equity: ${equity_status['active_bankroll']:.2f} vs "
            f"${bankroll.get_equity_floor():.2f} floor) — New entries paused until Council review.[/bold red]"
        )
    else:
        for sym in symbols:
            cur_equity = bankroll.get_status()["active_bankroll"]
            limits = bankroll.get_paper_bucket_limits(is_live_ready=is_live_ready, capital=cur_equity)
            max_concurrent = limits["max_concurrent"]
            max_per_bucket = limits["max_per_bucket"]
            max_total_risk = limits["max_total_risk"]
            active_now = db.get_active_trades()

            if len(active_now) >= max_concurrent:
                console.print(f"  [dim cyan]Risk Shield: Max concurrent open positions ({len(active_now)}/{max_concurrent}) reached; skipping new entries.[/dim cyan]")
                break
            if any(t["symbol"] == sym for t in active_now):
                continue

            sym_bucket = get_asset_bucket(sym)
            if sum(1 for t in active_now if (t.get("asset_bucket") or get_asset_bucket(t["symbol"])) == sym_bucket) >= max_per_bucket:
                console.print(f"  [dim cyan]Bucket Cap: {sym} skipped ({sym_bucket} slot occupied).[/dim cyan]")
                continue

            current_heat = sum(float(t.get("dollar_risk", 0.0)) for t in active_now)
            if current_heat >= max_total_risk:
                console.print(f"  [dim cyan]Portfolio Heat Cap: ${current_heat:.2f} / ${max_total_risk:.2f} utilized.[/dim cyan]")
                break

            df_h1 = ws.get_candles(sym, timeframe="H1", count=150)
            df_m15 = ws.get_candles(sym, timeframe="M15", count=150)
            df_h4 = ws.get_candles(sym, timeframe="H4", count=100)
            if df_h1 is None or len(df_h1) < 30:
                continue

            h1_atr = float(pa.calculate_atr(df_h1).iloc[-1])

            # Check Macro Alignment, Anti-Revenge Cooldown, 12h Dedup & Tactical Sniper Entry
            macro_info = macro_data.get(sym, {})
            macro_bias = macro_info.get("macro_bias", "NEUTRAL")
            macro_conf = macro_info.get("confidence_score")
            hours_since_loss = db.get_hours_since_last_loss(sym)
            recent_zones = [
                {**z, "direction": "BULLISH"} for z in db.get_recent_expired_or_stopped_zones(sym, "BULLISH", hours=12.0)
            ] + [
                {**z, "direction": "BEARISH"} for z in db.get_recent_expired_or_stopped_zones(sym, "BEARISH", hours=12.0)
            ]

            tip = confluence.generate_tip(
                sym,
                df_h1,
                m15_df=df_m15,
                h4_df=df_h4,
                macro_bias=macro_bias,
                macro_confidence=macro_conf,
                hours_since_last_loss=hours_since_loss,
                recent_expired_or_stopped=recent_zones,
            )
            if tip.direction != "NEUTRAL_NO_TRADE":
                equity = bankroll.get_status()["active_bankroll"]
                entry_p = tip.current_price if tip.entry_mode == "MARKET_CONFIRMATION" else (tip.entry_max if tip.direction == "BULLISH" else tip.entry_min)
                viab = position_sizer.evaluate_instrument_viability(sym, entry_p, tip.stop_loss, equity)

                if not viab["viable"]:
                    console.print(f"  [dim yellow]Filter: {sym} disqualified for ${equity:.0f} capital: {escape(str(viab['rejection_reason']))}[/dim yellow]")
                    continue

                if (current_heat + viab["actual_dollar_risk"]) > (max_total_risk + 1e-6):
                    console.print(f"  [dim yellow]Heat Cap: {sym} risk ${viab['actual_dollar_risk']:.2f} exceeds remaining ${max_total_risk - current_heat:.2f} ceiling[/dim yellow]")
                    continue

                tactical = council.validate_tactical_sniper(sym, macro_bias, {
                    "direction": tip.direction,
                    "entry_mode": tip.entry_mode,
                    "asset_bucket": sym_bucket,
                    "confidence": tip.confidence_score,
                    "regime": tip.market_regime,
                    "entry_zone": f"{tip.entry_min}-{tip.entry_max}",
                    "sl": tip.stop_loss,
                    "tp1": tip.take_profit_1,
                })

                if tactical["approved"]:
                    scaled_lot = round(max(viab["min_lot"], viab["recommended_lot"] * tactical["adjusted_lot_multiplier"]), 3)
                    if not dry_run:
                        trade_id = paper_trader.submit_tip(
                            tip,
                            lot_size=scaled_lot,
                            dollar_risk=viab["actual_dollar_risk"],
                            margin_req=viab["margin_required"],
                            scraper=scraper,
                            h1_atr=h1_atr,
                        )
                        if trade_id:
                            results["new_trades"] += 1
                            console.print(f"  [bold green]★ Sniper Entry ({tip.entry_mode} / {sym_bucket}) Placed ({trade_id}): {sym} {tip.direction} | Lot: {scaled_lot} | Risk: ${viab['actual_dollar_risk']:.2f}[/bold green]")
                    else:
                        console.print(f"  [yellow]★ DRY-RUN Sniper ({tip.entry_mode}): {sym} {tip.direction} | Lot: {scaled_lot}[/yellow]")

    # Refresh reports/LATEST_ADVICE.md
    advice_res = advisor.advise(capital=capital)
    console.print(f"[bold green]✓ Updated reports/LATEST_ADVICE.md (#1 Pick: {advice_res['recommended_instrument']})[/bold green]")
    return results


def cycle_macro(symbols_list: list = None) -> dict:
    """
    Cadence 2: Macro & Regime Desk (Every 4 Hours - 0 */4 * * * UTC)
    - Captures symbol-accurate TradingView H1 PNGs + live OHLCV across VIX, Step & Jump Indices.
    - Computes Pure Price Action, PrimeFX Liquidity Pools (EQH/EQL/PDH/PDL), and H4/H1 POIs.
    - Screens instruments via Gemini Multimodal Vision to publish config/macro_bias.json.
    """
    console.print("[bold magenta]══════ MACRO & REGIME DESK (4-HOUR CYCLE) ══════[/bold magenta]")
    load_environment()
    db = DatabaseManager()
    scraper = DerivScraper()
    council = GeminiCouncil()
    smc = SmcIctEngine()
    pa = PriceActionEngine()
    liq_engine = LiquidityEngine()
    poi_engine = PoiPriceActionEngine()

    symbols = symbols_list or [
        "STEPINDEX", "JD75", "JD100", "1HZ25V", "1HZ50V", "1HZ75V",
        "1HZ10V", "1HZ100V", "JD10", "JD25", "JD50", "R_75", "R_100", "BOOM1000", "CRASH1000"
    ]
    macro_map = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "Macro & Regime Desk (Gemini Multimodal Vision + PrimeFX Pure Liquidity/POI Telemetry)",
        "instruments": {}
    }

    for idx, sym in enumerate(symbols):
        if idx > 0:
            time.sleep(1.5)

        console.print(f"[cyan]Scanning Macro Structure for {sym}...[/cyan]")
        h1_data = scraper.capture_chart(sym, timeframe="H1", headless=True)
        df_h1 = h1_data["candles_df"]
        df_m15 = scraper.ws_interceptor.get_candles(sym, timeframe="M15", count=100)
        df_h4 = scraper.ws_interceptor.get_candles(sym, timeframe="H4", count=100)
        screenshot = h1_data["screenshot_path"]

        smc_res = smc.analyze(df_h1)
        pa_res = pa.analyze(df_h1)
        pa_h4 = pa.analyze(df_h4) if (df_h4 is not None and len(df_h4) >= 30) else pa_res
        reg_m = pa_h4.get("regime_metrics", pa_res.get("regime_metrics", {}))
        liq_res = liq_engine.analyze(df_h1, df_h4=df_h4)
        poi_res = poi_engine.analyze(df_h1, df_h4=df_h4)

        curr_p = float(df_h1["close"].iloc[-1])
        m15_atr_14 = (
            float(pa.calculate_atr(df_m15).iloc[-1])
            if (df_m15 is not None and len(df_m15) >= 14)
            else ((pa_res["atr_14"] or 1.0) * 0.5)
        )
        # Evaluate $100 nano-lot viability strictly on M15 execution SL (1.5 * ATR_14_M15) + 1.50x Jump buffer
        sl_probe = curr_p - (1.5 * m15_atr_14)
        viable_flag, _ = PositionSizer(PipCalculator()).is_viable_for_account(sym, 100.0, curr_p, sl_probe)

        summary = {
            "current_price": curr_p,
            "ws_source": scraper.ws_interceptor.last_source,
            "chart_verified_symbol": h1_data.get("verified_symbol_match", False),
            "is_jump_index": sym.startswith("JD"),
            "smc_ict": smc_res,
            "liquidity_pools": {
                "pdh": liq_res["pools"]["pdh"],
                "pdl": liq_res["pools"]["pdl"],
                "p4hh": liq_res["pools"]["p4hh"],
                "p4hl": liq_res["pools"]["p4hl"],
                "eqh_count": len(liq_res["pools"]["eqh"]),
                "eql_count": len(liq_res["pools"]["eql"]),
                "active_sweeps": liq_res["active_sweeps"][:3],
            },
            "poi_summary": {
                "valid_pois_count": len(poi_res["valid_pois"]),
                "trap_pois_count": len(poi_res["trap_pois"]),
                "best_bullish_poi": poi_res.get("best_bullish_poi"),
                "best_bearish_poi": poi_res.get("best_bearish_poi"),
            },
            "price_action": {
                "regime": reg_m.get("regime", pa_res["regime"]),
                "regime_metrics": reg_m,
                "hurst": reg_m.get("hurst", 0.5),
                "adx_14": reg_m.get("adx_14", 0.0),
                "efficiency_ratio": reg_m.get("efficiency_ratio", 0.0),
                "quant_confidence": reg_m.get("quant_confidence", 0.55),
                "one_time_framing": pa_h4["one_time_framing"]["status"],
                "dealing_range": pa_res["dealing_range"],
                "sr_polarity_flips": pa_res["sr_polarity_flips"],
                "atr_14": pa_res["atr_14"]
            }
        }

        eval_res = council.evaluate_macro_regime(sym, summary, image_path=screenshot, model_name="gemini-2.5-flash")
        eval_res["display_name"] = sym
        eval_res["asset_bucket"] = get_asset_bucket(sym)
        eval_res["hurst"] = reg_m.get("hurst")
        eval_res["adx_14"] = reg_m.get("adx_14")
        eval_res["efficiency_ratio"] = reg_m.get("efficiency_ratio")
        eval_res["m15_atr_14"] = round(m15_atr_14, 4)
        eval_res["nano_lot_viable"] = viable_flag
        macro_map["instruments"][sym] = eval_res

        b_color = "green" if eval_res["macro_bias"] == "BULLISH" else ("red" if eval_res["macro_bias"] == "BEARISH" else "yellow")
        console.print(f"  [bold]Bias for {sym}:[/bold] [{b_color}]{eval_res['macro_bias']}[/{b_color}] ({eval_res['primary_regime']}) [{eval_res.get('ai_model', 'ai')}]")

    bias_path = "config/macro_bias.json"
    with open(bias_path, "w") as f:
        json.dump(macro_map, f, indent=2)

    console.print(f"[bold green]✓ Authoritative macro bias map published to {bias_path}[/bold green]")
    return macro_map


def cycle_council() -> dict:
    """
    Cadence 3: Investment Council & Vault Desk (Every 24 Hours - 30 23 * * * UTC)
    - Queries unified resolved trades (profitable_trades UNION ALL losing_trades) for active epoch.
    - Audits any closed trades not yet recorded in council_journal with multimodal BEFORE/AFTER PNGs.
    - Mutates strategy_weights.json & active_filters.json.
    - Always regenerates reports/TRAINING_DASHBOARD.md and profit/loss ledgers.
    """
    console.print("[bold gold1]══════ INVESTMENT COUNCIL & VAULT COMMITTEE (24-HOUR CADENCE) ══════[/bold gold1]")
    load_environment()
    db = DatabaseManager()
    bankroll = BankrollManager(db)
    gate = ReadinessGate(db)
    pattern_memory = PatternMemoryEngine(db=db)
    post_mortem = PostMortemManager(db, pattern_memory=pattern_memory)

    epoch_info = db.get_active_epoch_info()
    current_epoch = int(epoch_info.get("epoch_number", 1))
    resolved_trades = db.get_resolved_trades(epoch_id=current_epoch)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT trade_id FROM council_journal WHERE trade_id IS NOT NULL AND trade_id != ''")
        audited_ids = {row["trade_id"] for row in cursor.fetchall()}

    pending_audits = [t for t in resolved_trades if t["trade_id"] not in audited_ids]
    console.print(f"[cyan]Resolved Trades (Epoch #{current_epoch}): {len(resolved_trades)} | Pending Multimodal Council Audits: {len(pending_audits)}[/cyan]")

    for trade in pending_audits:
        try:
            before_map = json.loads(trade.get("before_screenshots_json") or "{}")
        except Exception:
            before_map = {}
        try:
            after_map = json.loads(trade.get("after_screenshots_json") or "{}")
        except Exception:
            after_map = {}

        review = post_mortem.process_resolved_trade({
            "trade_id": trade["trade_id"],
            "symbol": trade["symbol"],
            "outcome": trade["outcome"],
            "strategy": trade.get("strategy_framework", "SMC_ICT"),
            "realized_pnl": trade.get("net_pnl", 0.0),
            "realized_r": trade.get("rr_realized", 0.0),
            "entry_price": trade.get("entry_price"),
            "exit_price": trade.get("exit_price"),
        }, before_screenshots=before_map, after_screenshots=after_map)
        console.print(f"  Council Reflexion on {trade['trade_id']} ({trade['symbol']}): [dim]{escape(review['actionable_rule_mutation'])}[/dim]")

    # Always evaluate Graduation Gate & regenerate TRAINING_DASHBOARD.md + ledgers
    metrics = gate.evaluate_metrics(write_report=True)
    db.export_all_ledgers()
    if bankroll.is_drawdown_breaker_active() and bankroll.policy.get("sandbox_auto_reset", True):
        bankroll.clear_drawdown_breaker()
        console.print("[yellow]🛡️ Council cleared DRAWDOWN_CIRCUIT_BREAKER after post-mortem review on new sandbox epoch.[/yellow]")
    b_stat = bankroll.get_status()

    console.print(f"[bold green]✓ Graduation Gate Evaluated: {metrics['status']} ({metrics['total_trades']} resolved trades)[/bold green]")
    console.print(f"[bold gold1]✓ Vault Balance Preserved: ${b_stat['vault_balance']:.2f} (Active Bankroll: ${b_stat['active_bankroll']:.2f})[/bold gold1]")
    console.print("[bold green]✓ Updated reports/TRAINING_DASHBOARD.md[/bold green]")
    return metrics


def cmd_advise(capital: float = 100.0):
    """Personalized Capital Advisor CLI Blueprint."""
    load_environment()
    db = DatabaseManager()
    advisor = CapitalAdvisor(db)
    result = advisor.advise(capital=capital)

    readiness = result["readiness"]
    rec_sym = result["recommended_instrument"]
    direction = result["direction"]
    lot = result["recommended_lot_size"]
    risk = result["dollar_risk"]
    risk_pct = result["risk_percent"]
    margin = result["margin_required"]
    entry = result["entry_zone"]
    sl = result["stop_loss"]
    tp1 = result["take_profit_1"]
    tp2 = result["take_profit_2"]
    rr = result["risk_reward_ratio"]
    be_trig = result["break_even_trigger"]
    hold = result["hold_time_estimate"]
    reasons = result["confluence_reasons"]
    rule = result["champions_rule"]
    note = result["council_edge_note"]

    dir_color = "green" if direction == "BULLISH" else ("red" if direction == "BEARISH" else "yellow")
    header = f"[bold green]DERIV CAPITAL ADVISOR — ${capital:.0f} ACCOUNT BLUEPRINT[/bold green]"
    calib_text = f"Status: [bold yellow]{readiness['status']}[/bold yellow] | Win Rate: {readiness['win_rate']*100:.1f}% | Profit Factor: {readiness['profit_factor']:.2f} | Vaulted Profit: ${readiness['total_vaulted_profit']:.2f}"

    body = f"""
[bold cyan]1. CALIBRATION & VAULT STATUS[/bold cyan]
{calib_text}

[bold cyan]2. #1 RECOMMENDED INSTRUMENT[/bold cyan]
[bold white]Instrument:[/bold white] [bold yellow]{rec_sym}[/bold yellow] (Ranked #1 for ${capital:.0f} Capital)
[bold white]Directional Bias:[/bold white] [bold {dir_color}]{direction}[/bold {dir_color}]
[bold white]Strategy Attribution:[/bold white] {result['primary_strategy']} ({result['market_regime']})
[bold white]Champion Rule Alignment:[/bold white] {escape(rule or 'PrimeFX Pure Price-Action + Liquidity Playbook')}

[bold cyan]3. EXACT EXECUTION NUMBERS[/bold cyan]
• [bold white]Recommended Lot Size:[/bold white] [bold green]{lot} lots[/bold green] (Exact Dollar Risk: [bold red]${risk:.2f}[/bold red] / {risk_pct}%)
• [bold white]Required Margin:[/bold white] ${margin:.2f} (Leverage 1:1000)
• [bold white]Entry Zone:[/bold white] {entry}
• [bold white]Stop Loss (SL):[/bold white] [bold red]{sl}[/bold red] ({result['sl_points']} points)
• [bold white]Take Profit 1 (TP1):[/bold white] [bold green]{tp1}[/bold green] (Planned R:R: {rr})
• [bold white]Take Profit 2 (TP2):[/bold white] [bold green]{tp2}[/bold green] (HTF Liquidity Target)
• [bold white]Cover-Risk-to-Zero Trigger:[/bold white] Ratchet SL to Break-Even at [bold cyan]{be_trig}[/bold cyan] (+1.0R)
• [bold white]Estimated Hold Duration:[/bold white] {hold}

[bold cyan]4. CONFLUENCE SIGNALS[/bold cyan]
"""
    for r in reasons:
        body += f"  • {escape(r)}\n"

    body += f"""
[bold cyan]5. GEMINI COUNCIL WARNING / EDGE NOTE[/bold cyan]
[italic dim]{escape(note)}[/italic dim]
"""

    if result.get("disqualified_instruments"):
        body += f"\n[bold cyan]6. WHY OTHER INDICES WERE DISQUALIFIED ON ${capital:.0f} CAPITAL[/bold cyan]\n"
        for dis in result["disqualified_instruments"]:
            body += f"  • [red]{dis['symbol']}[/red]: {escape(str(dis['reason']))}\n"

    panel = Panel(body.strip(), title=header, border_style="green", expand=False)
    console.print(panel)


def cmd_status(fetch_remote_ci: bool = True):
    """Prints the structured 5-table Markdown status report."""
    from src.reporting.status_table import render_status_tables

    print(render_status_tables(fetch_remote_ci=fetch_remote_ci))


def main():
    parser = argparse.ArgumentParser(description="Autonomous Deriv Trading Bot & Hedge-Fund Multi-Cadence System")
    parser.add_argument("--cycle", choices=["hourly", "macro", "council", "advise", "status", "full_scan"], default=None,
                        help="Execute a specific hedge-fund cadence desk")
    parser.add_argument("--capital", type=float, default=100.0, help="Account capital in USD (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Execute without submitting virtual orders")
    parser.add_argument("--symbol", type=str, default=None, help="Filter to a specific instrument")

    subparsers = parser.add_subparsers(dest="command", help="Legacy subcommands")
    p_run = subparsers.add_parser("run-once", help="Run a single scan cycle")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--symbol", type=str, default=None)

    p_adv = subparsers.add_parser("advise", help="Get capital advice")
    p_adv.add_argument("--capital", type=float, default=100.0)

    subparsers.add_parser("status", help="Show system dashboard")
    subparsers.add_parser("report", help="Generate performance report")

    args = parser.parse_args()

    if args.cycle:
        if args.cycle == "hourly":
            cycle_hourly(dry_run=args.dry_run, capital=args.capital)
        elif args.cycle == "macro":
            cycle_macro([args.symbol] if args.symbol else None)
        elif args.cycle == "council":
            cycle_council()
        elif args.cycle == "advise":
            cmd_advise(args.capital)
        elif args.cycle == "status":
            cmd_status()
        elif args.cycle == "full_scan":
            cycle_macro()
            cycle_hourly(dry_run=args.dry_run, capital=args.capital)
            cycle_council()
        return

    if args.command == "run-once":
        cycle_hourly(dry_run=args.dry_run)
    elif args.command == "advise":
        cmd_advise(args.capital)
    elif args.command == "status":
        cmd_status()
    elif args.command == "report":
        cmd_status()
    else:
        cmd_advise(100.0)


if __name__ == "__main__":
    main()
