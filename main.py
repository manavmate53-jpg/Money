#!/usr/bin/env python3
"""
Autonomous Deriv Synthetic Indices Trading Bot & Gemini Council Advisor.
Unified CLI: daemon (4-hr loop), run-once, advise, status, report.
"""

import sys
import os
import argparse
import time
from datetime import datetime, timezone
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markup import escape

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database.db_manager import DatabaseManager
from src.risk.pip_calculator import PipCalculator
from src.risk.position_sizer import PositionSizer
from src.risk.bankroll_manager import BankrollManager
from src.technicals.confluence_engine import ConfluenceEngine
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


def execute_cycle(dry_run: bool = False, symbol_filter: str = None) -> dict:
    """Executes a single 4-hour market scan and simulation cycle."""
    load_environment()
    db = DatabaseManager()
    pip_calc = PipCalculator()
    bankroll = BankrollManager(db)
    position_sizer = PositionSizer(pip_calc)
    confluence = ConfluenceEngine()
    paper_trader = PaperTrader(db, bankroll, pip_calc)
    post_mortem = PostMortemManager(db)
    scraper = DerivScraper()

    symbols = [symbol_filter] if symbol_filter else [
        "1HZ25V", "1HZ50V", "1HZ75V", "STEPINDEX", "1HZ10V", "R_75"
    ]

    results = {"scanned": len(symbols), "new_trades": 0, "resolved_trades": 0, "details": []}

    for sym in symbols:
        console.print(f"[bold cyan]Scanning {sym}...[/bold cyan]")
        # 1. Capture chart and fetch exact OHLCV
        chart_data = scraper.capture_chart(sym, timeframe="H1", headless=True)
        df = chart_data["candles_df"]
        screenshot_path = chart_data["screenshot_path"]

        # 2. Update existing open positions first
        resolved = paper_trader.update_open_positions(sym, df)
        for res in resolved:
            results["resolved_trades"] += 1
            console.print(f"[bold green]Resolved Trade on {sym}: {res['outcome']} (${res['realized_pnl']:+.2f})[/bold green]")
            # Run Council Post-Mortem
            post_mortem.process_resolved_trade(res, screenshot_path)

        # 3. Generate multi-framework tip
        tip = confluence.generate_tip(sym, df)
        if tip.direction != "NEUTRAL_NO_TRADE":
            # Check small-account viability
            bank_status = bankroll.get_status()
            equity = bank_status["active_bankroll"]
            entry_p = tip.entry_max if tip.direction == "BULLISH" else tip.entry_min
            viab = position_sizer.evaluate_instrument_viability(sym, entry_p, tip.stop_loss, equity)

            if viab["viable"]:
                if not dry_run:
                    trade_id = paper_trader.submit_tip(
                        tip,
                        lot_size=viab["recommended_lot"],
                        dollar_risk=viab["actual_dollar_risk"],
                        margin_req=viab["margin_required"],
                        screenshot_path=screenshot_path
                    )
                    results["new_trades"] += 1
                    console.print(f"  [green]Trade Tip Submitted: {sym} {tip.direction} | Lot: {viab['recommended_lot']} | Risk: ${viab['actual_dollar_risk']:.2f}[/green]")
                else:
                    console.print(f"  [yellow]DRY-RUN Tip: {sym} {tip.direction} | Lot: {viab['recommended_lot']} | Risk: ${viab['actual_dollar_risk']:.2f}[/yellow]")
            else:
                console.print(f"  [dim yellow]Disqualified {sym}: {viab['rejection_reason']}[/dim yellow]")

        results["details"].append({"symbol": sym, "direction": tip.direction, "regime": tip.market_regime})

    return results


def cmd_advise(capital: float):
    """Personalized Capital Advisor CLI."""
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

    # Open trades table
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM paper_trades WHERE status IN ('PENDING_ENTRY', 'OPEN', 'BREAK_EVEN')")
        trades = [dict(r) for r in cursor.fetchall()]

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
        console.print("[dim italic]No active open positions. Next 4-hour cycle will evaluate entries.[/dim italic]")


def cmd_report():
    """Outputs a comprehensive Markdown calibration report."""
    load_environment()
    db = DatabaseManager()
    gate = ReadinessGate(db)
    metrics = gate.evaluate_metrics()

    console.print(f"""
# 30-DAY CALIBRATION PERFORMANCE REPORT
- **Timestamp**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
- **Readiness State**: {metrics['status']}
- **Simulation Epoch**: #{metrics['simulation_epochs']}
- **Total Resolved Trades**: {metrics['total_trades']}
- **Win Rate**: {metrics['win_rate']*100:.1f}%
- **Profit Factor**: {metrics['profit_factor']:.2f}
- **Max Drawdown**: {metrics['max_drawdown_percent']:.2f}%
- **Active Bankroll**: ${metrics['active_bankroll']:.2f}
- **Locked Profit Vault ("Profit Brought Home")**: ${metrics['total_vaulted_profit']:.2f}
""")


def cmd_daemon():
    """Runs autonomous 4-hour cycle daemon."""
    console.print("[bold green]Starting Autonomous 4-Hour Deriv Bot Daemon...[/bold green]")
    console.print("[dim]Scheduled hours: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC[/dim]")
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        scheduler = BlockingScheduler()
        scheduler.add_job(execute_cycle, "cron", hour="0,4,8,12,16,20", minute="0")
        # Run immediately upon start
        execute_cycle()
        scheduler.start()
    except (ImportError, Exception):
        console.print("[yellow]APScheduler unavailable or running in loop mode. Running cycle loop...[/yellow]")
        while True:
            execute_cycle()
            time.sleep(4 * 3600)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Deriv Trading Bot & Gemini Council Advisor")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # run-once
    p_run = subparsers.add_parser("run-once", help="Run a single 4-hour scan cycle")
    p_run.add_argument("--dry-run", action="store_true", help="Scan without submitting virtual orders")
    p_run.add_argument("--symbol", type=str, default=None, help="Filter to a specific instrument")

    # advise
    p_advise = subparsers.add_parser("advise", help="Get personalized capital advice")
    p_advise.add_argument("--capital", type=float, default=100.0, help="Capital amount in USD (default: 100)")

    # status
    subparsers.add_parser("status", help="Show system dashboard and open positions")

    # report
    subparsers.add_parser("report", help="Generate performance report")

    # daemon
    subparsers.add_parser("daemon", help="Run 4-hour continuous daemon")

    args = parser.parse_args()

    if args.command == "run-once":
        execute_cycle(dry_run=args.dry_run, symbol_filter=args.symbol)
    elif args.command == "advise":
        cmd_advise(args.capital)
    elif args.command == "status":
        cmd_status()
    elif args.command == "report":
        cmd_report()
    elif args.command == "daemon":
        cmd_daemon()
    else:
        # Default behavior: run advise with default $100 capital
        cmd_advise(100.0)


if __name__ == "__main__":
    main()
