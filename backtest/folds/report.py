"""Rich console rendering for fold run results."""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel


def render_report(agg: dict, parent_id: int | None, pair: str, scheme: str, console: Console | None = None):
    console = console or Console()

    tbl = Table(title=f"Walk-Forward Folds — {pair} — {scheme}")
    tbl.add_column("Fold", style="cyan")
    tbl.add_column("Status", style="dim")
    tbl.add_column("Trades", justify="right")
    tbl.add_column("Win%", justify="right")
    tbl.add_column("PF", justify="right")
    tbl.add_column("Sharpe", justify="right")
    tbl.add_column("MaxDD%", justify="right")
    tbl.add_column("P&L", justify="right")

    for f in agg["per_fold"]:
        color = {
            "ok": "white",
            "partial": "yellow",
            "insufficient_data": "dim",
            "error": "red",
        }.get(f["status"], "white")
        pnl = f["total_pnl"]
        pnl_color = "green" if pnl > 0 else "red"
        tbl.add_row(
            f["fold_id"],
            f"[{color}]{f['status']}[/]",
            str(f["total_trades"]),
            f"{f['win_rate']:.1%}",
            f"{f['profit_factor']:.2f}",
            f"{f['sharpe_ratio']:.2f}",
            f"{f['max_drawdown_pct']:.1%}",
            f"[{pnl_color}]£{pnl:,.2f}[/]",
        )
    console.print(tbl)

    # Summary block
    s = agg["summary"]
    lines = [
        f"Counted folds:    {agg['num_counted']} / {agg['num_folds']}",
        f"% profitable:     {agg['pct_profitable_folds']:.1%}",
        "",
        f"Sharpe   mean={s['sharpe_ratio']['mean']:.2f}  med={s['sharpe_ratio']['median']:.2f}  std={s['sharpe_ratio']['std']:.2f}  min={s['sharpe_ratio']['min']:.2f}  max={s['sharpe_ratio']['max']:.2f}",
        f"PF       mean={s['profit_factor']['mean']:.2f}  med={s['profit_factor']['median']:.2f}  std={s['profit_factor']['std']:.2f}",
        f"WinRate  mean={s['win_rate']['mean']:.1%}  med={s['win_rate']['median']:.1%}",
        f"MaxDD%   mean={s['max_drawdown_pct']['mean']:.1%}  max={s['max_drawdown_pct']['max']:.1%}",
        f"P&L      mean=£{s['total_pnl']['mean']:,.0f}  med=£{s['total_pnl']['median']:,.0f}  min=£{s['total_pnl']['min']:,.0f}  max=£{s['total_pnl']['max']:,.0f}",
    ]
    console.print(Panel("\n".join(lines), title="Per-Fold Summary", style="blue"))

    cm = agg["combined_metrics"]
    cm_lines = [
        f"Trades:       {cm.get('total_trades', 0)}",
        f"Win rate:     {cm.get('win_rate', 0):.1%}",
        f"Profit factor:{cm.get('profit_factor', 0):.2f}",
        f"Total P&L:    £{cm.get('total_pnl', 0):,.2f}",
        f"Max DD%:      {cm.get('max_drawdown_pct', 0):.1%}",
        f"Final equity: £{cm.get('final_capital', 0):,.2f}",
    ]
    console.print(Panel("\n".join(cm_lines), title="Combined OOS", style="green"))

    if parent_id is not None:
        console.print(f"[dim]Saved as backtest_folds_runs.id={parent_id}[/]")
