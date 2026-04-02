"""EURUSD Auto Trading Bot - Main CLI Entry Point."""
import logging
import sys
from datetime import datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config.logging_config import setup_logging
from config.settings import (
    PAIR, PAIR_NAME, STARTING_CAPITAL, TIMEFRAMES,
    DASHBOARD_HOST, DASHBOARD_PORT,
)

app = typer.Typer(name="trading-bot", help="EURUSD Auto Trading Bot")
console = Console()


@app.command()
def fetch():
    """Fetch latest EURUSD data for all timeframes."""
    setup_logging()
    from data.ingestion import fetch_and_cache

    console.print(Panel(f"Fetching {PAIR_NAME} data...", style="blue"))
    data = fetch_and_cache(PAIR)

    table = Table(title="Data Summary")
    table.add_column("Timeframe", style="cyan")
    table.add_column("Candles", style="green")
    table.add_column("From", style="dim")
    table.add_column("To", style="dim")

    for tf, candles in data.items():
        if candles:
            table.add_row(
                tf,
                str(len(candles)),
                candles[0].timestamp.strftime("%Y-%m-%d"),
                candles[-1].timestamp.strftime("%Y-%m-%d"),
            )
        else:
            table.add_row(tf, "0", "--", "--")

    console.print(table)


@app.command()
def analyze():
    """Run current market analysis on all timeframes."""
    setup_logging()
    from data.ingestion import fetch_and_cache
    from analysis.context import build_price_context
    from analysis.confluence import score_confluence

    console.print(Panel(f"Analyzing {PAIR_NAME}...", style="blue"))

    # Fetch fresh data
    all_candles = fetch_and_cache(PAIR)
    context = build_price_context(all_candles)

    # Display analysis
    table = Table(title=f"{PAIR_NAME} Multi-Timeframe Analysis")
    table.add_column("TF", style="cyan")
    table.add_column("Bias", style="bold")
    table.add_column("Wave Phase")
    table.add_column("Break")
    table.add_column("ATR")
    table.add_column("Price")
    table.add_column("S/R Zones")

    for tf in TIMEFRAMES:
        if tf in context.analyses:
            a = context.analyses[tf]
            bias_color = {
                "bullish": "green", "bearish": "red", "ranging": "yellow"
            }.get(a.structure.bias.value, "white")

            table.add_row(
                tf,
                f"[{bias_color}]{a.structure.bias.value}[/{bias_color}]",
                a.wave.phase.value,
                a.structure.last_break.value,
                f"{a.atr:.5f}",
                f"{a.current_price:.5f}",
                str(len(a.sr_zones)),
            )

    console.print(table)
    console.print(
        f"\nOverall Bias: [{('green' if context.overall_bias.value == 'bullish' else 'red' if context.overall_bias.value == 'bearish' else 'yellow')}]"
        f"{context.overall_bias.value}[/] "
        f"(strength: {context.bias_strength:.2f})"
    )

    # Check for signals
    signals = score_confluence(context)
    if signals:
        console.print(f"\n[bold green]Found {len(signals)} signal(s):[/]")
        for s in signals:
            color = "green" if s.direction.value == "long" else "red"
            console.print(
                f"  [{color}]{s.direction.value.upper()}[/{color}] | "
                f"Score: {s.confluence_score:.0%} | "
                f"Entry: {s.entry_price:.5f} | "
                f"SL: {s.stop_loss:.5f} | TP: {s.take_profit:.5f}"
            )
    else:
        console.print("\n[dim]No signals at current confluence threshold[/]")


@app.command()
def run(
    interval: int = typer.Option(60, help="Loop interval in seconds (default 60s)"),
):
    """Start the trading bot on OANDA practice account."""
    setup_logging()
    from engine.pipeline import TradingPipeline

    # Show OANDA account info
    try:
        from data.oanda import get_account_summary, get_current_price
        acct = get_account_summary()
        price = get_current_price()
        console.print(Panel(
            f"OANDA Practice Account Connected\n"
            f"Balance: \u00a3{float(acct['balance']):,.2f}\n"
            f"EUR/USD: {price['mid']:.5f} (spread: {price['spread']*10000:.1f} pips)\n"
            f"Tradeable: {price['tradeable']}",
            title="OANDA",
            style="green",
        ))
    except Exception as e:
        console.print(f"[yellow]OANDA not available: {e}[/]")

    console.print(Panel(
        f"Starting {PAIR_NAME} Trading Bot\n"
        f"Mode: OANDA Practice Account\n"
        f"Interval: {interval}s\n"
        f"Risk: 2% per trade, max 3 positions",
        title="Trading Bot",
        style="green",
    ))

    pipeline = TradingPipeline()
    pipeline.setup()
    pipeline.run_loop(interval_seconds=interval)


