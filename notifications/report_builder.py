"""Assemble the daily trading bot report.

Real sections (implemented now):
- System status: capital, open positions, today's signals
- Today's trading: closed trades, open P&L
- Pending approvals (from reports/approvals.json)

Stub sections (flesh out in Phases 4–6):
- Research (Phase 4)
- Code review (Phase 5)
- Readiness (Phase 6)

Format matches the template in docs/AGENT_SYSTEMS.md. Output is plain-text
(rendered in a monospace block) plus a simple HTML wrapper; the HTML is
intentionally minimal — the plain-text version is the canonical form.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.assets import ACTIVE_ASSETS, DEFAULT_ASSET
from config.settings import STARTING_CAPITAL
from storage.database import PositionRecord, SignalRecord, get_session

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
APPROVALS_FILE = REPO_ROOT / "reports" / "approvals.json"

RULE = "━" * 40
DASH_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASH_PORT = os.getenv("DASHBOARD_PORT", "8050")
DASH_BASE = f"http://{DASH_HOST}:{DASH_PORT}"


@dataclass
class PendingApproval:
    id: str          # e.g. "R1", "C1"
    kind: str        # "research" | "code"
    title: str
    details: str     # multi-line pre-formatted summary


def _load_pending_approvals() -> list[PendingApproval]:
    """Read reports/approvals.json. Schema is stub for now — Phases 4/5 populate it.

    Expected shape:
        {
          "pending": [
            {"id": "R1", "kind": "research", "title": "...", "details": "..."},
            ...
          ]
        }
    """
    if not APPROVALS_FILE.exists():
        return []
    try:
        data = json.loads(APPROVALS_FILE.read_text())
    except json.JSONDecodeError as e:
        logger.warning(f"approvals.json malformed: {e}")
        return []
    out = []
    for item in data.get("pending", []):
        try:
            out.append(PendingApproval(
                id=item["id"],
                kind=item["kind"],
                title=item["title"],
                details=item["details"],
            ))
        except (KeyError, TypeError):
            logger.warning(f"Skipping malformed approval entry: {item}")
    return out


def _format_money(n: float) -> str:
    sign = "+" if n >= 0 else "-"
    return f"{sign}£{abs(n):,.2f}"


def _gather_status() -> dict:
    """Pull account snapshot + today's trading activity from the DB."""
    session = get_session()
    try:
        today_utc = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_utc - timedelta(days=today_utc.weekday())

        open_positions = (
            session.query(PositionRecord).filter_by(status="open").all()
        )

        closed_live = (
            session.query(PositionRecord)
            .filter_by(status="closed")
            .filter(~PositionRecord.tags.contains('"backtest"'))
            .all()
        )
        total_pnl = sum((r.pnl or 0) for r in closed_live)
        capital = STARTING_CAPITAL + total_pnl

        closed_today = [
            r for r in closed_live
            if r.closed_at and r.closed_at >= today_utc
        ]
        closed_week = [
            r for r in closed_live
            if r.closed_at and r.closed_at >= week_start
        ]
        today_pnl = sum((r.pnl or 0) for r in closed_today)
        week_pnl = sum((r.pnl or 0) for r in closed_week)
        week_wins = sum(1 for r in closed_week if (r.pnl or 0) > 0)
        week_losses = sum(1 for r in closed_week if (r.pnl or 0) < 0)

        signals_today = (
            session.query(SignalRecord)
            .filter(SignalRecord.timestamp >= today_utc)
            .count()
        )

        return {
            "capital": capital,
            "today_pnl": today_pnl,
            "open_positions": open_positions,
            "closed_today": closed_today,
            "closed_week_count": len(closed_week),
            "week_pnl": week_pnl,
            "week_wins": week_wins,
            "week_losses": week_losses,
            "signals_today": signals_today,
        }
    finally:
        session.close()


def _section_actions(approvals: list[PendingApproval]) -> str:
    if not approvals:
        return f"{RULE}\nACTIONS NEEDED (0)\n{RULE}\nNothing awaiting approval.\n"
    lines = [RULE, f"ACTIONS NEEDED ({len(approvals)})", RULE]
    for a in approvals:
        tag = "[RESEARCH]" if a.kind == "research" else "[CODE]"
        lines.append(f"{tag} {a.title}")
        for dl in a.details.splitlines():
            lines.append(f"  {dl}")
        approve_url = f"{DASH_BASE}/approve/{a.kind}/{a.id}"
        reject_url = f"{DASH_BASE}/reject/{a.kind}/{a.id}"
        lines.append(f"  Approve: {approve_url}")
        lines.append(f"  Reject:  {reject_url}")
        lines.append("")
    return "\n".join(lines)


