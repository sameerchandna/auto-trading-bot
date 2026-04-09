"""EURUSD Auto Trading Bot - Main CLI Entry Point."""
import logging
import sys
from datetime import datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from config.logging_config import setup_logging
from config.assets import ASSETS, ACTIVE_ASSETS, get_asset, resolve_pair_name, DEFAULT_ASSET
from config.settings import (
    PAIR, PAIR_NAME, STARTING_CAPITAL, TIMEFRAMES,
    DASHBOARD_HOST, DASHBOARD_PORT,
)

app = typer.Typer(name="trading-bot", help="EURUSD Auto Trading Bot")
console = Console()


@app.command()
def fetch(
    pairs: str = typer.Option("", "--pairs", help="Comma-separated pairs to fetch (e.g. EURUSD,AUDUSD). Default: active assets"),
):
    """Fetch latest data for all timeframes."""
    setup_logging()
    import time
    from data.ingestion import fetch_and_cache

    # Determine which pairs to fetch
    if pairs:
        pair_list = [p.strip().upper() for p in pairs.split(",")]
        for p in pair_list:
            get_asset(p)  # Validate asset exists
    else:
        pair_list = ACTIVE_ASSETS

    for i, pair_name in enumerate(pair_list):
        asset = get_asset(pair_name)
        console.print(Panel(f"Fetching {pair_name} data...", style="blue"))
        data = fetch_and_cache(asset.yahoo_ticker)

        table = Table(title=f"{pair_name} Data Summary")
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

        # Rate limit between pairs
        if i < len(pair_list) - 1:
            console.print("[dim]Waiting 0.5s before next pair...[/]")
            time.sleep(0.5)


@app.command()
def analyze(
    pair: str = typer.Option(DEFAULT_ASSET, "--pair", help="Asset to analyze (e.g. EURUSD, XAUUSD)"),
):
    """Run current market analysis on all timeframes."""
    setup_logging()
    from data.ingestion import fetch_and_cache
    from analysis.context import build_price_context
    from analysis.confluence import score_confluence

    asset = get_asset(pair)
    dec = asset.price_decimals

    console.print(Panel(f"Analyzing {asset.name}...", style="blue"))

    # Fetch fresh data
    all_candles = fetch_and_cache(asset.yahoo_ticker)
    context = build_price_context(all_candles, pair=asset.name)

    # Display analysis
    table = Table(title=f"{asset.name} Multi-Timeframe Analysis")
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
                f"{a.atr:.{dec}f}",
                f"{a.current_price:.{dec}f}",
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
                f"Entry: {s.entry_price:.{dec}f} | "
                f"SL: {s.stop_loss:.{dec}f} | TP: {s.take_profit:.{dec}f}"
            )
    else:
        console.print("\n[dim]No signals at current confluence threshold[/]")


@app.command()
def run(
    interval: int = typer.Option(300, help="Loop interval in seconds (default 300s = 5min)"),
    pairs: str = typer.Option("", "--pairs", help="Comma-separated pairs to trade (default: active assets)"),
):
    """Start the trading bot on OANDA practice account."""
    setup_logging()
    from engine.pipeline import TradingPipeline

    # Parse pairs
    if pairs:
        pair_list = [p.strip().upper() for p in pairs.split(",")]
        for p in pair_list:
            get_asset(p)  # Validate
    else:
        pair_list = list(ACTIVE_ASSETS)

    # Show OANDA account info
    try:
        from data.oanda import get_account_summary, get_current_price
        acct = get_account_summary()
        first_asset = get_asset(pair_list[0])
        price = get_current_price(instrument=first_asset.oanda_instrument)
        dec = first_asset.price_decimals
        console.print(Panel(
            f"OANDA Practice Account Connected\n"
            f"Balance: \u00a3{float(acct['balance']):,.2f}\n"
            f"{first_asset.name}: {price['mid']:.{dec}f} (spread: {price['spread']/first_asset.pip_value:.1f} pips)\n"
            f"Tradeable: {price['tradeable']}",
            title="OANDA",
            style="green",
        ))
    except Exception as e:
        console.print(f"[yellow]OANDA not available: {e}[/]")

    console.print(Panel(
        f"Starting Trading Bot\n"
        f"Pairs: {', '.join(pair_list)}\n"
        f"Mode: OANDA Practice Account\n"
        f"Interval: {interval}s\n"
        f"Risk: 2% per trade, max 3 positions",
        title="Trading Bot",
        style="green",
    ))

    pipeline = TradingPipeline(pairs=pair_list)
    pipeline.setup()
    pipeline.run_loop(interval_seconds=interval)