@app.command()
def backtest(
    start: str = typer.Option("2024-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(None, help="End date YYYY-MM-DD (default: today)"),
    capital: float = typer.Option(10_000, help="Starting capital"),
):
    """Run a backtest on historical data."""
    setup_logging()
    from backtest.engine import BacktestEngine
    from storage.database import BacktestRecord, get_session
    import json

    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d") if end else datetime.utcnow()

    console.print(Panel(
        f"Backtesting {PAIR_NAME}\n"
        f"Period: {start} to {end_date.strftime('%Y-%m-%d')}\n"
        f"Capital: \u00a3{capital:,}",
        title="Backtest",
        style="blue",
    ))

    engine = BacktestEngine(start_date, end_date, capital)
    results = engine.run()

    if "error" in results:
        console.print(f"[red]Error: {results['error']}[/]")
        return

    metrics = results["metrics"]

    # Display results
    table = Table(title="Backtest Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    for key, value in metrics.items():
        if isinstance(value, float):
            if "rate" in key or "pct" in key:
                table.add_row(key.replace("_", " ").title(), f"{value:.1%}")
            else:
                table.add_row(key.replace("_", " ").title(), f"{value:.2f}")
        else:
            table.add_row(key.replace("_", " ").title(), str(value))

    console.print(table)

    # Save to database
    session = get_session()
    try:
        rec = BacktestRecord(
            start_date=start_date,
            end_date=end_date,
            results_json=json.dumps(metrics),
            total_trades=metrics["total_trades"],
            win_rate=metrics["win_rate"],
            total_pnl=metrics["total_pnl"],
            max_drawdown=metrics["max_drawdown_pct"],
            sharpe_ratio=metrics["sharpe_ratio"],
        )
        session.add(rec)
        session.commit()
        console.print("[green]Results saved to database[/]")
    finally:
        session.close()


@app.command()
def dashboard():
    """Launch the web dashboard."""
    setup_logging()
    import uvicorn

    console.print(Panel(
        f"Dashboard: http://{DASHBOARD_HOST}:{DASHBOARD_PORT}",
        title="Web Dashboard",
        style="blue",
    ))
    uvicorn.run(
        "dashboard.app:app",
        host=DASHBOARD_HOST,
        port=DASHBOARD_PORT,
        reload=True,
    )


@app.command()
def status():
    """Show current bot status and stats."""
    setup_logging(logging.WARNING)
    from storage.database import PositionRecord, get_session

    session = get_session()
    try:
        open_pos = session.query(PositionRecord).filter_by(status="open").all()
        closed_pos = session.query(PositionRecord).filter_by(status="closed").all()

        total_pnl = sum(p.pnl for p in closed_pos if p.pnl)
        wins = sum(1 for p in closed_pos if p.pnl and p.pnl > 0)
        losses = len(closed_pos) - wins

        console.print(Panel(
            f"Pair: {PAIR_NAME}\n"
            f"Capital: \u00a3{STARTING_CAPITAL + total_pnl:,.2f}\n"
            f"Total P&L: \u00a3{total_pnl:+,.2f}\n"
            f"Open Positions: {len(open_pos)}\n"
            f"Closed Trades: {len(closed_pos)}\n"
            f"Win Rate: {wins/len(closed_pos):.1%}" if closed_pos else f"Pair: {PAIR_NAME}\nCapital: \u00a3{STARTING_CAPITAL:,}\nNo trades yet",
            title="Bot Status",
            style="cyan",
        ))

        if open_pos:
            table = Table(title="Open Positions")
            table.add_column("ID")
            table.add_column("Direction")
            table.add_column("Entry")
            table.add_column("SL")
            table.add_column("TP")
            table.add_column("Size")
            for p in open_pos:
                table.add_row(
                    str(p.id), p.direction,
                    f"{p.entry_price:.5f}",
                    f"{p.stop_loss:.5f}" if p.stop_loss else "--",
                    f"{p.take_profit:.5f}" if p.take_profit else "--",
                    f"{p.size:,.0f}",
                )
            console.print(table)
    finally:
        session.close()


if __name__ == "__main__":
    app()