def _section_status(status: dict) -> str:
    lines = [RULE, "SYSTEM STATUS", RULE]
    lines.append(f"Account:     £{status['capital']:,.2f} ({_format_money(status['today_pnl'])} today)")
    lines.append(f"Open:        {len(status['open_positions'])} positions")
    lines.append(f"Active:      {', '.join(ACTIVE_ASSETS)}")
    lines.append(f"Signals:     {status['signals_today']} generated today")
    lines.append(f"Pipeline:    Last report: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def _section_trading(status: dict) -> str:
    lines = [RULE, "TODAY'S TRADING", RULE]
    closed_today = status["closed_today"]
    if not closed_today:
        lines.append("Closed:  (none today)")
    else:
        for r in closed_today:
            direction = (r.direction or "").upper()
            pnl_pips = r.pnl_pips if r.pnl_pips is not None else 0
            pnl_gbp = r.pnl or 0
            lines.append(
                f"Closed:  {r.pair or DEFAULT_ASSET} {direction} | "
                f"{pnl_pips:+.1f} pips | {_format_money(pnl_gbp)}"
            )

    if status["open_positions"]:
        for p in status["open_positions"]:
            direction = (p.direction or "").upper()
            lines.append(
                f"Open:    {p.pair or DEFAULT_ASSET} {direction} "
                f"(entry {p.entry_price}, size {p.size})"
            )
    else:
        lines.append("Open:    (no open positions)")

    wl = f"{status['week_wins']}W {status['week_losses']}L"
    lines.append(
        f"Week:    {status['closed_week_count']} trades | {wl} | "
        f"{_format_money(status['week_pnl'])}"
    )
    return "\n".join(lines)


def _section_research() -> str:
    """Read research/test_history.json and summarise the most recent run."""
    history_file = REPO_ROOT / "research" / "test_history.json"
    if not history_file.exists():
        return f"{RULE}\nRESEARCH\n{RULE}\nNo research history yet.\n"
    try:
        data = json.loads(history_file.read_text())
    except json.JSONDecodeError:
        return f"{RULE}\nRESEARCH\n{RULE}\ntest_history.json malformed.\n"

    tests = data.get("tests", [])
    if not tests:
        return f"{RULE}\nRESEARCH\n{RULE}\nNo tests run yet.\n"

    today = datetime.utcnow().strftime("%Y-%m-%d")
    todays = [t for t in tests if t.get("tested_at", "").startswith(today)]
    sample = todays if todays else tests[-5:]
    label = "today" if todays else "most recent 5"

    promoted = sum(1 for t in sample if t.get("verdict") == "PROMOTED_CANDIDATE")
    flagged = sum(1 for t in sample if (t.get("verdict") or "").startswith("FLAGGED"))
    rejected = sum(1 for t in sample if (t.get("verdict") or "").startswith("REJECTED"))

    anchor = (data.get("anchor_baseline") or {}).get("metrics") or {}
    rolling_metrics = (data.get("rolling_baseline") or {}).get("metrics") or {}

    lines = [
        RULE, "RESEARCH", RULE,
        f"Tested ({label}): {len(sample)}  |  "
        f"Promoted: {promoted}  Flagged: {flagged}  Rejected: {rejected}",
        f"Tests this quarter: {data.get('budget', {}).get('tests_this_quarter', 0)}"
        f" / escalation at {data.get('budget', {}).get('escalate_bar_after', 500)}",
    ]
    if anchor:
        lines.append(
            f"Anchor baseline: PF {anchor.get('profit_factor', 0):.2f}  "
            f"WR {anchor.get('win_rate', 0):.1%}  "
            f"DD {anchor.get('max_drawdown_pct', 0):.1%}  "
            f"trades {anchor.get('trades', 0)}"
        )
    if rolling_metrics:
        lines.append(
            f"Rolling baseline: PF {rolling_metrics.get('profit_factor', 0):.2f}  "
            f"WR {rolling_metrics.get('win_rate', 0):.1%}  "
            f"DD {rolling_metrics.get('max_drawdown_pct', 0):.1%}"
        )
    lines.append("")

    if sample:
        lines.append("Candidates:")
        lines.append("  ID    Mutation                            PF    WR    DD    Pass  Verdict")
        for t in sample:
            agg = t.get("aggregate") or {}
            pass_rate = t.get("walk_forward_pass_rate") or 0
            verdict = (t.get("verdict") or "?").replace("_", " ")
            mutation = (t.get("mutation") or "")[:34].ljust(34)
            test_id = (t.get("id") or "")[-6:]
            lines.append(
                f"  {test_id:<6}{mutation}  "
                f"{agg.get('median_profit_factor', 0):>4.2f}  "
                f"{agg.get('median_win_rate', 0):>4.0%}  "
                f"{agg.get('median_max_drawdown_pct', 0):>4.0%}  "
                f"{pass_rate:>3.0%}   {verdict}"
            )
            reason = t.get("verdict_reason")
            if reason and t.get("verdict", "").startswith(("REJECTED", "FLAGGED")):
                lines.append(f"         → {reason[:90]}")
        lines.append("")

    if promoted:
        lines.append("Promotion candidates (see ACTIONS NEEDED above):")
        for t in sample:
            if t.get("verdict") != "PROMOTED_CANDIDATE":
                continue
            ap = t.get("approval") or {}
            agg = t.get("aggregate") or {}
            lines.append(
                f"  {ap.get('approval_id', '?')} {t.get('mutation', '')} — "
                f"PF {agg.get('median_profit_factor', 0):.2f}"
            )
    lines.append(f"Full research history: {DASH_BASE}/#research")
    return "\n".join(lines)


def _section_code_review() -> str:
    """Read the most recent reports/code_review/*.md and summarise it."""
    cr_dir = REPO_ROOT / "reports" / "code_review"
    if not cr_dir.exists():
        return f"{RULE}\nCODE REVIEW\n{RULE}\nNo code review runs yet.\n"
    reports = sorted(cr_dir.glob("*.md"))
    if not reports:
        return f"{RULE}\nCODE REVIEW\n{RULE}\nNo code review runs yet.\n"
    latest = reports[-1]
    text = latest.read_text(encoding="utf-8")

    # Pull the "Findings:" header line if present.
    summary_line = ""
    for line in text.splitlines():
        if line.startswith("Findings:"):
            summary_line = line.strip()
            break

    # Count code-kind pending approvals to surface action count.
    code_pending = 0
    if APPROVALS_FILE.exists():
        try:
            data = json.loads(APPROVALS_FILE.read_text())
            code_pending = sum(
                1 for e in data.get("pending", []) if e.get("kind") == "code"
            )
        except json.JSONDecodeError:
            pass

    lines = [
        RULE, "CODE REVIEW", RULE,
        f"Last run: {latest.stem}",
    ]
    if summary_line:
        lines.append(summary_line)
    lines.append(f"Pending fixes awaiting approval: {code_pending}")
    if code_pending:
        lines.append("(See ACTIONS NEEDED above for details.)")
    lines.append(f"Full report: {DASH_BASE}/reports/code_review/{latest.name}")
    return "\n".join(lines)


def _section_readiness() -> str:
    """Run ReadinessAgent and produce a daily traffic-light section.

    Also archives a daily Markdown snapshot to reports/readiness/.
    """
    try:
        from agents.readiness_agent import run as run_readiness, write_snapshot
    except Exception as exc:
        return f"{RULE}\nREADINESS STATUS\n{RULE}\nReadinessAgent unavailable: {exc}\n"
    try:
        report = run_readiness()
        snapshot = write_snapshot(report)
    except Exception as exc:
        logger.exception("readiness run failed")
        return f"{RULE}\nREADINESS STATUS\n{RULE}\nReadinessAgent error: {exc}\n"

    lines = [
        RULE, "READINESS STATUS", RULE,
        f"Demo:  {report.demo_status}  {report.demo_pass_count}/{len(report.demo)} checks passing",
        f"Live:  {report.live_status}  {report.live_pass_count}/{len(report.live)} checks passing",
        "",
    ]
    failing_demo = [c for c in report.demo if not c.passed]
    if failing_demo:
        lines.append("Demo blockers:")
        for c in failing_demo:
            lines.append(f"  ❌ {c.id} {c.label} — {c.detail}")
    if report.demo_pass_count == len(report.demo):
        failing_live = [c for c in report.live if not c.passed]
        if failing_live:
            lines.append("Live blockers:")
            for c in failing_live:
                lines.append(f"  ❌ {c.id} {c.label} — {c.detail}")
    lines.append("")
    lines.append(f"Snapshot: {snapshot.name}")
    return "\n".join(lines)


def build_report() -> tuple[str, str, str]:
    """Build the daily report.

    Returns (subject, body_text, body_html).
    """
    status = _gather_status()
    approvals = _load_pending_approvals()

    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    action_count = len(approvals)
    if action_count:
        subject = f"Trading Bot — Daily Report {date_str} | {action_count} action(s) needed"
    else:
        subject = f"Trading Bot — Daily Report {date_str}"

    sections = [
        _section_actions(approvals),
        _section_status(status),
        _section_trading(status),
        _section_research(),
        _section_code_review(),
        _section_readiness(),
        f"{RULE}\nFull dashboard: {DASH_BASE}/\n{RULE}",
    ]
    body_text = "\n\n".join(sections)

    # Minimal HTML — wrap plain text in a <pre> so clients render it monospace.
    body_html = (
        "<html><body>"
        f"<pre style='font-family: Menlo, Consolas, monospace; font-size: 13px;'>"
        f"{body_text.replace('<', '&lt;').replace('>', '&gt;')}"
        "</pre></body></html>"
    )

    return subject, body_text, body_html