@app.command()
def backtest(
    start: str = typer.Option("2024-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(None, help="End date YYYY-MM-DD (default: today)"),
    capital: float = typer.Option(10_000, help="Starting capital"),
    pair: str = typer.Option(DEFAULT_ASSET, "--pair", help="Asset to backtest (e.g. EURUSD, XAUUSD, US30)"),
    no_overlap: bool = typer.Option(False, "--no-overlap", help="Block same-direction entries when a position is already open"),
    min_score: float = typer.Option(0.0, "--min-score", help="Additional min score filter (0=use params threshold only)"),
    block_hours: str = typer.Option("", "--block-hours", help="Comma-separated UTC hours to skip (e.g. '17,18,19')"),
    block_days: str = typer.Option("", "--block-days", help="Comma-separated days to skip: mon,tue,wed,thu,fri"),
    cooldown: int = typer.Option(0, "--cooldown", help="Skip N signals after this many consecutive losses"),
    exclude_tf: str = typer.Option("", "--exclude-tf", help="Comma-separated timeframes to exclude (e.g. '15m')"),
    label: str = typer.Option("", "--label", help="Optional label for this run"),
):
    """Run a backtest on historical data."""
    setup_logging()
    from backtest.engine import BacktestEngine
    from backtest.config import BacktestConfig
    from storage.database import BacktestRecord, PositionRecord, get_session
    import json

    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d") if end else datetime.utcnow()

    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    cfg = BacktestConfig(
        no_overlap=no_overlap,
        min_score=min_score,
        block_hours=[int(h) for h in block_hours.split(",") if h.strip()] if block_hours else [],
        block_days=[day_map[d.strip().lower()] for d in block_days.split(",") if d.strip()] if block_days else [],
        cooldown_after_losses=cooldown,
        exclude_timeframes=[t.strip() for t in exclude_tf.split(",") if t.strip()] if exclude_tf else [],
    )
    cfg_label = label or cfg.label()

    asset = get_asset(pair)

    console.print(Panel(
        f"Backtesting {asset.name}\n"
        f"Period: {start} to {end_date.strftime('%Y-%m-%d')}\n"
        f"Capital: \u00a3{capital:,}\n"
        f"Config:  {cfg_label}",
        title="Backtest",
        style="blue",
    ))

    engine = BacktestEngine(start_date, end_date, capital, config=cfg, pair=pair)
    results = engine.run()

    if "error" in results:
        console.print(f"[red]Error: {results['error']}[/]")
        return

    metrics = results["metrics"]
    _print_metrics_table(metrics, title=f"Backtest Results — {asset.name} — {cfg_label}")
    _save_backtest(engine, metrics, start_date, end_date, cfg, cfg_label, pair=pair)


def _print_metrics_table(metrics: dict, title: str = "Backtest Results"):
    table = Table(title=title)
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


def _save_backtest(engine, metrics: dict, start_date, end_date, cfg, cfg_label: str, pair: str = DEFAULT_ASSET):
    from storage.database import BacktestRecord, PositionRecord, TradeJournalRecord, get_session
    import json

    # Snapshot the strategy params that were actually live for this run so
    # two "baseline" runs on different param versions are distinguishable.
    strat = getattr(engine, "params", None) or {}
    session = get_session()
    try:
        rec = BacktestRecord(
            pair=pair,
            start_date=start_date,
            end_date=end_date,
            params_json=json.dumps({
                "config": cfg_label,
                "threshold": strat.get("threshold"),
                "sl_multiplier": strat.get("sl_multiplier"),
                "tp_risk_reward": strat.get("tp_risk_reward"),
                "swing_lookback": strat.get("swing_lookback"),
                "weights": strat.get("weights"),
            }),
            results_json=json.dumps(metrics),
            total_trades=metrics["total_trades"],
            win_rate=metrics["win_rate"],
            total_pnl=metrics["total_pnl"],
            max_drawdown=metrics["max_drawdown_pct"],
            sharpe_ratio=metrics["sharpe_ratio"],
        )
        session.add(rec)
        session.flush()

        trade_tags = cfg.tags() + [f"bt_{rec.id}"]
        closed_trades = engine.risk_mgr.closed_positions
        trade_count = 0
        for pos in closed_trades:
            duration_mins = 0
            if pos.opened_at and pos.closed_at:
                duration_mins = int((pos.closed_at - pos.opened_at).total_seconds() / 60)

            pos_rec = PositionRecord(
                signal_id=None,
                pair=pair,
                status="closed",
                direction=pos.signal.direction.value,
                entry_price=pos.entry_price,
                exit_price=pos.exit_price,
                size=pos.size,
                risk_amount=pos.risk_amount,
                opened_at=pos.opened_at,
                closed_at=pos.closed_at,
                pnl=pos.pnl,
                pnl_pips=pos.pnl_pips,
                tags=json.dumps(trade_tags),
                signal_type=pos.signal.signal_type.value if hasattr(pos.signal.signal_type, 'value') else str(pos.signal.signal_type),
                stop_loss=pos.signal.stop_loss,
                take_profit=pos.signal.take_profit,
                confluence_score=pos.signal.confluence_score,
            )
            session.add(pos_rec)
            session.flush()

            journal = TradeJournalRecord(
                position_id=pos_rec.id,
                pair=pair,
                duration_minutes=duration_mins,
            )
            session.add(journal)
            trade_count += 1

        session.commit()
        console.print(f"[green]Saved to database: BT#{rec.id} ({trade_count} trades)[/]")
    finally:
        session.close()


@app.command("backtest-folds")
def backtest_folds(
    pair: str = typer.Option(DEFAULT_ASSET, "--pair", help="Asset to backtest"),
    scheme: str = typer.Option("walkforward", "--scheme", help="walkforward | kfold_shuffled"),
    start: str = typer.Option("2023-01-01", "--start", help="Start YYYY-MM-DD"),
    end: str = typer.Option(None, "--end", help="End YYYY-MM-DD (default: latest candle in DB)"),
    capital: float = typer.Option(10_000, "--capital"),
    workers: int = typer.Option(0, "--workers", help="Parallel workers (0 = os.cpu_count()-1)"),
    label: str = typer.Option("", "--label"),
    optimize: bool = typer.Option(False, "--optimize", help="Per-fold Optuna fit on IS, eval on OOS"),
    trials: int = typer.Option(30, "--trials", help="Optuna trials per fold when --optimize"),
    no_overlap: bool = typer.Option(False, "--no-overlap"),
    min_score: float = typer.Option(0.0, "--min-score"),
    block_hours: str = typer.Option("", "--block-hours"),
    block_days: str = typer.Option("", "--block-days"),
    cooldown: int = typer.Option(0, "--cooldown"),
    exclude_tf: str = typer.Option("", "--exclude-tf"),
):
    """Walk-forward multi-fold backtest: run baseline on every quarter in range in parallel."""
    setup_logging()
    from datetime import date as _date
    import json as _json

    from backtest.config import BacktestConfig
    from backtest.folds.splits import SCHEMES, latest_candle_date
    from backtest.folds.runner import run_folds
    from backtest.folds.aggregator import aggregate
    from backtest.folds.report import render_report
    from storage.database import (
        BacktestFoldsRun, BacktestRecord, PositionRecord, get_session,
    )

    if scheme not in SCHEMES:
        console.print(f"[red]Unknown scheme '{scheme}'. Options: {list(SCHEMES)}[/]")
        raise typer.Exit(1)

    start_d = datetime.strptime(start, "%Y-%m-%d").date()
    if end:
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
    else:
        end_d = latest_candle_date(pair)
        console.print(f"[dim]--end not given; using latest candle in DB: {end_d}[/]")

    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    cfg = BacktestConfig(
        no_overlap=no_overlap,
        min_score=min_score,
        block_hours=[int(h) for h in block_hours.split(",") if h.strip()] if block_hours else [],
        block_days=[day_map[d.strip().lower()] for d in block_days.split(",") if d.strip()] if block_days else [],
        cooldown_after_losses=cooldown,
        exclude_timeframes=[t.strip() for t in exclude_tf.split(",") if t.strip()] if exclude_tf else [],
    )

    folds = SCHEMES[scheme](start_d, end_d)
    if not folds:
        console.print("[red]No folds produced — check --start/--end[/]")
        raise typer.Exit(1)

    asset = get_asset(pair)
    console.print(Panel(
        f"backtest-folds — {asset.name}\n"
        f"Scheme:  {scheme}\n"
        f"Range:   {start_d} to {end_d}\n"
        f"Folds:   {len(folds)} (partial: {sum(1 for f in folds if f.partial)})\n"
        f"Capital: \u00a3{capital:,}\n"
        f"Config:  {cfg.label()}\n"
        f"Workers: {workers or 'auto'}",
        title="Walk-Forward Folds",
        style="blue",
    ))

    results = run_folds(
        pair, folds, capital, cfg,
        max_workers=workers or None,
        optimize=optimize,
        n_trials=trials,
    )
    agg = aggregate(results, capital)

    # Persist
    session = get_session()
    parent_id = None
    try:
        parent = BacktestFoldsRun(
            pair=pair,
            scheme=scheme,
            num_folds=len(folds),
            start_date=str(start_d),
            end_date=str(end_d),
            params_json=_json.dumps({"config": cfg.label()}),
            summary_json=_json.dumps(agg["summary"]),
            combined_metrics_json=_json.dumps(agg["combined_metrics"]),
            combined_equity_curve_json=_json.dumps(agg["combined_equity_curve"]),
            per_fold_json=_json.dumps(agg["per_fold"]),
            label=label or f"{scheme}-{start_d}-{end_d}",
        )
        session.add(parent)
        session.flush()
        parent_id = parent.id

        for r in results:
            m = r.get("metrics") or {}
            child = BacktestRecord(
                pair=pair,
                start_date=datetime.fromisoformat(r["oos_start"]),
                end_date=datetime.fromisoformat(r["oos_end"]),
                params_json=_json.dumps({
                    "fold": r["fold_id"],
                    "partial": r.get("partial", False),
                    "mode": r.get("mode", "baseline"),
                    "best_params": r.get("best_params"),
                    "is_metrics": r.get("is_metrics"),
                }),
                results_json=_json.dumps(m),
                total_trades=m.get("total_trades", 0),
                win_rate=m.get("win_rate", 0.0),
                total_pnl=m.get("total_pnl", 0.0),
                max_drawdown=m.get("max_drawdown_pct", 0.0),
                sharpe_ratio=m.get("sharpe_ratio", 0.0),
                fold_parent_id=parent_id,
            )
            session.add(child)
        session.commit()
    finally:
        session.close()

    render_report(agg, parent_id, pair=pair, scheme=scheme, console=console)


@app.command()
def compare(
    start: str = typer.Option("2023-01-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(None, help="End date YYYY-MM-DD (default: today)"),
    capital: float = typer.Option(10_000, help="Starting capital"),
    pair: str = typer.Option(DEFAULT_ASSET, "--pair", help="Asset to compare"),
    no_overlap: bool = typer.Option(False, "--no-overlap"),
    min_score: float = typer.Option(0.0, "--min-score"),
    block_hours: str = typer.Option("", "--block-hours"),
    block_days: str = typer.Option("", "--block-days"),
    cooldown: int = typer.Option(0, "--cooldown"),
    exclude_tf: str = typer.Option("", "--exclude-tf", help="Comma-separated timeframes to exclude (e.g. '15m')"),
    sl_method: str = typer.Option("atr", "--sl-method", help="SL placement: 'atr' or 'structure'"),
):
    """Run baseline vs. a modified config and show side-by-side results."""
    setup_logging()
    from backtest.engine import BacktestEngine
    from backtest.config import BacktestConfig, BASELINE
    import json

    start_date = datetime.strptime(start, "%Y-%m-%d")
    end_date = datetime.strptime(end, "%Y-%m-%d") if end else datetime.utcnow()

    day_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    test_cfg = BacktestConfig(
        no_overlap=no_overlap,
        min_score=min_score,
        block_hours=[int(h) for h in block_hours.split(",") if h.strip()] if block_hours else [],
        block_days=[day_map[d.strip().lower()] for d in block_days.split(",") if d.strip()] if block_days else [],
        cooldown_after_losses=cooldown,
        exclude_timeframes=[t.strip() for t in exclude_tf.split(",") if t.strip()] if exclude_tf else [],
        sl_method=sl_method,
    )

    if test_cfg.label() == "baseline":
        console.print("[yellow]No rules specified — nothing to compare. Add at least one flag (e.g. --no-overlap)[/]")
        return

    console.print(Panel(
        f"Comparing: [bold]baseline[/] vs [bold]{test_cfg.label()}[/]\n"
        f"Period: {start} to {end_date.strftime('%Y-%m-%d')} | Capital: \u00a3{capital:,}",
        title="A/B Comparison",
        style="blue",
    ))

    # Run baseline
    console.print("\n[dim]Running baseline...[/]")
    base_engine = BacktestEngine(start_date, end_date, capital, config=BASELINE, pair=pair)
    base_results = base_engine.run()

    # Run test
    console.print(f"[dim]Running {test_cfg.label()}...[/]")
    test_engine = BacktestEngine(start_date, end_date, capital, config=test_cfg, pair=pair)
    test_results = test_engine.run()

    if "error" in base_results or "error" in test_results:
        console.print("[red]Error running one or both backtests[/]")
        return

    bm = base_results["metrics"]
    tm = test_results["metrics"]

    # Side-by-side comparison table
    COMPARE_KEYS = [
        ("total_trades",       "Trades",       lambda v: str(v),               False),
        ("wins",               "Wins",         lambda v: str(v),               True),
        ("losses",             "Losses",       lambda v: str(v),               False),
        ("win_rate",           "Win Rate",     lambda v: f"{v:.1%}",           True),
        ("profit_factor",      "Profit Factor",lambda v: f"{v:.2f}",           True),
        ("total_pnl",          "Total P&L",    lambda v: f"\u00a3{v:.2f}",    True),
        ("expectancy_pips",    "Expectancy",   lambda v: f"{v:+.1f} pips",    True),
        ("max_drawdown_pct",   "Max Drawdown", lambda v: f"{v:.1%}",           False),
        ("sharpe_ratio",       "Sharpe",       lambda v: f"{v:.2f}",           True),
        ("avg_win_pips",       "Avg Win",      lambda v: f"{v:.1f} pips",     True),
        ("avg_loss_pips",      "Avg Loss",     lambda v: f"{v:.1f} pips",     False),
        ("consecutive_losses", "Max Con. Losses", lambda v: str(v),           False),
    ]

    table = Table(title="Baseline vs " + test_cfg.label())
    table.add_column("Metric", style="cyan")
    table.add_column("Baseline", style="white")
    table.add_column(test_cfg.label(), style="white")
    table.add_column("Delta", style="bold")

    for key, label, fmt, higher_is_better in COMPARE_KEYS:
        bv = bm.get(key, 0)
        tv = tm.get(key, 0)
        b_str = fmt(bv)
        t_str = fmt(tv)

        if isinstance(bv, (int, float)) and isinstance(tv, (int, float)):
            delta = tv - bv
            if key in ("win_rate", "max_drawdown_pct"):
                d_str = f"{delta:+.1%}"
            elif key == "total_pnl":
                d_str = f"\u00a3{delta:+.2f}"
            elif isinstance(delta, float):
                d_str = f"{delta:+.2f}"
            else:
                d_str = f"{delta:+d}"

            improved = (delta > 0) == higher_is_better
            delta_color = "green" if improved else "red"
            delta_cell = f"[{delta_color}]{d_str}[/{delta_color}]"
        else:
            delta_cell = "--"

        table.add_row(label, b_str, t_str, delta_cell)

    console.print(table)

    # Save both to DB
    _save_backtest(base_engine, bm, start_date, end_date, BASELINE, "baseline", pair=pair)
    _save_backtest(test_engine, tm, start_date, end_date, test_cfg, test_cfg.label(), pair=pair)


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
def status(
    pair: str = typer.Option("", "--pair", help="Filter by pair (default: all)"),
):
    """Show current bot status and stats."""
    setup_logging(logging.WARNING)
    from storage.database import PositionRecord, get_session

    session = get_session()
    try:
        query_open = session.query(PositionRecord).filter_by(status="open")
        query_closed = session.query(PositionRecord).filter_by(status="closed")
        if pair:
            query_open = query_open.filter_by(pair=pair)
            query_closed = query_closed.filter_by(pair=pair)

        open_pos = query_open.all()
        closed_pos = query_closed.all()

        total_pnl = sum(p.pnl for p in closed_pos if p.pnl)
        wins = sum(1 for p in closed_pos if p.pnl and p.pnl > 0)

        pair_label = pair or "All Pairs"
        console.print(Panel(
            f"Pair: {pair_label}\n"
            f"Capital: \u00a3{STARTING_CAPITAL + total_pnl:,.2f}\n"
            f"Total P&L: \u00a3{total_pnl:+,.2f}\n"
            f"Open Positions: {len(open_pos)}\n"
            f"Closed Trades: {len(closed_pos)}\n"
            f"Win Rate: {wins/len(closed_pos):.1%}" if closed_pos else f"Pair: {pair_label}\nCapital: \u00a3{STARTING_CAPITAL:,}\nNo trades yet",
            title="Bot Status",
            style="cyan",
        ))

        if open_pos:
            table = Table(title="Open Positions")
            table.add_column("ID")
            table.add_column("Pair")
            table.add_column("Direction")
            table.add_column("Entry")
            table.add_column("SL")
            table.add_column("TP")
            table.add_column("Size")
            for p in open_pos:
                p_pair = p.pair or DEFAULT_ASSET
                try:
                    dec = get_asset(p_pair).price_decimals
                except KeyError:
                    dec = 5
                table.add_row(
                    str(p.id), p_pair, p.direction,
                    f"{p.entry_price:.{dec}f}",
                    f"{p.stop_loss:.{dec}f}" if p.stop_loss else "--",
                    f"{p.take_profit:.{dec}f}" if p.take_profit else "--",
                    f"{p.size:,.0f}",
                )
            console.print(table)
    finally:
        session.close()


if __name__ == "__main__":
    app()
