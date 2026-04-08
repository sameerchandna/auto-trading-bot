"""FixAgent — turns review findings into approval-queue-ready proposals.

Per the design in docs/AGENT_SYSTEMS.md, FixAgent **never applies code
changes**. It only:

  1. Generates a textual *suggested fix* per finding (rule-specific
     remediation guidance, not a literal git diff — actual diffs need an
     LLM-backed editor and are out of scope for Phase 5).
  2. Tags each fix with risk level (LOW / MEDIUM / HIGH) so the user can
     scan the daily report quickly.
  3. Writes a structured Markdown report to reports/code_review/YYYY-MM-DD.md.
  4. Returns proposal records for the scheduler to push into approvals.json.

The user (or a future LLM-backed apply step) is responsible for actually
turning an approved suggestion into a code change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agents.review_agent import Finding

REPO_ROOT = Path(__file__).parent.parent
REPORTS_DIR = REPO_ROOT / "reports" / "code_review"


# Risk classification: how dangerous is auto-applying a fix to this rule?
RISK_OF_RULE = {
    "CR-001": "HIGH",   # changing a constant could shift live behaviour
    "CR-002": "HIGH",
    "CR-003": "HIGH",
    "CR-004": "HIGH",
    "CR-005": "HIGH",
    "CR-006": "HIGH",
    "WR-001": "LOW",    # bulk-upsert refactor: contained
    "WR-002": "MEDIUM", # threading config through changes signatures
    "WR-003": "MEDIUM",
    "WR-004": "LOW",
    "WR-005": "MEDIUM",
    "WR-006": "MEDIUM",
    "SG-001": "LOW",
    "SG-002": "LOW",
    "SG-003": "LOW",
    "SG-004": "LOW",
    "SG-005": "LOW",
}

# Boilerplate suggested-fix text per rule. Kept short — the finding's own
# `message` already names the specific symbol/line.
SUGGESTION_OF_RULE = {
    "WR-001": "Replace per-row session call with a bulk INSERT/UPSERT or `session.bulk_save_objects(...)`.",
    "WR-002": "Move the timeframe literal into `config/settings.py` and import it.",
    "WR-004": "Catch the specific exception class and either log it or re-raise — never swallow silently.",
    "SG-001": "Add type annotations to the function signature and return type.",
    "SG-002": "Split the function into smaller helpers (target <60 lines per function).",
    "SG-003": "Add a one-line docstring describing what the function does and what it returns.",
}


@dataclass
class Proposal:
    finding: Finding
    risk: str
    suggestion: str

    @property
    def approval_id_suffix(self) -> str:
        return self.finding.hash

    def to_dict(self) -> dict:
        return {
            "finding": self.finding.to_dict(),
            "risk": self.risk,
            "suggestion": self.suggestion,
        }


def propose(findings: list[Finding]) -> list[Proposal]:
    """Generate one Proposal per finding."""
    out: list[Proposal] = []
    for f in findings:
        risk = RISK_OF_RULE.get(f.rule_id, "MEDIUM")
        suggestion = SUGGESTION_OF_RULE.get(
            f.rule_id,
            "Manual review required — no canned remediation for this rule yet.",
        )
        out.append(Proposal(finding=f, risk=risk, suggestion=suggestion))
    return out


def write_report(proposals: list[Proposal], findings: list[Finding]) -> Path:
    """Write a daily Markdown report. Returns the report path."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = REPORTS_DIR / f"{today}.md"

    by_sev: dict[str, list[Finding]] = {"CRITICAL": [], "WARNING": [], "SUGGESTION": []}
    for f in findings:
        by_sev.setdefault(f.severity, []).append(f)

    lines = [
        f"# Code Review — {today}",
        "",
        f"Findings: **{len(findings)}**  "
        f"(CRITICAL {len(by_sev['CRITICAL'])} · "
        f"WARNING {len(by_sev['WARNING'])} · "
        f"SUGGESTION {len(by_sev['SUGGESTION'])})",
        "",
    ]

    if not findings:
        lines.append("_No findings — repo passes all automated checks._")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    proposals_by_hash = {p.finding.hash: p for p in proposals}

    for sev in ("CRITICAL", "WARNING", "SUGGESTION"):
        items = by_sev.get(sev) or []
        if not items:
            continue
        lines.append(f"## {sev} ({len(items)})")
        lines.append("")
        for f in items:
            p = proposals_by_hash.get(f.hash)
            risk = p.risk if p else "?"
            lines.append(f"### `{f.rule_id}` — {f.file}:{f.line}")
            lines.append(f"- **Risk:** {risk}")
            if f.symbol:
                lines.append(f"- **Symbol:** `{f.symbol}`")
            lines.append(f"- **Issue:** {f.message}")
            if f.snippet:
                lines.append(f"- **Code:** `{f.snippet}`")
            if p:
                lines.append(f"- **Suggested fix:** {p.suggestion}")
            lines.append(f"- **Hash:** `{f.hash}`")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
