"""BacktestRunner — runs candidate params across walk-forward windows + OOS.

For each candidate:
  1. Run on each non-overlapping walk-forward window (IS + VAL split per window)
  2. Compute per-window metrics, aggregate medians
  3. Determine pass/fail per window (validation period must beat baseline)
  4. If >=75% of windows pass, run OOS gate
  5. Return a result dict ready for validation_agent

The runner deliberately does NOT mutate optimized_params.json — it passes
candidate params directly via BacktestEngine(params_override=...).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backtest.config import BacktestConfig, BASELINE
from backtest.engine import BacktestEngine

from research.parameter_agent import Candidate
from research import history

# Minimum pass rate across walk-forward windows
WALK_FORWARD_PASS_RATIO = 0.75
# Minimum trades for a window's result to count
MIN_TRADES_PER_WINDOW = 50
# Minimum trades for OOS sample to be considered meaningful
MIN_OOS_TRADES = 30


@dataclass
class WindowResult:
    window_id: str
    period: str  # "IS:2023-01..2023-06 VAL:2023-07..2023-12"
    is_metrics: dict
    val_metrics: dict
    passed: bool
    fail_reason: str | None = None


@dataclass
class CandidateResult:
    candidate: Candidate
    window_results: list[WindowResult] = field(default_factory=list)
    oos_metrics: dict | None = None
    walk_forward_pass_rate: float = 0.0
    aggregate: dict = field(default_factory=dict)
    error: str | None = None

    def to_history_entry(self, test_id: str, tested_at: str) -> dict:
        return {
            "id": test_id,
            "params_hash": self.candidate.params_hash,
            "params": self.candidate.params,
            "mutation": self.candidate.mutation_summary,
            "tested_at": tested_at,
            "windows": [
                {
                    "window_id": w.window_id,
                    "period": w.period,
                    "is": w.is_metrics,
                    "val": w.val_metrics,
                    "passed": w.passed,
                    "fail_reason": w.fail_reason,
                }
                for w in self.window_results
            ],
            "walk_forward_pass_rate": self.walk_forward_pass_rate,
            "aggregate": self.aggregate,
            "oos": self.oos_metrics,
            "error": self.error,
            # verdict / approval populated by validation_agent
        }


def _build_config(candidate: Candidate) -> BacktestConfig:
    """Mirror BASELINE but apply candidate's sl_method."""
    sl_method = candidate.params.get("sl_method", "atr")
    return BacktestConfig(
        block_hours=BASELINE.block_hours,
        block_days=BASELINE.block_days,
        cooldown_after_losses=BASELINE.cooldown_after_losses,
        min_score=BASELINE.min_score,
        exclude_timeframes=BASELINE.exclude_timeframes,
        sl_method=sl_method,
    )


def _extract_metrics(raw: dict) -> dict:
    """Normalise metric keys from BacktestEngine output.

    BacktestEngine.run() returns {"metrics": {...}, "trades": int, ...}.
    The inner metrics dict has total_trades, win_rate, profit_factor,
    expectancy_pips, max_drawdown_pct, sharpe_ratio, total_pnl, etc.
    """
    if not raw or "error" in raw:
        return {"trades": 0, "error": raw.get("error", "no result") if raw else "no result"}
    m = raw.get("metrics") or {}
    return {
        "trades": m.get("total_trades", 0),
        "win_rate": m.get("win_rate", 0.0),
        "profit_factor": m.get("profit_factor", 0.0),
        "expectancy_pips": m.get("expectancy_pips", 0.0),
        "max_drawdown_pct": m.get("max_drawdown_pct", 0.0),
        "total_pnl": m.get("total_pnl", 0.0),
        "sharpe_ratio": m.get("sharpe_ratio", 0.0),
    }


def _run_one(
    candidate: Candidate,
    start: datetime,
    end: datetime,
    pair: str = "EURUSD",
) -> dict:
    config = _build_config(candidate)
    engine = BacktestEngine(
        start_date=start,
        end_date=end,
        config=config,
        pair=pair,
        params_override=candidate.params,
    )
    raw = engine.run()
    return _extract_metrics(raw)


