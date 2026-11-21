#!/usr/bin/env python3
"""
Autonomous Deriv Synthetic Indices Trading Bot & Hedge-Fund Multi-Cadence System.
Unified CLI supporting:
- --cycle hourly   (Execution & Risk Desk: M15/H1 candle check, trade trailing, sniper execution, LATEST_ADVICE.md)
- --cycle macro    (Macro & Regime Desk: H4/H1/M15 vision, technical scoring, writes config/macro_bias.json)
- --cycle council  (Investment Council Desk: 24h post-mortem, Bayesian weight mutation, Vault sweep, TRAINING_DASHBOARD.md)
- --cycle advise   (Personalized Capital Advisor ticket & LATEST_ADVICE.md refresh)
"""

import sys
import os
import argparse
import time
import json
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markup import escape

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database.db_manager import DatabaseManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.risk.bankroll_manager import BankrollManager
from src.technicals.confluence_engine import ConfluenceEngine
from src.technicals.smc_ict import SmcIctEngine
from src.technicals.supply_demand import SupplyDemandEngine
from src.technicals.price_action import PriceActionEngine
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
                    os.environ[k.strip()] = v.strip().strip("'\"")


def cycle_hourly(dry_run: bool = False, capital: float = 100.0) -> dict:
    """
    Cadence 1: Execution & Risk Desk (Every 1 Hour - 0 * * * * UTC)
    - Parses charts + WebSocket OHLCV (M15/H1).
    - Resolves open paper trades against intrabar price paths.
    - Moves SL to Break-Even (0R) at +1.0R unrealized profit.
    - Aligns precision entries with config/macro_bias.json.
    - Regenerates reports/LATEST_ADVICE.md.
    """
    console.print("[bold cyan]══════ EXECUTION & RISK DESK (1-HOUR CYCLE) ══════[/bold cyan]")
    load_environment()
    db = DatabaseManager()
    pip_calc = PipCalculator()
    bankroll = BankrollManager(db)
    position_sizer = PositionSizer(pip_calc)
    confluence = ConfluenceEngine()
    paper_trader = PaperTrader(db, bankroll, pip_calc)
    post_mortem = PostMortemManager(db)
    scraper = DerivScraper()
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

    symbols = ["1HZ25V", "1HZ50V", "1HZ75V", "STEPINDEX", "1HZ10V", "R_75"]
    results = {"resolved_trades": 0, "new_trades": 0, "active_positions": 0}

    for sym in symbols:
        # Capture current chart & candles
        chart_data = scraper.capture_chart(sym, timeframe="H1", headless=True)
        df = chart_data["candles_df"]
        screenshot = chart_data["screenshot_path"]

        # 1. Resolve open trades (check entry fill, +1.0R break-even, TP/SL)
        resolved = paper_trader.update_open_positions(sym, df, scraper=scraper)
        for res in resolved:
            results["resolved_trades"] += 1
            console.print(f"[bold green]✓ Resolved Trade on {sym}: {res['outcome']} (${res['realized_pnl']:+.2f} / {res['realized_r']:+.2f}R)[/bold green]")
            post_mortem.process_resolved_trade(
                res,
                screenshot_path=screenshot,
                before_screenshots=res.get("before_screenshots"),
                after_screenshots=res.get("after_screenshots")
            )

        # 2. Check Macro Alignment & Tactical Sniper Entry
        macro_info = macro_data.get(sym, {})
        macro_bias = macro_info.get("macro_bias", "NEUTRAL")

        tip = confluence.generate_tip(sym, df)
        if tip.direction != "NEUTRAL_NO_TRADE":
            # Tactical validation
            tactical = council.validate_tactical_sniper(sym, macro_bias, {
                "direction": tip.direction,
                "confidence": tip.confidence_score,
                "regime": tip.market_regime
            })

            if tactical["approved"]:
                equity = bankroll.get_status()["active_bankroll"]
                entry_p = tip.entry_max if tip.direction == "BULLISH" else tip.entry_min
                viab = position_sizer.evaluate_instrument_viability(sym, entry_p, tip.stop_loss, equity)

                if viab["viable"]:
                    scaled_lot = round(max(viab["min_lot"], viab["recommended_lot"] * tactical["adjusted_lot_multiplier"]), 3)
                    if not dry_run:
                        trade_id = paper_trader.submit_tip(
                            tip,
                            lot_size=scaled_lot,
                            dollar_risk=viab["actual_dollar_risk"],
                            margin_req=viab["margin_required"],
                            scraper=scraper
                        )
                        results["new_trades"] += 1
                        console.print(f"  [bold green]★ Sniper Entry Placed: {sym} {tip.direction} | Lot: {scaled_lot} | Risk: ${viab['actual_dollar_risk']:.2f}[/bold green]")
                    else:
                        console.print(f"  [yellow]★ DRY-RUN Sniper: {sym} {tip.direction} | Lot: {scaled_lot}[/yellow]")
                else:
                    console.print(f"  [dim yellow]Filter: {sym} disqualified for ${equity:.0f} capital: {viab['rejection_reason']}[/dim yellow]")

    # 3. Refresh reports/LATEST_ADVICE.md
    advice_res = advisor.advise(capital=capital)
    console.print(f"[bold green]✓ Updated reports/LATEST_ADVICE.md (#1 Pick: {advice_res['recommended_instrument']})[/bold green]")
    return results


