"""Parameter optimizer - finds profitable settings via backtesting."""
import logging
import json
from datetime import datetime

import optuna
from optuna.samplers import TPESampler

from backtest.engine import BacktestEngine
from config import settings
from storage.database import BacktestRecord, get_session

logger = logging.getLogger(__name__)

# Silence optuna logs
optuna.logging.set_verbosity(optuna.logging.WARNING)


def objective(trial: optuna.Trial) -> float:
    """Optuna objective function - maximize expectancy."""

    # Sample parameters
    weights = {
        "htf_bias": trial.suggest_float("htf_bias", 0.10, 0.40),
        "bos": trial.suggest_float("bos", 0.10, 0.35),
        "wave_position": trial.suggest_float("wave_position", 0.05, 0.25),
        "liquidity_sweep": trial.suggest_float("liquidity_sweep", 0.05, 0.25),
        "sr_reaction": trial.suggest_float("sr_reaction", 0.05, 0.20),
        "catalyst": 0.0,  # No news data yet
        "wave_ending": trial.suggest_float("wave_ending", 0.0, 0.15),
    }

    # Normalize weights to sum to 1
    total = sum(weights.values())
    weights = {k: v / total for k, v in weights.items()}

    threshold = trial.suggest_float("threshold", 0.25, 0.65)
    sl_mult = trial.suggest_float("sl_multiplier", 1.0, 3.0)
    tp_rr = trial.suggest_float("tp_risk_reward", 1.5, 4.0)
    swing_lb = trial.suggest_int("swing_lookback", 3, 8)

    # Apply parameters
    settings.CONFLUENCE_THRESHOLD = threshold
    settings.SL_ATR_MULTIPLIER = sl_mult
    settings.TP_RISK_REWARD = tp_rr
    settings.SWING_LOOKBACK = swing_lb

    # Run backtest
    engine = BacktestEngine(
        start_date=datetime(2025, 1, 1),
        end_date=datetime(2025, 12, 31),
        initial_capital=100_000,
    )
    results = engine.run(weights=weights)

    if "error" in results:
        return -1000

    metrics = results["metrics"]
    total_trades = metrics["total_trades"]

    # Need minimum trades for statistical significance
    if total_trades < 5:
        return -500

    # Composite score: balance expectancy, win rate, and drawdown
    expectancy = metrics["expectancy_pips"]
    win_rate = metrics["win_rate"]
    max_dd = metrics["max_drawdown_pct"]
    profit_factor = metrics["profit_factor"]

    # Penalize high drawdown
    dd_penalty = max(0, max_dd - 0.10) * 500

    # Score: weighted combination
    score = (
        expectancy * 2.0 +           # Reward positive expectancy
        (win_rate - 0.4) * 50 +      # Bonus for win rate above 40%
        min(profit_factor, 3) * 10 + # Reward profit factor (capped)
        total_trades * 0.5 -          # Slight bonus for more trades
        dd_penalty                    # Penalize drawdown
    )

    trial.set_user_attr("total_trades", total_trades)
    trial.set_user_attr("win_rate", win_rate)
    trial.set_user_attr("expectancy", expectancy)
    trial.set_user_attr("total_pnl", metrics["total_pnl"])
    trial.set_user_attr("max_dd", max_dd)
    trial.set_user_attr("profit_factor", profit_factor)

    return score


def run_optimization(n_trials: int = 50) -> dict:
    """Run parameter optimization and return best parameters."""
    logger.info(f"Starting optimization with {n_trials} trials...")

    study = optuna.create_study(
        direction="maximize",
        sampler=TPESampler(seed=42),
        study_name="eurusd_optimizer",
    )

    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_trial
    logger.info(f"\n=== Best Trial #{best.number} ===")
    logger.info(f"Score: {best.value:.2f}")
    logger.info(f"Trades: {best.user_attrs['total_trades']}")
    logger.info(f"Win Rate: {best.user_attrs['win_rate']:.1%}")
    logger.info(f"Expectancy: {best.user_attrs['expectancy']:.1f} pips")
    logger.info(f"P&L: £{best.user_attrs['total_pnl']:.2f}")
    logger.info(f"Max DD: {best.user_attrs['max_dd']:.1%}")
    logger.info(f"Profit Factor: {best.user_attrs['profit_factor']:.2f}")
    logger.info(f"Parameters: {best.params}")

    # Build normalized weights
    weight_keys = ["htf_bias", "bos", "wave_position", "liquidity_sweep", "sr_reaction", "wave_ending"]
    raw_weights = {k: best.params[k] for k in weight_keys}
    raw_weights["catalyst"] = 0.0
    total = sum(raw_weights.values())
    normalized_weights = {k: round(v / total, 4) for k, v in raw_weights.items()}

    result = {
        "score": best.value,
        "weights": normalized_weights,
        "threshold": best.params["threshold"],
        "sl_multiplier": best.params["sl_multiplier"],
        "tp_risk_reward": best.params["tp_risk_reward"],
        "swing_lookback": best.params["swing_lookback"],
        "metrics": {
            "total_trades": best.user_attrs["total_trades"],
            "win_rate": best.user_attrs["win_rate"],
            "expectancy": best.user_attrs["expectancy"],
            "total_pnl": best.user_attrs["total_pnl"],
            "max_dd": best.user_attrs["max_dd"],
            "profit_factor": best.user_attrs["profit_factor"],
        },
        "all_trials": [
            {
                "number": t.number,
                "score": t.value,
                "trades": t.user_attrs.get("total_trades", 0),
                "win_rate": t.user_attrs.get("win_rate", 0),
                "pnl": t.user_attrs.get("total_pnl", 0),
            }
            for t in study.trials
            if t.value is not None and t.value > -100
        ],
    }

    # Save best params
    _save_optimized_params(result)

    return result


def _save_optimized_params(result: dict):
    """Save optimized parameters to a file and database."""
    from config.settings import PROJECT_ROOT

    # Save to JSON file
    params_file = PROJECT_ROOT / "config" / "optimized_params.json"
    with open(params_file, "w") as f:
        json.dump(result, f, indent=2, default=str)

    logger.info(f"Saved optimized params to {params_file}")


def apply_optimized_params():
    """Load and apply optimized parameters."""
    from config.settings import PROJECT_ROOT

    params_file = PROJECT_ROOT / "config" / "optimized_params.json"
    if not params_file.exists():
        logger.warning("No optimized params found, using defaults")
        return False

    with open(params_file) as f:
        result = json.load(f)

    settings.CONFLUENCE_WEIGHTS = result["weights"]
    settings.CONFLUENCE_THRESHOLD = result["threshold"]
    settings.SL_ATR_MULTIPLIER = result["sl_multiplier"]
    settings.TP_RISK_REWARD = result["tp_risk_reward"]
    settings.SWING_LOOKBACK = int(result["swing_lookback"])

    logger.info(
        f"Applied optimized params: threshold={result['threshold']:.2f} "
        f"SL={result['sl_multiplier']:.1f}x TP={result['tp_risk_reward']:.1f}:1"
    )
    return True
