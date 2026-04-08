"""Thin wrapper around the shared email_utils package.

Loads Gmail credentials from environment (populated by dotenv in main.py /
scheduler entry points) and sends a pre-built report. All report assembly
lives in report_builder.py — this module only handles the send.
"""
from __future__ import annotations

import logging
import os

from email_utils import EmailConfig, EmailError, send_email

logger = logging.getLogger(__name__)


def _load_config() -> EmailConfig:
    user = os.getenv("GMAIL_USER", "").strip()
    pwd = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not user or not pwd:
        raise EmailError(
            "GMAIL_USER and GMAIL_APP_PASSWORD must be set in .env. "
            "Generate an App Password at https://myaccount.google.com/apppasswords"
        )
    return EmailConfig(user=user, app_password=pwd, from_name="Trading Bot")


def send_report(
    subject: str,
    body_text: str,
    body_html: str | None = None,
    to: str | None = None,
) -> None:
    """Send a daily report email. Raises EmailError on failure."""
    cfg = _load_config()
    recipient = to or os.getenv("REPORT_TO_EMAIL", "").strip() or cfg.user
    send_email(
        cfg,
        to=recipient,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )
    logger.info(f"Daily report sent to {recipient}")
