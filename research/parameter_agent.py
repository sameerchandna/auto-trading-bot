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
}

# Structural params — high impact, capped per run
STRUCTURAL_PARAMS = {"sl_method", "swing_lookback"}

SL_METHOD_CHOICES = ["atr", "structure"]


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
    }
    pkey = key_map[name]
    current = base[pkey]
    new_value = _clamp(current + direction * step, lo, hi)
    if name == "swing_lookback":
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

    return pool


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

    rng = random.Random(seed)
    rng.shuffle(pool)

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