def cycle_macro(symbols_list: list = None) -> dict:
    """
    Cadence 2: Macro & Regime Desk (Every 4 Hours - 0 */4 * * * UTC)
    - Captures high-res H4/H1/M15 screenshots + OHLCV across all Deriv indices.
    - Computes SMC, S&D, One-Time Framing, and Value Area metrics.
    - Uses gemini-2.5-pro with multimodal vision to write config/macro_bias.json.
    """
    console.print("[bold magenta]══════ MACRO & REGIME DESK (4-HOUR CYCLE) ══════[/bold magenta]")
    load_environment()
    db = DatabaseManager()
    scraper = DerivScraper()
    council = GeminiCouncil()
    smc = SmcIctEngine()
    sd = SupplyDemandEngine()
    pa = PriceActionEngine()

    symbols = symbols_list or ["1HZ25V", "1HZ50V", "1HZ75V", "STEPINDEX", "1HZ10V", "R_75", "R_100", "BOOM1000", "CRASH1000"]
    macro_map = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": "Macro & Regime Desk (Gemini 2.5 Pro Vision + Quantitative Structure)",
        "instruments": {}
    }

    for sym in symbols:
        console.print(f"[cyan]Scanning Macro Structure for {sym}...[/cyan]")
        h1_data = scraper.capture_chart(sym, timeframe="H1", headless=True)
        df_h1 = h1_data["candles_df"]
        screenshot = h1_data["screenshot_path"]

        # Technical computations
        smc_res = smc.analyze(df_h1)
        sd_res = sd.analyze(df_h1)
        pa_res = pa.analyze(df_h1)

        summary = {
            "current_price": float(df_h1["close"].iloc[-1]),
            "smc_ict": smc_res,
            "supply_demand": {
                "fresh_demand_count": len(sd_res["fresh_demand_zones"]),
                "fresh_supply_count": len(sd_res["fresh_supply_zones"])
            },
            "price_action": {
                "regime": pa_res["regime"],
                "one_time_framing": pa_res["one_time_framing"]["status"],
                "value_area": pa_res["value_area"],
                "atr_14": pa_res["atr_14"]
            }
        }

        eval_res = council.evaluate_macro_regime(sym, summary, image_path=screenshot)
        eval_res["display_name"] = sym
        eval_res["nano_lot_viable"] = "25" in sym or "50" in sym or "STEP" in sym
        macro_map["instruments"][sym] = eval_res
        console.print(f"  [bold]Bias for {sym}:[/bold] [{ 'green' if eval_res['macro_bias'] == 'BULLISH' else 'red' }]{eval_res['macro_bias']}[/] ({eval_res['primary_regime']})")

    # Write config/macro_bias.json
    bias_path = "config/macro_bias.json"
    with open(bias_path, "w") as f:
        json.dump(macro_map, f, indent=2)

    console.print(f"[bold green]✓ Authoritative macro bias map published to {bias_path}[/bold green]")
    return macro_map


