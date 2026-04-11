"""ParameterAgent — generates candidate parameter combinations for daily research.

Responsibilities:
  - Read PARAM_BOUNDS from config.settings
  - Generate candidate combinations by perturbing the rolling baseline
  - Enforce daily budget cap (max 5/day)
  - Skip candidates that are: already tested, currently blacklisted, or
    identical to a baseline
  - Limit structural-param mutations per run (max 2)
  - Return ordered list of candidates ready for the BacktestRunner
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable

from config.settings import PARAM_BOUNDS

from research import history

# Numeric tunables (tested daily) — name -> step size
NUMERIC_STEPS = {
    "confluence_threshold": 0.05,
    "sl_atr_multiplier": 0.25,
    "tp_risk_reward": 0.25,
    "swing_lookback": 1,
    "regime_adx_threshold": 2.5,
    "signal_model_min_confidence": 0.05,
    "news_block_before_mins": 5,
    "news_block_after_mins": 5,
    "atr_volatility_threshold": 5.0,
}

# Structural params — high impact, capped per run
STRUCTURAL_PARAMS = {"sl_method", "swing_lookback", "regime_filter_enabled", "regime_params_enabled", "signal_model_enabled", "news_filter_enabled"}

# Per-regime overridable params and their step sizes
REGIME_OVERRIDE_PARAMS = {
    "threshold": 0.05,
    "sl_multiplier": 0.25,
    "tp_risk_reward": 0.25,
}

# Regimes that get per-regime overrides (trending uses base params)
OVERRIDE_REGIMES = ["ranging", "volatile"]

SL_METHOD_CHOICES = ["atr", "structure"]

# Map mutation param keys to confluence factor names (for feature importance biasing)
# A mutation that changes a weight related to a high-importance factor gets priority
_PARAM_TO_FACTOR = {
    "threshold": None,           # affects all factors equally
    "sl_multiplier": None,
    "tp_risk_reward": None,
    "swing_lookback": "wave_position",
    "regime_adx_threshold": "adx",
    "signal_model_min_confidence": None,
    "news_block_before_mins": None,
    "news_block_after_mins": None,
    "sl_method": None,
    "regime_filter": "adx",
    "regime_params": None,
    "atr_volatility_threshold": "atr",
    "signal_model": None,
    "news_filter": None,
}


@dataclass
class Candidate:
    params: dict
    params_hash: str
    mutation_summary: str  # human-readable description, e.g. "threshold 0.45->0.50"

    def to_dict(self) -> dict:
        return {
            "params": self.params,
            "params_hash": self.params_hash,
            "mutation": self.mutation_summary,
        }


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _round(value: float, step: float) -> float:
    # avoid float drift
    return round(round(value / step) * step, 6)


def _baseline_params(data: dict) -> dict:
    """Use rolling baseline as the starting point for mutations."""
    rb = history.get_rolling_baseline(data)
    if not rb.get("params"):
        data = history.sync_rolling_baseline(data)
        rb = history.get_rolling_baseline(data)
    # Deep copy via json roundtrip is overkill here; manual copy is fine
    base = dict(rb["params"])
    base["weights"] = dict(base["weights"])
    base.setdefault("sl_method", "atr")
    base.setdefault("regime_filter_enabled", False)
    base.setdefault("regime_adx_threshold", 25.0)
    base.setdefault("signal_model_enabled", False)
    base.setdefault("signal_model_min_confidence", 0.5)
    base.setdefault("news_filter_enabled", False)
    base.setdefault("news_block_before_mins", 30)
    base.setdefault("news_block_after_mins", 15)
    base.setdefault("regime_params_enabled", False)
    base.setdefault("atr_volatility_threshold", 80.0)
    # Deep copy regime_params to avoid mutation leakage
    rp = base.get("regime_params", {"trending": {}, "ranging": {}, "volatile": {}})
    base["regime_params"] = {k: dict(v) for k, v in rp.items()}
    return base


def _perturb_numeric(base: dict, name: str, direction: int) -> tuple[dict, str] | None:
    """Bump a numeric param up (+1) or down (-1) by one step, clamped to PARAM_BOUNDS."""
    bounds_key = name
    if bounds_key not in PARAM_BOUNDS:
        return None
    lo, hi = PARAM_BOUNDS[bounds_key]
    step = NUMERIC_STEPS[name]

    # Map agent name -> baseline param key
    key_map = {
        "confluence_threshold": "threshold",
        "sl_atr_multiplier": "sl_multiplier",
        "tp_risk_reward": "tp_risk_reward",
        "swing_lookback": "swing_lookback",
        "regime_adx_threshold": "regime_adx_threshold",
        "signal_model_min_confidence": "signal_model_min_confidence",
        "news_block_before_mins": "news_block_before_mins",
        "news_block_after_mins": "news_block_after_mins",
        "atr_volatility_threshold": "atr_volatility_threshold",
    }
    pkey = key_map[name]
    current = base[pkey]
    new_value = _clamp(current + direction * step, lo, hi)
    if name in ("swing_lookback", "news_block_before_mins", "news_block_after_mins"):
        new_value = int(round(new_value))
    if new_value == current:
        return None

    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params[pkey] = new_value
    summary = f"{pkey} {current}->{new_value}"
    return new_params, summary


def _perturb_sl_method(base: dict) -> tuple[dict, str] | None:
    current = base.get("sl_method", "atr")
    other = "structure" if current == "atr" else "atr"
    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params["sl_method"] = other
    return new_params, f"sl_method {current}->{other}"


def _toggle_regime_filter(base: dict) -> tuple[dict, str] | None:
    current = base.get("regime_filter_enabled", False)
    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params["regime_filter_enabled"] = not current
    state = "ON" if not current else "OFF"
    return new_params, f"regime_filter {state}"


def _toggle_signal_model(base: dict) -> tuple[dict, str] | None:
    current = base.get("signal_model_enabled", False)
    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params["signal_model_enabled"] = not current
    state = "ON" if not current else "OFF"
    return new_params, f"signal_model {state}"


def _toggle_news_filter(base: dict) -> tuple[dict, str] | None:
    current = base.get("news_filter_enabled", False)
    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params["news_filter_enabled"] = not current
    state = "ON" if not current else "OFF"
    return new_params, f"news_filter {state}"


def _toggle_regime_params(base: dict) -> tuple[dict, str] | None:
    current = base.get("regime_params_enabled", False)
    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params["regime_params"] = {k: dict(v) for k, v in base.get("regime_params", {}).items()}
    new_params["regime_params_enabled"] = not current
    state = "ON" if not current else "OFF"
    return new_params, f"regime_params {state}"


def _perturb_regime_param(
    base: dict, regime: str, param: str, direction: int,
) -> tuple[dict, str] | None:
    """Bump a per-regime override param up or down by one step.

    Uses PARAM_BOUNDS for the underlying param (e.g. confluence_threshold bounds
    for regime_params.ranging.threshold).
    """
    # Map override param name to PARAM_BOUNDS key
    bounds_map = {
        "threshold": "confluence_threshold",
        "sl_multiplier": "sl_atr_multiplier",
        "tp_risk_reward": "tp_risk_reward",
    }
    bounds_key = bounds_map.get(param)
    if bounds_key not in PARAM_BOUNDS:
        return None
    lo, hi = PARAM_BOUNDS[bounds_key]
    step = REGIME_OVERRIDE_PARAMS[param]

    regime_params = base.get("regime_params", {})
    overrides = regime_params.get(regime, {})

    # If no override set yet, start from the base param value
    base_key_map = {"threshold": "threshold", "sl_multiplier": "sl_multiplier", "tp_risk_reward": "tp_risk_reward"}
    current = overrides.get(param, base.get(base_key_map[param], lo))
    new_value = _clamp(current + direction * step, lo, hi)
    if new_value == current:
        return None

    new_params = dict(base)
    new_params["weights"] = dict(base["weights"])
    new_params["regime_params"] = {k: dict(v) for k, v in regime_params.items()}
    new_params["regime_params"][regime] = dict(overrides)
    new_params["regime_params"][regime][param] = new_value
    summary = f"{regime}.{param} {current}->{new_value}"
    return new_params, summary


def _candidate_pool(base: dict) -> list[tuple[dict, str, bool]]:
    """Build the full pool of single-mutation candidates.

    Returns list of (params, mutation_summary, is_structural).
    """
    pool: list[tuple[dict, str, bool]] = []

    # Numeric ± steps
    for name in ("confluence_threshold", "sl_atr_multiplier", "tp_risk_reward"):
        for direction in (-1, +1):
            res = _perturb_numeric(base, name, direction)
            if res is not None:
                params, summary = res
                pool.append((params, summary, False))

    # Regime ADX threshold ± steps (only when regime filter is enabled)
    if base.get("regime_filter_enabled", False):
        for direction in (-1, +1):
            res = _perturb_numeric(base, "regime_adx_threshold", direction)
            if res is not None:
                params, summary = res
                pool.append((params, summary, False))

    # Structural mutations
    for direction in (-1, +1):
        res = _perturb_numeric(base, "swing_lookback", direction)
        if res is not None:
            params, summary = res
            pool.append((params, summary, True))

    sl_res = _perturb_sl_method(base)
    if sl_res is not None:
        params, summary = sl_res
        pool.append((params, summary, True))

    regime_res = _toggle_regime_filter(base)
    if regime_res is not None:
        params, summary = regime_res
        pool.append((params, summary, True))

    # Signal model toggle (only if model file exists)
    from analysis.signal_model import MODEL_PATH
    if MODEL_PATH.exists():
        model_res = _toggle_signal_model(base)
        if model_res is not None:
            params, summary = model_res
            pool.append((params, summary, True))

        # Model confidence threshold ± steps (only when model is enabled)
        if base.get("signal_model_enabled", False):
            for direction in (-1, +1):
                res = _perturb_numeric(base, "signal_model_min_confidence", direction)
                if res is not None:
                    params, summary = res
                    pool.append((params, summary, False))

    # News filter toggle
    news_res = _toggle_news_filter(base)
    if news_res is not None:
        params, summary = news_res
        pool.append((params, summary, True))

    # News block window ± steps (only when news filter is enabled)
    if base.get("news_filter_enabled", False):
        for name in ("news_block_before_mins", "news_block_after_mins"):
            for direction in (-1, +1):
                res = _perturb_numeric(base, name, direction)
                if res is not None:
                    params, summary = res
                    pool.append((params, summary, False))

    # Regime-aware param switching toggle
    rp_res = _toggle_regime_params(base)
    if rp_res is not None:
        params, summary = rp_res
        pool.append((params, summary, True))

    # ATR volatility threshold ± steps (only when regime_params is enabled)
    if base.get("regime_params_enabled", False):
        for direction in (-1, +1):
            res = _perturb_numeric(base, "atr_volatility_threshold", direction)
            if res is not None:
                params, summary = res
                pool.append((params, summary, False))

        # Per-regime param overrides (ranging + volatile)
        for regime in OVERRIDE_REGIMES:
            for param in REGIME_OVERRIDE_PARAMS:
                for direction in (-1, +1):
                    res = _perturb_regime_param(base, regime, param, direction)
                    if res is not None:
                        params, summary = res
                        pool.append((params, summary, False))

    return pool


def _importance_weight(summary: str, data: dict) -> float:
    """Return a priority weight for a mutation based on feature importance data.

    Higher weight = more likely to be selected. Mutations touching factors
    with high absolute point-biserial correlation get boosted.
    Falls back to 1.0 (neutral) when no importance data exists.
    """
    # Extract the param key from the mutation summary (e.g. "threshold 0.45->0.50")
    param_key = summary.split()[0] if summary else ""
    factor = _PARAM_TO_FACTOR.get(param_key)
    if factor is None:
        return 1.0

    # Check all pairs for importance data, use max absolute correlation
    insights = data.get("strategy_insights", {})
    max_corr = 0.0
    for key, asset_data in insights.items():
        if key == "learner_proposals_history":
            continue
        fi = asset_data.get("feature_importance", {})
        factors = fi.get("factors", {})
        factor_data = factors.get(factor, {})
        corr = abs(factor_data.get("point_biserial", 0.0))
        max_corr = max(max_corr, corr)

    if max_corr == 0.0:
        return 1.0

    # Scale: correlation of 0.1 -> weight 2.0, 0.2 -> 3.0, etc.
    return 1.0 + max_corr * 10.0


def generate_candidates(
    data: dict | None = None,
    budget: int | None = None,
    seed: int | None = None,
) -> list[Candidate]:
    """Generate up to `budget` candidates for today's research run.

    Filters out:
      - candidates already in test history
      - candidates currently blacklisted (within 30-day cooldown)
      - candidates whose hash matches anchor or rolling baseline

    Caps structural-param mutations at max_structural_params_per_run (default 2).
    Uses feature importance data (when available) to bias selection toward
    high-impact factor mutations.
    """
    if data is None:
        data = history.load()
        data = history.sync_rolling_baseline(data)

    budget_cfg = data.get("budget", {})
    if budget is None:
        budget = int(budget_cfg.get("max_combinations_per_day", 5))
    max_structural = int(budget_cfg.get("max_structural_params_per_run", 2))

    base = _baseline_params(data)
    anchor_hash = history.get_anchor_baseline(data).get("params_hash")
    rolling_hash = history.get_rolling_baseline(data).get("params_hash")

    pool = _candidate_pool(base)

    # Importance-biased shuffle: assign weights, then sort by weight * random
    rng = random.Random(seed)
    weighted = [
        (params, summary, is_struct, _importance_weight(summary, data) * rng.random())
        for params, summary, is_struct in pool
    ]
    weighted.sort(key=lambda x: x[3], reverse=True)
    pool = [(p, s, st) for p, s, st, _ in weighted]

    selected: list[Candidate] = []
    structural_used = 0

    for params, summary, is_structural in pool:
        if len(selected) >= budget:
            break
        if is_structural and structural_used >= max_structural:
            continue

        h = history.hash_params(params)
        if h in (anchor_hash, rolling_hash):
            continue
        if history.already_tested(data, h):
            continue
        if history.is_blacklisted(data, h):
            continue
        if any(c.params_hash == h for c in selected):
            continue

        selected.append(Candidate(params=params, params_hash=h, mutation_summary=summary))
        if is_structural:
            structural_used += 1

    return selected


def summarise(candidates: Iterable[Candidate]) -> str:
    lines = [f"Generated {len(list(candidates))} candidates"]
    return "\n".join(lines)
