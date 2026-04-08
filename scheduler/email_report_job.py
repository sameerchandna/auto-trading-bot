"""Windows Task Scheduler entry point: build and send the daily report.

Scheduled for 19:00 UTC daily. Run manually with:

    python -m scheduler.email_report_job

Exits non-zero on send failure so Task Scheduler marks the run as failed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Load .env before importing modules that read from os.environ
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from email_utils import EmailError
from notifications.email_reporter import send_report
from notifications.report_builder import build_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("email_report_job")


def main() -> int:
    try:
        subject, body_text, body_html = build_report()
    except Exception as e:
        logger.exception(f"Failed to build report: {e}")
        return 2

    try:
        send_report(subject, body_text, body_html)
    except EmailError as e:
        logger.error(f"Failed to send report: {e}")
        return 1

    logger.info("Daily report sent successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
