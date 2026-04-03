"""Load strategy parameters from the single source of truth: optimized_params.json.

Both the backtest engine and the PineScript generator read from this file,
ensuring they always stay in sync.
"""
import json
from pathlib import Path

from config.settings import CONFLUENCE_WEIGHTS, CONFLUENCE_THRESHOLD, SL_ATR_MULTIPLIER, TP_RISK_REWARD

PARAMS_FILE = Path(__file__).parent / "optimized_params.json"


def load_strategy_params() -> dict:
    """Load strategy parameters from optimized_params.json.

    Returns a dict with keys: weights, threshold, sl_multiplier,
    tp_risk_reward, swing_lookback.

    Falls back to settings.py defaults if the file is missing.
    """
    if not PARAMS_FILE.exists():
        return {
            "weights": CONFLUENCE_WEIGHTS.copy(),
            "threshold": CONFLUENCE_THRESHOLD,
            "sl_multiplier": SL_ATR_MULTIPLIER,
            "tp_risk_reward": TP_RISK_REWARD,
            "swing_lookback": 5,
        }

    with open(PARAMS_FILE) as f:
        data = json.load(f)

    return {
        "weights": data.get("weights", CONFLUENCE_WEIGHTS.copy()),
        "threshold": data.get("threshold", CONFLUENCE_THRESHOLD),
        "sl_multiplier": data.get("sl_multiplier", SL_ATR_MULTIPLIER),
        "tp_risk_reward": data.get("tp_risk_reward", TP_RISK_REWARD),
        "swing_lookback": data.get("swing_lookback", 5),
    }


def save_strategy_params(params: dict, backtest_results: dict | None = None):
    """Save strategy parameters to optimized_params.json."""
    data = {
        "weights": params["weights"],
        "threshold": params["threshold"],
        "sl_multiplier": params["sl_multiplier"],
        "tp_risk_reward": params["tp_risk_reward"],
        "swing_lookback": params["swing_lookback"],
    }
    if backtest_results:
        data["backtest_results"] = backtest_results

    with open(PARAMS_FILE, "w") as f:
        json.dump(data, f, indent=2)
