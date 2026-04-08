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

    lines = [
        RULE, "RESEARCH", RULE,
        f"Tested ({label}): {len(sample)}  |  "
        f"Promoted: {promoted}  Flagged: {flagged}  Rejected: {rejected}",
        f"Tests this quarter: {data.get('budget', {}).get('tests_this_quarter', 0)}"
        f" / escalation at {data.get('budget', {}).get('escalate_bar_after', 500)}",
        "",
    ]
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
    else:
        lines.append("No promotion candidates this run.")
    return "\n".join(lines)


def _section_code_review_stub() -> str:
    return (
        f"{RULE}\nCODE REVIEW\n{RULE}\n"
        "Code review pipeline not yet implemented (Phase 5).\n"
    )


def _section_readiness_stub() -> str:
    return (
        f"{RULE}\nREADINESS STATUS\n{RULE}\n"
        "Demo:  🟡 checks pending (ReadinessAgent — Phase 6)\n"
        "Live:  🔴 Not ready (demo required first)\n"
    )


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
        _section_code_review_stub(),
        _section_readiness_stub(),
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
