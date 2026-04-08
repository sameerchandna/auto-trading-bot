"""ReadinessAgent — daily traffic-light status for demo + live readiness.

Advisory only. Never blocks the bot. Read by the email report and the
dashboard. Cheap to run (no backtests, just reads existing state):

  - Backtest metrics: research/test_history.json `anchor_baseline.metrics`
  - Live trades: PositionRecord rows where tags do NOT contain "backtest"
  - Code presence: simple file content checks
  - Manual flags: reports/readiness/checklist.json `manual` block

Each check returns a CheckResult; the agent rolls them into a per-list
status (🔴/🟡/🟢) and saves a daily snapshot to reports/readiness/.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
CHECKLIST_FILE = REPO_ROOT / "reports" / "readiness" / "checklist.json"
SNAPSHOT_DIR = REPO_ROOT / "reports" / "readiness"
HISTORY_FILE = REPO_ROOT / "research" / "test_history.json"


@dataclass
class CheckResult:
    id: str
    label: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "passed": self.passed, "detail": self.detail}


@dataclass
class ReadinessReport:
    demo: list[CheckResult] = field(default_factory=list)
    live: list[CheckResult] = field(default_factory=list)
    generated_at: str = ""

    @property
    def demo_pass_count(self) -> int:
        return sum(1 for c in self.demo if c.passed)

    @property
    def live_pass_count(self) -> int:
        return sum(1 for c in self.live if c.passed)

    @property
    def demo_status(self) -> str:
        if not self.demo:
            return "⚪"
        return "🟢" if self.demo_pass_count == len(self.demo) else "🟡" if self.demo_pass_count > 0 else "🔴"

    @property
    def live_status(self) -> str:
        if not self.live:
            return "⚪"
        if self.demo_pass_count < len(self.demo):
            return "🔴"
        return "🟢" if self.live_pass_count == len(self.live) else "🟡"

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "demo": {
                "status": self.demo_status,
                "pass": self.demo_pass_count,
                "total": len(self.demo),
                "checks": [c.to_dict() for c in self.demo],
            },
            "live": {
                "status": self.live_status,
                "pass": self.live_pass_count,
                "total": len(self.live),
                "checks": [c.to_dict() for c in self.live],
            },
        }


# ---------- data accessors ------------------------------------------------

def _load_anchor_metrics() -> dict | None:
    if not HISTORY_FILE.exists():
        return None
    try:
        data = json.loads(HISTORY_FILE.read_text())
    except json.JSONDecodeError:
        return None
    return (data.get("anchor_baseline") or {}).get("metrics")


def _load_live_trades() -> list:
    """Closed live (non-backtest) PositionRecord rows."""
    try:
        from storage.database import PositionRecord, get_session
    except Exception:
        return []
    session = get_session()
    try:
        rows = (
            session.query(PositionRecord)
            .filter_by(status="closed")
            .filter(~PositionRecord.tags.contains('"backtest"'))
            .all()
        )
        # detach simple fields so we can close the session
        return [
            {
                "pnl": r.pnl or 0.0,
                "pnl_pips": r.pnl_pips or 0.0,
                "closed_at": r.closed_at,
            }
            for r in rows
        ]
    finally:
        session.close()


# ---------- evaluators ----------------------------------------------------

def _eval_backtest_pf_min(check: dict, ctx: dict) -> CheckResult:
    m = ctx.get("anchor_metrics")
    target = check["params"]["min"]
    if not m:
        return CheckResult(check["id"], check["label"], False, "no anchor metrics yet")
    pf = m.get("profit_factor", 0)
    return CheckResult(check["id"], check["label"], pf >= target, f"PF={pf:.2f} (need ≥{target})")


def _eval_backtest_wr_min(check: dict, ctx: dict) -> CheckResult:
    m = ctx.get("anchor_metrics")
    target = check["params"]["min"]
    if not m:
        return CheckResult(check["id"], check["label"], False, "no anchor metrics yet")
    wr = m.get("win_rate", 0)
    return CheckResult(check["id"], check["label"], wr >= target, f"WR={wr:.1%} (need ≥{target:.0%})")


def _eval_backtest_dd_max(check: dict, ctx: dict) -> CheckResult:
    m = ctx.get("anchor_metrics")
    cap = check["params"]["max"]
    if not m:
        return CheckResult(check["id"], check["label"], False, "no anchor metrics yet")
    dd = m.get("max_drawdown_pct", 1.0)
    return CheckResult(check["id"], check["label"], dd <= cap, f"DD={dd:.1%} (need ≤{cap:.0%})")


def _eval_backtest_trades_min(check: dict, ctx: dict) -> CheckResult:
    m = ctx.get("anchor_metrics")
    target = check["params"]["min"]
    if not m:
        return CheckResult(check["id"], check["label"], False, "no anchor metrics yet")
    n = m.get("trades", 0)
    return CheckResult(check["id"], check["label"], n >= target, f"trades={n} (need ≥{target})")


def _eval_code_contains(check: dict, ctx: dict) -> CheckResult:
    p = REPO_ROOT / check["params"]["file"]
    needle = check["params"]["needle"]
    if not p.exists():
        return CheckResult(check["id"], check["label"], False, f"{p.name} missing")
    found = needle in p.read_text(encoding="utf-8", errors="ignore")
    return CheckResult(
        check["id"], check["label"], found,
        f"{'found' if found else 'missing'} `{needle}` in {check['params']['file']}",
    )


def _demo_drawdown_pct(trades: list) -> float:
    """Peak-to-trough drawdown of cumulative live PnL."""
    if not trades:
        return 0.0
    sorted_trades = sorted(trades, key=lambda t: t["closed_at"] or datetime.min)
    cum, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted_trades:
        cum += t["pnl"]
        if cum > peak:
            peak = cum
        if peak > 0:
            dd = (peak - cum) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _eval_live_vs_backtest_expectancy(check: dict, ctx: dict) -> CheckResult:
    trades = ctx.get("live_trades") or []
    min_trades = check["params"].get("min_live_trades", 20)
    tol = check["params"]["tolerance_pct"]
    if len(trades) < min_trades:
        return CheckResult(
            check["id"], check["label"], False,
            f"only {len(trades)} live trades (need ≥{min_trades})",
        )
    m = ctx.get("anchor_metrics") or {}
    bt_exp = m.get("expectancy_pips", 0)
    live_exp = sum(t["pnl_pips"] for t in trades) / len(trades)
    if bt_exp == 0:
        return CheckResult(check["id"], check["label"], False, "backtest expectancy is 0")
    delta = abs(live_exp - bt_exp) / abs(bt_exp)
    return CheckResult(
        check["id"], check["label"], delta <= tol,
        f"live={live_exp:.1f}p vs bt={bt_exp:.1f}p (Δ {delta:.0%}, tol {tol:.0%})",
    )


def _eval_demo_days_min(check: dict, ctx: dict) -> CheckResult:
    trades = ctx.get("live_trades") or []
    target = check["params"]["min"]
    days = {t["closed_at"].date() for t in trades if t["closed_at"]}
    return CheckResult(
        check["id"], check["label"], len(days) >= target,
        f"{len(days)} demo days (need ≥{target})",
    )


def _eval_demo_expectancy_positive(check: dict, ctx: dict) -> CheckResult:
    trades = ctx.get("live_trades") or []
    if not trades:
        return CheckResult(check["id"], check["label"], False, "no live trades yet")
    exp = sum(t["pnl_pips"] for t in trades) / len(trades)
    return CheckResult(check["id"], check["label"], exp > 0, f"expectancy={exp:.1f}p over {len(trades)} trades")


def _eval_demo_dd_max(check: dict, ctx: dict) -> CheckResult:
    trades = ctx.get("live_trades") or []
    cap = check["params"]["max"]
    if not trades:
        return CheckResult(check["id"], check["label"], False, "no live trades yet")
    dd = _demo_drawdown_pct(trades)
    return CheckResult(check["id"], check["label"], dd <= cap, f"demo DD={dd:.1%} (need ≤{cap:.0%})")


def _eval_manual_flag(check: dict, ctx: dict) -> CheckResult:
    flag = check["params"]["flag"]
    val = bool((ctx.get("manual") or {}).get(flag))
    return CheckResult(
        check["id"], check["label"], val,
        f"manual.{flag}={val} (edit reports/readiness/checklist.json)",
    )


def _eval_all_demo_pass(check: dict, ctx: dict) -> CheckResult:
    demo_results = ctx.get("demo_results") or []
    passed = all(c.passed for c in demo_results)
    n_pass = sum(1 for c in demo_results if c.passed)
    return CheckResult(
        check["id"], check["label"], passed,
        f"{n_pass}/{len(demo_results)} demo checks passing",
    )


CHECK_EVALUATORS = {
    "backtest_pf_min": _eval_backtest_pf_min,
    "backtest_wr_min": _eval_backtest_wr_min,
    "backtest_dd_max": _eval_backtest_dd_max,
    "backtest_trades_min": _eval_backtest_trades_min,
    "code_contains": _eval_code_contains,
    "live_vs_backtest_expectancy": _eval_live_vs_backtest_expectancy,
    "demo_days_min": _eval_demo_days_min,
    "demo_expectancy_positive": _eval_demo_expectancy_positive,
    "demo_dd_max": _eval_demo_dd_max,
    "manual_flag": _eval_manual_flag,
    "all_demo_pass": _eval_all_demo_pass,
}


def _evaluate(check: dict, ctx: dict) -> CheckResult:
    fn = CHECK_EVALUATORS.get(check["evaluator"])
    if fn is None:
        return CheckResult(check["id"], check["label"], False, f"unknown evaluator {check['evaluator']}")
    try:
        return fn(check, ctx)
    except Exception as exc:
        logger.exception(f"check {check['id']} crashed")
        return CheckResult(check["id"], check["label"], False, f"evaluator error: {exc}")


def run() -> ReadinessReport:
    """Evaluate the full checklist and return a ReadinessReport."""
    if not CHECKLIST_FILE.exists():
        raise FileNotFoundError(f"checklist missing: {CHECKLIST_FILE}")
    spec = json.loads(CHECKLIST_FILE.read_text())

    ctx: dict = {
        "anchor_metrics": _load_anchor_metrics(),
        "live_trades": _load_live_trades(),
        "manual": spec.get("manual") or {},
    }

    report = ReadinessReport(generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    for check in spec.get("demo", []):
        report.demo.append(_evaluate(check, ctx))

    # Live evaluators may need demo results (LV-01 all_demo_pass)
    ctx["demo_results"] = report.demo
    for check in spec.get("live", []):
        report.live.append(_evaluate(check, ctx))

    return report


def write_snapshot(report: ReadinessReport) -> Path:
    """Daily archive of the readiness state — Markdown."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = SNAPSHOT_DIR / f"{today}.md"
    lines = [
        f"# Readiness — {today}",
        "",
        f"- Demo: {report.demo_status}  ({report.demo_pass_count}/{len(report.demo)} passing)",
        f"- Live: {report.live_status}  ({report.live_pass_count}/{len(report.live)} passing)",
        "",
        "## Demo checks",
        "",
    ]
    for c in report.demo:
        mark = "✅" if c.passed else "❌"
        lines.append(f"- {mark} **{c.id}** {c.label} — {c.detail}")
    lines += ["", "## Live checks", ""]
    for c in report.live:
        mark = "✅" if c.passed else "❌"
        lines.append(f"- {mark} **{c.id}** {c.label} — {c.detail}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
