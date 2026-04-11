"""Load strategy parameters from the single source of truth: optimized_params.json.

Both the backtest engine and the PineScript generator read from this file,
ensuring they always stay in sync.
"""
import json
from pathlib import Path

from config.settings import (
    CONFLUENCE_WEIGHTS, CONFLUENCE_THRESHOLD, SL_ATR_MULTIPLIER, TP_RISK_REWARD,
    REGIME_FILTER_ENABLED, REGIME_ADX_THRESHOLD,
    REGIME_PARAMS_ENABLED, ATR_VOLATILITY_THRESHOLD, DEFAULT_REGIME_PARAMS,
    NEWS_FILTER_ENABLED, NEWS_BLOCK_BEFORE_MINS, NEWS_BLOCK_AFTER_MINS,
    LEARNER_ENABLED,
)

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
        "regime_filter_enabled": data.get("regime_filter_enabled", REGIME_FILTER_ENABLED),
        "regime_adx_threshold": data.get("regime_adx_threshold", REGIME_ADX_THRESHOLD),
        "signal_model_enabled": data.get("signal_model_enabled", False),
        "signal_model_min_confidence": data.get("signal_model_min_confidence", 0.5),
        "news_filter_enabled": data.get("news_filter_enabled", NEWS_FILTER_ENABLED),
        "news_block_before_mins": data.get("news_block_before_mins", NEWS_BLOCK_BEFORE_MINS),
        "news_block_after_mins": data.get("news_block_after_mins", NEWS_BLOCK_AFTER_MINS),
        "auto_promote_enabled": data.get("auto_promote_enabled", False),
        "learner_enabled": data.get("learner_enabled", LEARNER_ENABLED),
        "regime_params_enabled": data.get("regime_params_enabled", REGIME_PARAMS_ENABLED),
        "atr_volatility_threshold": data.get("atr_volatility_threshold", ATR_VOLATILITY_THRESHOLD),
        "regime_params": data.get("regime_params", DEFAULT_REGIME_PARAMS.copy()),
    }


def save_strategy_params(params: dict, backtest_results: dict | None = None):
    """Save strategy parameters to optimized_params.json."""
    data = {
        "weights": params["weights"],
        "threshold": params["threshold"],
        "sl_multiplier": params["sl_multiplier"],
        "tp_risk_reward": params["tp_risk_reward"],
        "swing_lookback": params["swing_lookback"],
        "regime_filter_enabled": params.get("regime_filter_enabled", REGIME_FILTER_ENABLED),
        "regime_adx_threshold": params.get("regime_adx_threshold", REGIME_ADX_THRESHOLD),
        "signal_model_enabled": params.get("signal_model_enabled", False),
        "signal_model_min_confidence": params.get("signal_model_min_confidence", 0.5),
        "news_filter_enabled": params.get("news_filter_enabled", NEWS_FILTER_ENABLED),
        "news_block_before_mins": params.get("news_block_before_mins", NEWS_BLOCK_BEFORE_MINS),
        "news_block_after_mins": params.get("news_block_after_mins", NEWS_BLOCK_AFTER_MINS),
        "auto_promote_enabled": params.get("auto_promote_enabled", False),
        "learner_enabled": params.get("learner_enabled", LEARNER_ENABLED),
        "regime_params_enabled": params.get("regime_params_enabled", REGIME_PARAMS_ENABLED),
        "atr_volatility_threshold": params.get("atr_volatility_threshold", ATR_VOLATILITY_THRESHOLD),
        "regime_params": params.get("regime_params", DEFAULT_REGIME_PARAMS.copy()),
    }
    if backtest_results:
        data["backtest_results"] = backtest_results

    with open(PARAMS_FILE, "w") as f:
        json.dump(data, f, indent=2)
