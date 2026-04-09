"""Aggregate per-fold results into summary stats + combined OOS equity curve."""
from __future__ import annotations

from datetime import datetime
from statistics import mean, median, pstdev

MIN_TRADES_PER_FOLD = 20

SUMMARY_METRICS = [
    "sharpe_ratio",
    "profit_factor",
    "win_rate",
    "max_drawdown_pct",
    "total_pnl",
    "total_trades",
]


def _status(fold: dict) -> str:
    if fold.get("error"):
        return "error"
    trades = fold.get("metrics", {}).get("total_trades", 0)
    if trades < MIN_TRADES_PER_FOLD:
        return "insufficient_data"
    if fold.get("partial"):
        return "partial"
    return "ok"


def aggregate(fold_results: list[dict], initial_capital: float) -> dict:
    per_fold = []
    countable = []
    for f in fold_results:
        status = _status(f)
        m = f.get("metrics") or {}
        per_fold.append({
            "fold_id": f["fold_id"],
            "label": f["label"],
            "partial": f.get("partial", False),
            "status": status,
            "total_trades": m.get("total_trades", 0),
            "win_rate": m.get("win_rate", 0.0),
            "profit_factor": m.get("profit_factor", 0.0),
            "sharpe_ratio": m.get("sharpe_ratio", 0.0),
            "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
            "total_pnl": m.get("total_pnl", 0.0),
        })
        if status == "ok":
            countable.append(f)

    summary: dict = {}
    for key in SUMMARY_METRICS:
        vals = [float(f["metrics"].get(key, 0.0)) for f in countable]
        if vals:
            summary[key] = {
                "mean": mean(vals),
                "median": median(vals),
                "std": pstdev(vals) if len(vals) > 1 else 0.0,
                "min": min(vals),
                "max": max(vals),
            }
        else:
            summary[key] = {"mean": 0, "median": 0, "std": 0, "min": 0, "max": 0}

    if countable:
        pct_profitable = sum(
            1 for f in countable if float(f["metrics"].get("total_pnl", 0.0)) > 0
        ) / len(countable)
    else:
        pct_profitable = 0.0

    combined_curve, combined_trades = _build_combined(fold_results, initial_capital)
    combined_metrics = _combined_metrics(combined_trades, initial_capital, combined_curve)

    return {
        "per_fold": per_fold,
        "summary": summary,
        "pct_profitable_folds": pct_profitable,
        "num_folds": len(fold_results),
        "num_counted": len(countable),
        "combined_equity_curve": combined_curve,
        "combined_metrics": combined_metrics,
    }


def _build_combined(fold_results: list[dict], initial_capital: float):
    """Concatenate OOS segments. Each fold's curve is re-based so it starts
    from the previous fold's ending value.
    """
    combined_curve: list[tuple[str, float]] = []
    combined_trades: list[dict] = []
    running = initial_capital

    for f in fold_results:
        curve = f.get("equity_curve") or []
        if not curve:
            continue
        seg_start = float(curve[0][1]) if curve else initial_capital
        offset = running - seg_start
        for ts, v in curve:
            combined_curve.append((ts, float(v) + offset))
        running = combined_curve[-1][1]
        combined_trades.extend(f.get("trades") or [])

    return combined_curve, combined_trades


def _combined_metrics(trades: list[dict], initial_capital: float, curve) -> dict:
    """Compute aggregate stats over the concatenated OOS runs.

    We keep this dict-based (no Position objects) so the aggregator stays
    pure/picklable and doesn't depend on the trading models.
    """
    if not trades:
        return {"total_trades": 0, "total_pnl": 0.0, "win_rate": 0.0, "profit_factor": 0.0}

    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    # Drawdown on the combined curve
    peak = curve[0][1] if curve else initial_capital
    max_dd = 0.0
    peak_at_dd = peak
    for _, v in curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
            peak_at_dd = peak
    max_dd_pct = max_dd / peak_at_dd if peak_at_dd else 0.0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trades),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "final_capital": round(curve[-1][1], 2) if curve else initial_capital,
    }
