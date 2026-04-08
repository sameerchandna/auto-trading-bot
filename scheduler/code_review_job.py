"""Code review job entry point — runs daily via Task Scheduler at 07:00 UTC.

Pipeline:
  1. apply_code_decisions() — drain any approve/reject decisions made since
     the last run. Rejected hashes go into reports/rejected_fixes.json so
     ReviewAgent never re-proposes them. Approved entries are logged only:
     actually editing source code is left to the user (no auto-apply).
  2. ReviewAgent.scan() — heuristic checks against REVIEW_RULES.md.
  3. Filter out findings whose hash is in rejected_fixes.json.
  4. FixAgent.propose() — wrap each finding in a Proposal with risk + suggestion.
  5. Write reports/code_review/YYYY-MM-DD.md.
  6. Push the top CRITICAL/WARNING proposals into approvals.json `pending`
     so they show up in the next email + dashboard.

SUGGESTION-level findings appear in the daily report but are *not* pushed to
the approval queue — too noisy for daily action. The user reads them in the
report directly.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from agents.review_agent import scan, summarise
from agents.fix_agent import propose, write_report, Proposal

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
APPROVALS_FILE = REPO_ROOT / "reports" / "approvals.json"
REJECTED_FILE = REPO_ROOT / "reports" / "rejected_fixes.json"

# Cap how many findings get pushed into the approval queue per run.
# Anything beyond this still appears in the markdown report.
MAX_PENDING_PER_RUN = 5


# ---------- approvals + rejected-fixes I/O --------------------------------

def _load_approvals() -> dict:
    if not APPROVALS_FILE.exists():
        return {"pending": [], "approved": [], "rejected": []}
    data = json.loads(APPROVALS_FILE.read_text())
    data.setdefault("pending", [])
    data.setdefault("approved", [])
    data.setdefault("rejected", [])
    return data


def _save_approvals(data: dict) -> None:
    APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_FILE.write_text(json.dumps(data, indent=2))


def _load_rejected() -> dict:
    """Schema: {"hashes": {hash: {rule_id, file, rejected_at, reason}}}."""
    if not REJECTED_FILE.exists():
        return {"hashes": {}}
    try:
        data = json.loads(REJECTED_FILE.read_text())
    except json.JSONDecodeError:
        logger.warning("rejected_fixes.json malformed, starting fresh")
        return {"hashes": {}}
    data.setdefault("hashes", {})
    return data


def _save_rejected(data: dict) -> None:
    REJECTED_FILE.parent.mkdir(parents=True, exist_ok=True)
    REJECTED_FILE.write_text(json.dumps(data, indent=2))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- decision application ------------------------------------------

def apply_code_decisions() -> dict:
    """Drain code-review approve/reject decisions from approvals.json.

    - approved -> logged only (no auto-apply); marked processed
    - rejected -> hash added to rejected_fixes.json; marked processed
    """
    approvals = _load_approvals()
    rejected_db = _load_rejected()
    summary = {"approved_logged": 0, "rejected_blacklisted": 0}

    for entry in approvals.get("approved", []):
        if entry.get("kind") != "code" or entry.get("processed"):
            continue
        logger.info(
            f"Code fix {entry.get('id')} approved by user — "
            f"manual application required: {entry.get('title')}"
        )
        entry["processed"] = True
        entry["applied_at"] = _utcnow_iso()
        summary["approved_logged"] += 1

    for entry in approvals.get("rejected", []):
        if entry.get("kind") != "code" or entry.get("processed"):
            continue
        h = entry.get("finding_hash")
        if h:
            rejected_db["hashes"][h] = {
                "rule_id": entry.get("rule_id"),
                "file": entry.get("file"),
                "rejected_at": _utcnow_iso(),
                "reason": f"user rejected {entry.get('id')}",
            }
            summary["rejected_blacklisted"] += 1
        entry["processed"] = True

    _save_approvals(approvals)
    _save_rejected(rejected_db)
    return summary


def push_proposal(approvals: dict, proposal: Proposal, approval_id: str) -> None:
    """Add a proposal to approvals.json `pending`."""
    f = proposal.finding
    title = f"Fix {f.rule_id} in {f.file}:{f.line}"
    detail_lines = [
        f"  Risk: {proposal.risk}",
        f"  Issue: {f.message}",
    ]
    if f.symbol:
        detail_lines.append(f"  Symbol: {f.symbol}")
    if f.snippet:
        detail_lines.append(f"  Code: {f.snippet}")
    detail_lines.append(f"  Suggested fix: {proposal.suggestion}")
    detail_lines.append(f"  Hash: {f.hash}")

    approvals["pending"].append({
        "id": approval_id,
        "kind": "code",
        "title": title,
        "details": "\n".join(detail_lines),
        # extra fields used by apply_code_decisions:
        "rule_id": f.rule_id,
        "file": f.file,
        "finding_hash": f.hash,
    })


# ---------- main ----------------------------------------------------------

def run(dry_run: bool = False) -> dict:
    decisions = apply_code_decisions()
    if any(decisions.values()):
        print(f"Applied prior code-review decisions: {decisions}")

    print("Scanning source dirs...")
    findings = scan(REPO_ROOT)
    by_sev = summarise(findings)
    print(
        f"  CRITICAL: {len(by_sev['CRITICAL'])}  "
        f"WARNING: {len(by_sev['WARNING'])}  "
        f"SUGGESTION: {len(by_sev['SUGGESTION'])}"
    )

    rejected_db = _load_rejected()
    rejected_hashes = set(rejected_db["hashes"].keys())
    if rejected_hashes:
        before = len(findings)
        findings = [f for f in findings if f.hash not in rejected_hashes]
        skipped = before - len(findings)
        if skipped:
            print(f"  Skipped {skipped} previously-rejected finding(s).")

    proposals = propose(findings)
    report_path = write_report(proposals, findings) if not dry_run else None
    if report_path:
        print(f"Report written: {report_path}")

    # Pick which proposals to push into the approval queue: CRITICAL first,
    # then WARNING, ordered by file. SUGGESTION never pushed.
    actionable = [
        p for p in proposals
        if p.finding.severity in ("CRITICAL", "WARNING")
    ]
    actionable.sort(key=lambda p: (
        0 if p.finding.severity == "CRITICAL" else 1,
        p.finding.file,
        p.finding.line,
    ))
    actionable = actionable[:MAX_PENDING_PER_RUN]

    pushed = 0
    if actionable and not dry_run:
        approvals = _load_approvals()
        # Don't double-add: skip any whose finding_hash already lives in any bucket
        existing = {
            e.get("finding_hash")
            for bucket in ("pending", "approved", "rejected")
            for e in approvals.get(bucket, [])
            if e.get("kind") == "code"
        }
        next_n = 1
        for p in actionable:
            if p.finding.hash in existing:
                continue
            push_proposal(approvals, p, f"C{next_n}")
            next_n += 1
            pushed += 1
        if pushed:
            _save_approvals(approvals)

    summary = {
        "findings": len(findings),
        "critical": len(by_sev["CRITICAL"]),
        "warning": len(by_sev["WARNING"]),
        "suggestion": len(by_sev["SUGGESTION"]),
        "pushed_to_approvals": pushed,
        "report": str(report_path) if report_path else None,
    }
    print("\nSummary:", json.dumps(summary, indent=2))
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Run daily code review")
    parser.add_argument("--dry-run", action="store_true", help="Don't write report or approvals")
    args = parser.parse_args()
    try:
        run(dry_run=args.dry_run)
        return 0
    except Exception as exc:
        print(f"code_review_job FAILED: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
