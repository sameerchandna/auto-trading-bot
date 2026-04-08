"""FastAPI routes for one-click approval/rejection of proposed changes.

Approvals are persisted to reports/approvals.json with shape:

    {
      "pending":  [ {id, kind, title, details}, ... ],
      "approved": [ {id, kind, title, details, decided_at}, ... ],
      "rejected": [ {id, kind, title, details, decided_at}, ... ]
    }

Phases 4/5 (research + code review agents) write into `pending`. The
routes in this module move entries from `pending` to `approved` or
`rejected`. Actually *applying* an approved change is a separate step
owned by the originating agent (e.g. research agent promotes params.json
after seeing its entry move to `approved`).

Mount into the dashboard app via `register_approval_routes(app)`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
APPROVALS_FILE = REPO_ROOT / "reports" / "approvals.json"

VALID_KINDS = {"research", "code"}


def _ensure_file() -> None:
    APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not APPROVALS_FILE.exists():
        APPROVALS_FILE.write_text(
            json.dumps({"pending": [], "approved": [], "rejected": []}, indent=2)
        )


def _load() -> dict:
    _ensure_file()
    data = json.loads(APPROVALS_FILE.read_text())
    data.setdefault("pending", [])
    data.setdefault("approved", [])
    data.setdefault("rejected", [])
    return data


def _save(data: dict) -> None:
    APPROVALS_FILE.write_text(json.dumps(data, indent=2))


def _decide(kind: str, item_id: str, decision: str) -> dict:
    if kind not in VALID_KINDS:
        raise HTTPException(400, f"Unknown kind '{kind}' (expected one of {VALID_KINDS})")
    if decision not in ("approved", "rejected"):
        raise HTTPException(400, f"Invalid decision '{decision}'")

    data = _load()
    match = next(
        (i for i, e in enumerate(data["pending"])
         if e.get("id") == item_id and e.get("kind") == kind),
        None,
    )
    if match is None:
        # Already decided? Return idempotent success so a double-click doesn't error.
        for bucket in ("approved", "rejected"):
            for e in data[bucket]:
                if e.get("id") == item_id and e.get("kind") == kind:
                    return {"status": "already_decided", "bucket": bucket, "entry": e}
        raise HTTPException(404, f"No pending {kind} item with id '{item_id}'")

    entry = data["pending"].pop(match)
    entry["decided_at"] = datetime.now(timezone.utc).isoformat()
    data[decision].append(entry)
    _save(data)
    logger.info(f"Approval decision: {kind}/{item_id} -> {decision}")
    return {"status": "ok", "bucket": decision, "entry": entry}


def _html_response(title: str, body: str, color: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html><head><title>{title}</title>
<style>
body {{ font-family: -apple-system, Segoe UI, sans-serif; max-width: 560px;
       margin: 80px auto; padding: 24px; }}
.card {{ border-left: 4px solid {color}; padding: 16px 20px; background: #f8f8f8; }}
h1 {{ margin: 0 0 12px; font-size: 20px; }}
a {{ color: #0366d6; }}
</style></head>
<body><div class="card"><h1>{title}</h1>{body}</div>
<p><a href="/">← Back to dashboard</a></p></body></html>"""
    return HTMLResponse(html)


def register_approval_routes(app: FastAPI) -> None:
    """Attach /approve and /reject routes to an existing FastAPI app."""

    @app.get("/approve/{kind}/{item_id}", response_class=HTMLResponse)
    async def approve(kind: str, item_id: str):
        result = _decide(kind, item_id, "approved")
        if result["status"] == "already_decided":
            return _html_response(
                "Already decided",
                f"<p><code>{kind}/{item_id}</code> was previously "
                f"<b>{result['bucket']}</b>.</p>",
                "#888",
            )
        return _html_response(
            "Approved ✓",
            f"<p><code>{kind}/{item_id}</code> approved and moved to the "
            f"approved queue. The originating agent will apply the change on "
            f"its next run.</p>",
            "#2ea44f",
        )

    @app.get("/reject/{kind}/{item_id}", response_class=HTMLResponse)
    async def reject(kind: str, item_id: str):
        result = _decide(kind, item_id, "rejected")
        if result["status"] == "already_decided":
            return _html_response(
                "Already decided",
                f"<p><code>{kind}/{item_id}</code> was previously "
                f"<b>{result['bucket']}</b>.</p>",
                "#888",
            )
        return _html_response(
            "Rejected ✗",
            f"<p><code>{kind}/{item_id}</code> rejected. It will not be "
            f"re-proposed for 30 days.</p>",
            "#d73a49",
        )

    @app.get("/api/approvals")
    async def list_approvals():
        return _load()