def _window_passes(val_metrics: dict, baseline_pf: float) -> tuple[bool, str | None]:
    """A walk-forward window passes if:
       - it has enough trades
       - validation profit factor > baseline profit factor
    """
    if val_metrics.get("trades", 0) < MIN_TRADES_PER_WINDOW:
        return False, f"trades={val_metrics.get('trades', 0)} < {MIN_TRADES_PER_WINDOW}"
    val_pf = val_metrics.get("profit_factor", 0.0)
    if val_pf <= baseline_pf:
        return False, f"val_pf={val_pf:.3f} <= baseline_pf={baseline_pf:.3f}"
    return True, None


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def _aggregate_windows(window_results: list[WindowResult]) -> dict:
    val_pfs = [w.val_metrics.get("profit_factor", 0.0) for w in window_results if w.val_metrics.get("trades", 0) > 0]
    val_wrs = [w.val_metrics.get("win_rate", 0.0) for w in window_results if w.val_metrics.get("trades", 0) > 0]
    val_dds = [w.val_metrics.get("max_drawdown_pct", 0.0) for w in window_results if w.val_metrics.get("trades", 0) > 0]
    val_exps = [w.val_metrics.get("expectancy_pips", 0.0) for w in window_results if w.val_metrics.get("trades", 0) > 0]
    return {
        "median_profit_factor": _median(val_pfs),
        "median_win_rate": _median(val_wrs),
        "median_max_drawdown_pct": _median(val_dds),
        "median_expectancy_pips": _median(val_exps),
        "windows_with_data": len(val_pfs),
    }


def run_candidate(
    candidate: Candidate,
    data: dict,
    pair: str = "EURUSD",
    run_oos: bool = True,
) -> CandidateResult:
    """Run one candidate across all walk-forward windows + optional OOS gate."""
    rotation = data["rotation"]
    windows_cfg = rotation["walk_forward_windows"]

    # Use rolling baseline metrics as the reference for "did this window pass?"
    rolling = data["rolling_baseline"]
    baseline_pf = (rolling.get("params") or {}).get("profit_factor")
    if baseline_pf is None:
        # fall back to anchor metrics
        baseline_pf = data["anchor_baseline"]["metrics"]["profit_factor"]

    result = CandidateResult(candidate=candidate)

    for w in windows_cfg:
        is_start = datetime.fromisoformat(w["is_start"])
        is_end = datetime.fromisoformat(w["is_end"])
        val_start = datetime.fromisoformat(w["val_start"])
        val_end = datetime.fromisoformat(w["val_end"])

        try:
            is_metrics = _run_one(candidate, is_start, is_end, pair=pair)
            val_metrics = _run_one(candidate, val_start, val_end, pair=pair)
        except Exception as exc:
            result.error = f"window {w['id']}: {exc}"
            return result

        passed, reason = _window_passes(val_metrics, baseline_pf)
        result.window_results.append(WindowResult(
            window_id=w["id"],
            period=f"IS:{w['is_start']}..{w['is_end']} VAL:{w['val_start']}..{w['val_end']}",
            is_metrics=is_metrics,
            val_metrics=val_metrics,
            passed=passed,
            fail_reason=reason,
        ))

    n_windows = len(result.window_results)
    n_passed = sum(1 for w in result.window_results if w.passed)
    result.walk_forward_pass_rate = (n_passed / n_windows) if n_windows else 0.0
    result.aggregate = _aggregate_windows(result.window_results)

    # OOS gate — only run if walk-forward passed
    if run_oos and result.walk_forward_pass_rate >= WALK_FORWARD_PASS_RATIO:
        try:
            oos_start = datetime.fromisoformat(rotation["oos_start"])
            oos_end = datetime.fromisoformat(rotation["oos_end"])
            result.oos_metrics = _run_one(candidate, oos_start, oos_end, pair=pair)
        except Exception as exc:
            result.error = f"oos: {exc}"

    return result


def run_batch(
    candidates: list[Candidate],
    data: dict,
    pair: str = "EURUSD",
) -> list[CandidateResult]:
    """Run all candidates sequentially. Returns one CandidateResult per candidate."""
    results: list[CandidateResult] = []
    for c in candidates:
        results.append(run_candidate(c, data, pair=pair))
    return results
