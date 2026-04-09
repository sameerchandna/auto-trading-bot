"""Research job entry point — runs daily via Task Scheduler at 08:00 UTC.

Pipeline:
  1. Load test_history.json, sync rolling baseline from optimized_params.json
  2. ParameterAgent generates up to N candidates (5/day)
  3. BacktestRunner runs each across non-overlapping walk-forward windows + OOS
  4. ValidationAgent assigns a verdict to each result
  5. Record results to test_history.json
  6. Write daily report to reports/research/YYYY-MM-DD.md
  7. Promotions go into the approval queue for the evening email

Approval IDs (R1, R2, ...) are assigned only to PROMOTED_CANDIDATE results.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from research import history
from research.parameter_agent import generate_candidates
from research.backtest_runner import run_batch
from research.validation_agent import evaluate
from research.promotion import apply_decisions, push_promotion

REPORTS_DIR = Path(__file__).parent.parent / "reports" / "research"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_report(path: Path, results: list[dict]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Research Run — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"Total candidates tested: **{len(results)}**",
        "",
        "## Verdicts",
        "",
        "| ID | Mutation | Pass Rate | Median PF | Median WR | Median DD | Verdict | Approval |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        agg = r.get("aggregate") or {}
        ap = r.get("approval") or {}
        lines.append(
            f"| {r['id']} | {r.get('mutation', '')} | "
            f"{r.get('walk_forward_pass_rate', 0):.0%} | "
            f"{agg.get('median_profit_factor', 0):.2f} | "
            f"{agg.get('median_win_rate', 0):.1%} | "
            f"{agg.get('median_max_drawdown_pct', 0):.1%} | "
            f"`{r.get('verdict', '?')}` | "
            f"{ap.get('approval_id', '-')} |"
        )

    lines += ["", "## Details", ""]
    for r in results:
        lines.append(f"### {r['id']} — {r.get('mutation', '')}")
        lines.append(f"- Hash: `{r['params_hash']}`")
        lines.append(f"- Verdict: **{r.get('verdict')}** — {r.get('verdict_reason', '')}")
        agg = r.get("aggregate") or {}
        if agg:
            lines.append(
                f"- Aggregate: PF={agg.get('median_profit_factor', 0):.2f}  "
                f"WR={agg.get('median_win_rate', 0):.1%}  "
                f"DD={agg.get('median_max_drawdown_pct', 0):.1%}  "
                f"Exp={agg.get('median_expectancy_pips', 0):.1f}p"
            )
        if r.get("oos"):
            o = r["oos"]
            lines.append(
                f"- OOS: trades={o.get('trades', 0)}  PF={o.get('profit_factor', 0):.2f}  "
                f"WR={o.get('win_rate', 0):.1%}"
            )
        lines.append("- Windows:")
        for w in r.get("windows", []):
            v = w.get("val", {})
            lines.append(
                f"  - {w['window_id']}  {w['period']}  -> "
                f"trades={v.get('trades', 0)} PF={v.get('profit_factor', 0):.2f} "
                f"{'PASS' if w['passed'] else 'FAIL: ' + (w.get('fail_reason') or '')}"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _send_chained_email() -> None:
    """Fire the daily report email after research completes.

    Research is the last scheduled step of the morning pipeline (fetch →
    code review → research), so hooking the email off its tail means the
    user receives the report the moment all processing is done, instead
    of waiting until a fixed 19:00 slot.

    Failures here are logged but do not fail the research job itself —
    the research results are already on disk and in the approval queue.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        from notifications.report_builder import build_report
        from notifications.email_reporter import send_report
        subject, body_text, body_html = build_report()
        send_report(subject, body_text, body_html)
        print(f"Daily report email sent: {subject}")
    except Exception as exc:
        print(f"WARNING: chained email failed: {exc}", file=sys.stderr)


def run(budget: int | None = None, dry_run: bool = False, seed: int | None = None,
        send_email: bool = True) -> dict:
    """Main entry point. Returns a summary dict."""
    data = history.load()
    data = history.sync_rolling_baseline(data)

    # Process any approve/reject decisions made since the last run
    decisions_summary = apply_decisions(history_data=data)
    if any(decisions_summary.values()):
        print(f"Applied prior decisions: {decisions_summary}")
        # Re-sync in case rolling baseline changed
        data = history.sync_rolling_baseline(data)

    candidates = generate_candidates(data=data, budget=budget, seed=seed)
    if not candidates:
        print("No candidates generated (all already tested or blacklisted).")
        return {"tested": 0, "promoted": 0, "flagged": 0, "rejected": 0}

    print(f"Generated {len(candidates)} candidates:")
    for c in candidates:
        print(f"  {c.params_hash}  {c.mutation_summary}")

    print("\nRunning backtests across walk-forward windows + OOS...")
    results = run_batch(candidates, data)

    history_entries: list[dict] = []
    promoted_count = 0
    flagged_count = 0
    rejected_count = 0
    next_approval_n = 1

    for r in results:
        verdict = evaluate(r, data)
        test_id = history.next_test_id(data)
        entry = r.to_history_entry(test_id, _utcnow_iso())
        entry["verdict"] = verdict.code
        entry["verdict_reason"] = verdict.reason
        entry["delta_vs_anchor"] = verdict.delta_vs_anchor
        entry["delta_vs_rolling"] = verdict.delta_vs_rolling

        if verdict.code == "PROMOTED_CANDIDATE":
            approval_id = f"R{next_approval_n}"
            entry["approval"] = {
                "status": "PENDING",
                "approval_id": approval_id,
                "decided_at": None,
                "decided_by": None,
            }
            push_promotion(entry, approval_id)
            next_approval_n += 1
            promoted_count += 1
        elif verdict.code.startswith("FLAGGED"):
            entry["approval"] = None
            flagged_count += 1
        else:
            entry["approval"] = None
            rejected_count += 1

        history_entries.append(entry)
        history.record_test(data, entry)

    if not dry_run:
        history.save(data)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{today}.md"
    if not dry_run:
        _write_report(report_path, history_entries)
        print(f"\nReport written: {report_path}")

    summary = {
        "tested": len(results),
        "promoted": promoted_count,
        "flagged": flagged_count,
        "rejected": rejected_count,
        "report": str(report_path),
    }
    print("\nSummary:", json.dumps(summary, indent=2))

    if send_email and not dry_run:
        print("\nSending chained daily report email...")
        _send_chained_email()

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run daily parameter research")
    parser.add_argument("--budget", type=int, default=None, help="Override max combinations (default: 5)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write history or report")
    parser.add_argument("--seed", type=int, default=None, help="Seed for reproducible candidate selection")
    parser.add_argument("--no-email", action="store_true", help="Skip the chained daily report email")
    args = parser.parse_args()
    try:
        run(budget=args.budget, dry_run=args.dry_run, seed=args.seed, send_email=not args.no_email)
        return 0
    except Exception as exc:
        print(f"research_job FAILED: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
