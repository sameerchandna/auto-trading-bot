"""Per-fold Optuna optimizer — fits params on IS window, returns best params.

Uses BacktestEngine's params_override so it never mutates globals (safe for
ProcessPoolExecutor). Each fold creates its own study.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import optuna
from optuna.samplers import TPESampler

from backtest.engine import BacktestEngine
from config.params import load_strategy_params

logger = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)

MIN_IS_DAYS = 180   # ~2 quarters; folds with less IS fall back to baseline params
MIN_IS_TRADES = 20  # objective penalizes under-trading IS windows

WEIGHT_KEYS = ["htf_bias", "bos", "wave_position", "liquidity_sweep", "sr_reaction", "wave_ending"]


def _objective_factory(pair, is_start_dt, is_end_dt, capital, config):
    def obj(trial: optuna.Trial) -> float:
        raw = {
            "htf_bias": trial.suggest_float("htf_bias", 0.10, 0.40),
            "bos": trial.suggest_float("bos", 0.10, 0.35),
            "wave_position": trial.suggest_float("wave_position", 0.05, 0.25),
            "liquidity_sweep": trial.suggest_float("liquidity_sweep", 0.05, 0.25),
            "sr_reaction": trial.suggest_float("sr_reaction", 0.05, 0.20),
            "wave_ending": trial.suggest_float("wave_ending", 0.0, 0.15),
        }
        total = sum(raw.values())
        weights = {k: v / total for k, v in raw.items()}
        weights["catalyst"] = 0.0

        params = {
            "weights": weights,
            "threshold": trial.suggest_float("threshold", 0.25, 0.65),
            "sl_multiplier": trial.suggest_float("sl_multiplier", 1.0, 3.0),
            "tp_risk_reward": trial.suggest_float("tp_risk_reward", 1.5, 4.0),
            "swing_lookback": trial.suggest_int("swing_lookback", 3, 8),
        }

        engine = BacktestEngine(
            start_date=is_start_dt,
            end_date=is_end_dt,
            initial_capital=capital,
            config=config,
            pair=pair,
            params_override=params,
        )
        r = engine.run()
        if "error" in r:
            return -1000.0

        m = r["metrics"]
        if m["total_trades"] < MIN_IS_TRADES:
            return -500.0

        dd_pen = max(0.0, m["max_drawdown_pct"] - 0.10) * 500
        score = (
            m["expectancy_pips"] * 2.0
            + (m["win_rate"] - 0.4) * 50
            + min(m["profit_factor"], 3) * 10
            + m["total_trades"] * 0.1
            - dd_pen
        )
        for k in ("total_trades", "win_rate", "expectancy_pips", "total_pnl",
                  "max_drawdown_pct", "profit_factor", "sharpe_ratio"):
            trial.set_user_attr(k, m[k])
        return score

    return obj


def _normalize_best(best: optuna.trial.FrozenTrial) -> dict:
    raw = {k: best.params[k] for k in WEIGHT_KEYS}
    total = sum(raw.values())
    weights = {k: round(v / total, 4) for k, v in raw.items()}
    weights["catalyst"] = 0.0
    return {
        "weights": weights,
        "threshold": round(best.params["threshold"], 4),
        "sl_multiplier": round(best.params["sl_multiplier"], 4),
        "tp_risk_reward": round(best.params["tp_risk_reward"], 4),
        "swing_lookback": int(best.params["swing_lookback"]),
    }


def optimize_fold(
    pair: str,
    is_start: date,
    is_end: date,
    capital: float,
    config,
    n_trials: int = 30,
) -> dict:
    """Run Optuna on the IS window and return best params + IS metrics."""
    if (is_end - is_start).days < MIN_IS_DAYS:
        return {
            "mode": "baseline",
            "reason": f"IS < {MIN_IS_DAYS} days",
            "params": load_strategy_params(),
            "is_metrics": {},
            "score": None,
        }

    is_start_dt = datetime.combine(is_start, datetime.min.time())
    is_end_dt = datetime.combine(is_end, datetime.max.time())

    study = optuna.create_study(
        direction="maximize",
        sampler=TPESampler(seed=42),
    )
    study.optimize(
        _objective_factory(pair, is_start_dt, is_end_dt, capital, config),
        n_trials=n_trials,
        show_progress_bar=False,
    )

    try:
        best = study.best_trial
    except ValueError:
        return {
            "mode": "baseline",
            "reason": "no valid trials",
            "params": load_strategy_params(),
            "is_metrics": {},
            "score": None,
        }

    return {
        "mode": "optimized",
        "params": _normalize_best(best),
        "is_metrics": dict(best.user_attrs),
        "score": float(best.value),
        "n_trials": n_trials,
    }
