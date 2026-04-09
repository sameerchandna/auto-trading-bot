"""Parallel fold runner — each fold runs in its own process."""
from __future__ import annotations

import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime

from backtest.folds.splits import Fold

logger = logging.getLogger(__name__)


def _plog(msg: str) -> None:
    """Unbuffered progress line to stderr — visible across workers."""
    print(f"[folds] {msg}", file=sys.stderr, flush=True)


def run_single_fold(args: dict) -> dict:
    """Top-level picklable worker.

    If args['optimize'] is True, fits Optuna on the fold's IS window first
    (params_override, no global mutation), then evaluates those params on OOS.
    Otherwise runs baseline params directly.
    """
    from datetime import date as _date
    from backtest.engine import BacktestEngine
    from backtest.config import BacktestConfig

    fold_dict = args["fold"]
    pair = args["pair"]
    capital = args["capital"]
    cfg_kwargs = args["config_kwargs"]
    do_optimize = args.get("optimize", False)
    n_trials = args.get("n_trials", 30)

    cfg = BacktestConfig(**cfg_kwargs)

    fid = fold_dict["fold_id"]
    t0 = time.time()
    opt_result = None
    params_override = None
    if do_optimize:
        from backtest.folds.optimizer import optimize_fold
        is_start = _date.fromisoformat(fold_dict["is_start"])
        is_end = _date.fromisoformat(fold_dict["is_end"])
        is_days = (is_end - is_start).days
        _plog(f"{fid}: IS optimize start ({n_trials} trials, IS={is_days}d)")
        opt_result = optimize_fold(pair, is_start, is_end, capital, cfg, n_trials=n_trials)
        params_override = opt_result["params"]
        _plog(f"{fid}: IS done mode={opt_result['mode']} in {time.time()-t0:.1f}s")
    else:
        _plog(f"{fid}: start (baseline)")

    start_dt = datetime.combine(
        datetime.fromisoformat(fold_dict["oos_start"]).date(), datetime.min.time()
    )
    end_dt = datetime.combine(
        datetime.fromisoformat(fold_dict["oos_end"]).date(), datetime.max.time()
    )

    engine = BacktestEngine(
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=capital,
        config=cfg,
        pair=pair,
        params_override=params_override,
    )
    oos_t0 = time.time()
    results = engine.run()
    _plog(f"{fid}: OOS done in {time.time()-oos_t0:.1f}s (total {time.time()-t0:.1f}s)")

    closed = list(engine.risk_mgr.closed_positions) if "error" not in results else []
    out = {
        "fold_id": fold_dict["fold_id"],
        "label": fold_dict["label"],
        "partial": fold_dict["partial"],
        "oos_start": fold_dict["oos_start"],
        "oos_end": fold_dict["oos_end"],
        "is_start": fold_dict["is_start"],
        "is_end": fold_dict["is_end"],
        "metrics": results.get("metrics", {}),
        "equity_curve": results.get("equity_curve", []),
        "trades": _serialize_trades(closed),
        "error": results.get("error"),
    }
    if opt_result is not None:
        out["mode"] = opt_result["mode"]
        out["best_params"] = opt_result["params"]
        out["is_metrics"] = opt_result["is_metrics"]
        out["opt_score"] = opt_result.get("score")
        out["opt_reason"] = opt_result.get("reason")
    else:
        out["mode"] = "baseline"
    return out


def _serialize_trades(trades) -> list[dict]:
    out = []
    for t in trades:
        out.append({
            "direction": t.signal.direction.value,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "size": t.size,
            "pnl": t.pnl,
            "pnl_pips": t.pnl_pips,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
            "signal_type": t.signal.signal_type.value if hasattr(t.signal.signal_type, "value") else str(t.signal.signal_type),
            "stop_loss": t.signal.stop_loss,
            "take_profit": t.signal.take_profit,
            "confluence_score": t.signal.confluence_score,
            "risk_amount": t.risk_amount,
        })
    return out


def _fold_to_dict(f: Fold) -> dict:
    d = asdict(f)
    for k in ("is_start", "is_end", "oos_start", "oos_end"):
        d[k] = d[k].isoformat()
    return d


def run_folds(
    pair: str,
    folds: list[Fold],
    capital: float,
    config,
    max_workers: int | None = None,
    optimize: bool = False,
    n_trials: int = 30,
) -> list[dict]:
    """Run every fold in parallel via ProcessPoolExecutor."""
    if max_workers is None:
        max_workers = max(1, (os.cpu_count() or 2) - 1)

    cfg_kwargs = {
        "no_overlap": config.no_overlap,
        "min_score": config.min_score,
        "block_hours": list(config.block_hours),
        "block_days": list(config.block_days),
        "cooldown_after_losses": config.cooldown_after_losses,
        "exclude_timeframes": list(config.exclude_timeframes),
        "sl_method": config.sl_method,
    }

    payloads = [
        {
            "fold": _fold_to_dict(f),
            "pair": pair,
            "capital": capital,
            "config_kwargs": cfg_kwargs,
            "optimize": optimize,
            "n_trials": n_trials,
        }
        for f in folds
    ]

    total = len(payloads)
    _plog(f"launching {total} folds (workers={max_workers}, optimize={optimize}, trials={n_trials})")
    wall0 = time.time()

    results: list[dict] = []
    if max_workers == 1:
        for i, p in enumerate(payloads, 1):
            _plog(f"[{i}/{total}] {p['fold']['fold_id']}: begin")
            results.append(run_single_fold(p))
            _plog(f"[{i}/{total}] {p['fold']['fold_id']}: complete ({time.time()-wall0:.0f}s elapsed)")
        return _sorted(results, folds)

    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(run_single_fold, p): p["fold"]["fold_id"] for p in payloads}
        done = 0
        for fut in as_completed(futures):
            fid = futures[fut]
            done += 1
            try:
                results.append(fut.result())
                _plog(f"[{done}/{total}] {fid}: complete ({time.time()-wall0:.0f}s elapsed)")
            except Exception as e:
                logger.exception(f"Fold {fid} crashed: {e}")
                results.append({
                    "fold_id": fid,
                    "label": fid,
                    "partial": False,
                    "metrics": {},
                    "equity_curve": [],
                    "trades": [],
                    "error": str(e),
                })

    return _sorted(results, folds)


def _sorted(results: list[dict], folds: list[Fold]) -> list[dict]:
    order = {f.fold_id: i for i, f in enumerate(folds)}
    return sorted(results, key=lambda r: order.get(r["fold_id"], 999))