def cycle_council() -> dict:
    """
    Cadence 3: Investment Council & Vault Desk (Every 24 Hours - 30 23 * * * UTC)
    - Conducts post-mortems on all trades closed in the past 24 hours.
    - Mutates Bayesian expectancy weights in config/strategy_weights.json.
    - Sweeps 50% of every +20% milestone gain into the Locked Profit Vault.
    - Evaluates 30-Day Graduation Gate and updates reports/TRAINING_DASHBOARD.md.
    """
    console.print("[bold gold1]══════ INVESTMENT COUNCIL & VAULT COMMITTEE (24-HOUR CADENCE) ══════[/bold gold1]")
    load_environment()
    db = DatabaseManager()
    bankroll = BankrollManager(db)
    gate = ReadinessGate(db)
    post_mortem = PostMortemManager(db)

    # 1. Fetch trades closed in past 24h
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
        SELECT * FROM paper_trades
        WHERE status IN ('CLOSED_TP', 'CLOSED_SL')
        ORDER BY closed_at DESC LIMIT 20
        """)
        closed_trades = [dict(r) for r in cursor.fetchall()]

    console.print(f"[cyan]Auditing {len(closed_trades)} recent closed trades with Council Strategist...[/cyan]")
    for trade in closed_trades:
        review = post_mortem.process_resolved_trade({
            "trade_id": trade["trade_id"],
            "symbol": trade["symbol"],
            "outcome": "WIN" if trade["status"] == "CLOSED_TP" else "LOSS",
            "strategy": trade["strategy_primary"],
            "realized_pnl": trade["realized_pnl"],
            "realized_r": trade["realized_r"]
        }, screenshot_path=trade.get("screenshot_path"))
        console.print(f"  Council Reflexion on #{trade['trade_id']} ({trade['symbol']}): [dim]{review['actionable_rule_mutation']}[/dim]")

    # 2. Evaluate Graduation Gate and update reports/TRAINING_DASHBOARD.md
    metrics = gate.evaluate_metrics()
    b_stat = bankroll.get_status()

    console.print(f"[bold green]✓ Graduation Gate Evaluated: {metrics['status']}[/bold green]")
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

    dir_color = "green" if direction == "BULLISH" else "red"
    header = f"[bold green]DERIV CAPITAL ADVISOR — ${capital:.0f} ACCOUNT BLUEPRINT[/bold green]"
    calib_text = f"Status: [bold yellow]{readiness['status']}[/bold yellow] | Win Rate: {readiness['win_rate']*100:.1f}% | Profit Factor: {readiness['profit_factor']:.2f} | Vaulted Profit: ${readiness['total_vaulted_profit']:.2f}"

    body = f"""
[bold cyan]1. CALIBRATION & VAULT STATUS[/bold cyan]
{calib_text}

