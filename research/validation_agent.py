"""ValidationAgent — applies overfit rejection rules and multi-metric gate.

Takes a CandidateResult from backtest_runner and assigns one of:

  PROMOTED_CANDIDATE             — passed everything, recommended for approval
  FLAGGED_NEEDS_MANUAL_REVIEW    — passed walk-forward but OOS degraded
  FLAGGED_INSUFFICIENT_OOS_SAMPLE
  FLAGGED_SUSPICIOUS             — improvement >30%, looks too good
  FLAGGED_PARAM_AT_BOUND
  REJECTED_WALK_FORWARD          — failed >=75% pass rate
  REJECTED_MULTI_METRIC          — PF up but win rate / drawdown degraded too much
  REJECTED_INSUFFICIENT_TRADES   — total trades across windows too low
  REJECTED_NO_IMPROVEMENT        — didn't beat both baselines on PF
  ERROR                          — backtest crashed

Each candidate must beat BOTH the rolling baseline AND the immutable anchor
baseline. The multi-metric gate prevents single-metric (PF only) gaming.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import PARAM_BOUNDS

from research.backtest_runner import (
    CandidateResult,
    WALK_FORWARD_PASS_RATIO,
    MIN_OOS_TRADES,
)

# Multi-metric tolerances (relative degradation allowed vs baseline)
MAX_WIN_RATE_DEGRADATION = 0.10       # 10%
MAX_DRAWDOWN_DEGRADATION = 0.10       # 10%
SUSPICIOUS_IMPROVEMENT_PCT = 0.30     # 30%
OOS_DEGRADATION_THRESHOLD = 0.25      # 25%

# When tests_this_quarter exceeds this, escalate the bar
ESCALATION_TESTS_THRESHOLD = 500
ESCALATED_MIN_PF_IMPROVEMENT = 0.15   # 15%


@dataclass
class Verdict:
    code: str
    reason: str
    delta_vs_anchor: dict | None = None
    delta_vs_rolling: dict | None = None


def _baseline_metrics(baseline: dict) -> dict:
    """Pull a normalised metrics dict out of a baseline entry."""
    m = baseline.get("metrics") or {}
    return {
        "profit_factor": m.get("profit_factor", 0.0),
        "win_rate": m.get("win_rate", 0.0),
        "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
        "expectancy_pips": m.get("expectancy_pips", 0.0),
        "trades": m.get("trades", 0),
    }


def _rolling_metrics(data: dict) -> dict:
    """Rolling baseline may not yet have metrics — fall back to anchor."""
    rb = data["rolling_baseline"]
    if rb.get("params") and "profit_factor" in (rb.get("params") or {}):
        return _baseline_metrics({"metrics": rb["params"]})
    # Fall back: rolling has same metrics as anchor until first promotion
    return _baseline_metrics(data["anchor_baseline"])


def _delta(candidate: float, baseline: float) -> float:
    """Relative change. Positive = candidate better (for PF/WR/expectancy).
    For drawdown, caller should invert."""
    if baseline == 0:
        return 0.0
    return (candidate - baseline) / baseline


def _check_param_at_bound(params: dict) -> str | None:
    """Returns name of any param sitting at PARAM_BOUNDS edge, else None."""
    name_map = {
        "threshold": "confluence_threshold",
        "sl_multiplier": "sl_atr_multiplier",
        "tp_risk_reward": "tp_risk_reward",
        "swing_lookback": "swing_lookback",
    }
    for pkey, bounds_key in name_map.items():
        if bounds_key not in PARAM_BOUNDS:
            continue
        lo, hi = PARAM_BOUNDS[bounds_key]
        v = params.get(pkey)
        if v is None:
            continue
        if v <= lo or v >= hi:
            return f"{pkey}={v} at bound ({lo},{hi})"
    return None


def _multi_metric_gate(
    cand_pf: float, cand_wr: float, cand_dd: float, cand_exp: float,
    base: dict, label: str,
) -> tuple[bool, str | None]:
    """Returns (passed, reason_if_failed) against one baseline."""
    if cand_exp < 0:
        return False, f"{label}: expectancy negative ({cand_exp})"
    if cand_pf <= base["profit_factor"]:
        return False, f"{label}: PF {cand_pf:.3f} <= baseline {base['profit_factor']:.3f}"
    # Win rate degradation
    if base["win_rate"] > 0:
        wr_drop = (base["win_rate"] - cand_wr) / base["win_rate"]
        if wr_drop > MAX_WIN_RATE_DEGRADATION:
            return False, f"{label}: WR degraded {wr_drop:.1%} > {MAX_WIN_RATE_DEGRADATION:.0%}"
    # Drawdown degradation (higher dd = worse)
    if base["max_drawdown_pct"] > 0:
        dd_increase = (cand_dd - base["max_drawdown_pct"]) / base["max_drawdown_pct"]
        if dd_increase > MAX_DRAWDOWN_DEGRADATION:
            return False, f"{label}: DD increased {dd_increase:.1%} > {MAX_DRAWDOWN_DEGRADATION:.0%}"
    return True, None


def evaluate(result: CandidateResult, data: dict) -> Verdict:
    """Apply all rules and return a Verdict."""
    if result.error:
        return Verdict(code="ERROR", reason=result.error)

    if not result.window_results:
        return Verdict(code="ERROR", reason="no window results")

    # Insufficient sample across windows
    total_trades = sum(w.val_metrics.get("trades", 0) for w in result.window_results)
    if total_trades < 50:
        return Verdict(
            code="REJECTED_INSUFFICIENT_TRADES",
            reason=f"total val trades across windows = {total_trades} < 50",
        )

    # Walk-forward pass rate
    if result.walk_forward_pass_rate < WALK_FORWARD_PASS_RATIO:
        n_passed = sum(1 for w in result.window_results if w.passed)
        n_total = len(result.window_results)
        return Verdict(
            code="REJECTED_WALK_FORWARD",
            reason=f"passed {n_passed}/{n_total} windows ({result.walk_forward_pass_rate:.0%}) < {WALK_FORWARD_PASS_RATIO:.0%}",
        )

    agg = result.aggregate
    cand_pf = agg.get("median_profit_factor", 0.0)
    cand_wr = agg.get("median_win_rate", 0.0)
    cand_dd = agg.get("median_max_drawdown_pct", 0.0)
    cand_exp = agg.get("median_expectancy_pips", 0.0)

    anchor = _baseline_metrics(data["anchor_baseline"])
    rolling = _rolling_metrics(data)

    # Multi-metric gate vs both baselines
    ok_anchor, reason_anchor = _multi_metric_gate(cand_pf, cand_wr, cand_dd, cand_exp, anchor, "anchor")
    ok_rolling, reason_rolling = _multi_metric_gate(cand_pf, cand_wr, cand_dd, cand_exp, rolling, "rolling")

    if not ok_anchor or not ok_rolling:
        combined_reason = "; ".join(r for r in (reason_anchor, reason_rolling) if r)
        # If any failure was due to non-PF metric (DD/WR/expectancy), it's a multi-metric reject.
        non_pf_keywords = ("DD", "WR", "expectancy")
        if any(k in combined_reason for k in non_pf_keywords):
            return Verdict(code="REJECTED_MULTI_METRIC", reason=combined_reason)
        return Verdict(code="REJECTED_NO_IMPROVEMENT", reason=combined_reason)

    # Escalation: if quarter has burned >500 tests, demand stronger PF improvement
    tests_this_quarter = data.get("budget", {}).get("tests_this_quarter", 0)
    if tests_this_quarter > ESCALATION_TESTS_THRESHOLD:
        pf_improvement = _delta(cand_pf, max(anchor["profit_factor"], rolling["profit_factor"]))
        if pf_improvement < ESCALATED_MIN_PF_IMPROVEMENT:
            return Verdict(
                code="REJECTED_NO_IMPROVEMENT",
                reason=f"escalated bar (>{ESCALATION_TESTS_THRESHOLD} tests this quarter): PF improvement {pf_improvement:.1%} < {ESCALATED_MIN_PF_IMPROVEMENT:.0%}",
            )

    delta_anchor = {
        "profit_factor_pct": _delta(cand_pf, anchor["profit_factor"]),
        "win_rate_pp": cand_wr - anchor["win_rate"],
        "drawdown_pct_change": _delta(cand_dd, anchor["max_drawdown_pct"]),
    }
    delta_rolling = {
        "profit_factor_pct": _delta(cand_pf, rolling["profit_factor"]),
        "win_rate_pp": cand_wr - rolling["win_rate"],
        "drawdown_pct_change": _delta(cand_dd, rolling["max_drawdown_pct"]),
    }

    # Suspicious improvement
    if delta_anchor["profit_factor_pct"] > SUSPICIOUS_IMPROVEMENT_PCT:
        return Verdict(
            code="FLAGGED_SUSPICIOUS",
            reason=f"PF improvement vs anchor {delta_anchor['profit_factor_pct']:.1%} > {SUSPICIOUS_IMPROVEMENT_PCT:.0%}",
            delta_vs_anchor=delta_anchor,
            delta_vs_rolling=delta_rolling,
        )

    # Param at bound
    bound_hit = _check_param_at_bound(result.candidate.params)
    if bound_hit:
        return Verdict(
            code="FLAGGED_PARAM_AT_BOUND",
            reason=bound_hit,
            delta_vs_anchor=delta_anchor,
            delta_vs_rolling=delta_rolling,
        )

    # OOS gate
    oos = result.oos_metrics
    if not oos:
        return Verdict(
            code="FLAGGED_NEEDS_MANUAL_REVIEW",
            reason="walk-forward passed but no OOS result",
            delta_vs_anchor=delta_anchor,
            delta_vs_rolling=delta_rolling,
        )
    if oos.get("trades", 0) < MIN_OOS_TRADES:
        return Verdict(
            code="FLAGGED_INSUFFICIENT_OOS_SAMPLE",
            reason=f"oos trades {oos.get('trades', 0)} < {MIN_OOS_TRADES}",
            delta_vs_anchor=delta_anchor,
            delta_vs_rolling=delta_rolling,
        )
    oos_pf = oos.get("profit_factor", 0.0)
    if cand_pf > 0:
        oos_drop = (cand_pf - oos_pf) / cand_pf
        if oos_drop > OOS_DEGRADATION_THRESHOLD:
            return Verdict(
                code="FLAGGED_NEEDS_MANUAL_REVIEW",
                reason=f"OOS PF {oos_pf:.3f} degraded {oos_drop:.1%} vs walk-forward median {cand_pf:.3f}",
                delta_vs_anchor=delta_anchor,
                delta_vs_rolling=delta_rolling,
            )

    return Verdict(
        code="PROMOTED_CANDIDATE",
        reason=f"passed all gates: PF {cand_pf:.3f} (anchor +{delta_anchor['profit_factor_pct']:.1%}), OOS PF {oos_pf:.3f}",
        delta_vs_anchor=delta_anchor,
        delta_vs_rolling=delta_rolling,
    )