[bold cyan]2. #1 RECOMMENDED INSTRUMENT[/bold cyan]
[bold white]Instrument:[/bold white] [bold yellow]{rec_sym}[/bold yellow] (Ranked #1 for ${capital:.0f} Capital)
[bold white]Directional Bias:[/bold white] [bold {dir_color}]{direction}[/bold {dir_color}]
[bold white]Strategy Attribution:[/bold white] {result['primary_strategy']} ({result['market_regime']})
[bold white]Champion Rule Alignment:[/bold white] {escape(rule or 'Auction Value Area Absorption')}

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
        body += "\n[bold cyan]6. WHY OTHER INDICES WERE DISQUALIFIED ON $100 CAPITAL[/bold cyan]\n"
        for dis in result["disqualified_instruments"]:
            body += f"  • [red]{dis['symbol']}[/red]: {escape(str(dis['reason']))}\n"

    panel = Panel(body.strip(), title=header, border_style="green", expand=False)
    console.print(panel)


def cmd_status():
    """Displays terminal dashboard of active trades, vault balance, and readiness gate."""
    load_environment()
    db = DatabaseManager()
    bankroll = BankrollManager(db)
    gate = ReadinessGate(db)

    b_stat = bankroll.get_status()
    r_stat = gate.evaluate_metrics()

    console.print(Panel(
        f"[bold white]Active Trading Bankroll:[/bold white] [bold green]${b_stat['active_bankroll']:.2f}[/bold green] | "
        f"[bold white]Locked Profit Vault:[/bold white] [bold gold1]${b_stat['vault_balance']:.2f}[/bold gold1] | "
        f"[bold white]Total Equity:[/bold white] [bold cyan]${b_stat['total_accumulated_capital']:.2f}[/bold cyan] | "
        f"[bold white]Simulation Epoch:[/bold white] #{b_stat['epoch_number']}\n"
        f"[bold white]Calibration Status:[/bold white] [bold yellow]{r_stat['status']}[/bold yellow] | "
        f"[bold white]Win Rate:[/bold white] {r_stat['win_rate']*100:.1f}% | "
        f"[bold white]Profit Factor:[/bold white] {r_stat['profit_factor']:.2f} | "
        f"[bold white]Max Drawdown:[/bold white] {r_stat['max_drawdown_percent']:.1f}%",
        title="[bold cyan]AUTONOMOUS DERIV BOT — SYSTEM DASHBOARD[/bold cyan]",
        border_style="cyan"
    ))

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM active_trades WHERE status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')")
        trades = [dict(r) for r in cursor.fetchall()]
        if not trades:
            cursor.execute("SELECT * FROM paper_trades WHERE status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')")
            trades = [dict(r) for r in cursor.fetchall()]

    n_wins = len(db.get_profitable_trades())
    n_losses = len(db.get_losing_trades())
    console.print(f"[dim]Repository Ledgers: [green]{n_wins} Wins[/green] in reports/PROFIT_LEDGER.md | [red]{n_losses} Losses[/red] in reports/LOSS_LEDGER.md | Visuals in reports/TRADE_VISUAL_JOURNAL.md[/dim]")

    table = Table(title="Active Virtual Positions")
    table.add_column("ID", justify="right")
    table.add_column("Symbol", style="cyan")
    table.add_column("Direction", style="bold")
    table.add_column("Lot", justify="right")
    table.add_column("Entry Exec", justify="right")
    table.add_column("SL", justify="right", style="red")
    table.add_column("TP1", justify="right", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Risk ($)", justify="right")

    for t in trades:
        table.add_row(
            str(t["trade_id"]),
            t["symbol"],
            t["direction"],
            str(t["lot_size"]),
            str(t["entry_executed"] or "-"),
            str(t["stop_loss"]),
            str(t["take_profit_1"]),
            t["status"],
            f"${t['dollar_risk']:.2f}"
        )

    if trades:
        console.print(table)
    else:
        console.print("[dim italic]No active open positions.[/dim italic]")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Deriv Trading Bot & Hedge-Fund Multi-Cadence System")
    parser.add_argument("--cycle", choices=["hourly", "macro", "council", "advise", "full_scan"], default=None,
                        help="Execute a specific hedge-fund cadence desk")
    parser.add_argument("--capital", type=float, default=100.0, help="Account capital in USD (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Execute without submitting virtual orders")
    parser.add_argument("--symbol", type=str, default=None, help="Filter to a specific instrument")

    # Legacy subcommands compatibility
    subparsers = parser.add_subparsers(dest="command", help="Legacy subcommands")
    p_run = subparsers.add_parser("run-once", help="Run a single scan cycle")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument("--symbol", type=str, default=None)

    p_adv = subparsers.add_parser("advise", help="Get capital advice")
    p_adv.add_argument("--capital", type=float, default=100.0)

    subparsers.add_parser("status", help="Show system dashboard")
    subparsers.add_parser("report", help="Generate performance report")

    args = parser.parse_args()

    # Route by --cycle if provided
    if args.cycle:
        if args.cycle == "hourly":
            cycle_hourly(dry_run=args.dry_run, capital=args.capital)
        elif args.cycle == "macro":
            cycle_macro([args.symbol] if args.symbol else None)
        elif args.cycle == "council":
            cycle_council()
        elif args.cycle == "advise":
            cmd_advise(args.capital)
        elif args.cycle == "full_scan":
            cycle_macro()
            cycle_hourly(dry_run=args.dry_run, capital=args.capital)
            cycle_council()
        return

    # Route by subcommand if provided
    if args.command == "run-once":
        cycle_hourly(dry_run=args.dry_run)
    elif args.command == "advise":
        cmd_advise(args.capital)
    elif args.command == "status":
        cmd_status()
    elif args.command == "report":
        cmd_status()
    else:
        # Default behavior: advise with $100
        cmd_advise(100.0)


if __name__ == "__main__":
    main()
