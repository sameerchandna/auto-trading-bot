"""Economic calendar — rule-based generation of high-impact EUR/USD events.

Generates dates for major recurring events that move EURUSD:
  - US Non-Farm Payrolls (NFP): first Friday of each month, 13:30 UTC
  - US CPI: ~12th-15th of each month, 13:30 UTC (2nd Tuesday–Thursday)
  - FOMC Rate Decision: 8 meetings/year, 19:00 UTC
  - ECB Rate Decision: ~6 meetings/year, 13:15 UTC

Rule-based approach works for backtests without needing historical scrapes.
Override file (config/news_overrides.json) adds ad-hoc events.

Usage:
    from data.economic_calendar import is_news_blocked
    if is_news_blocked(timestamp, before_mins=30, after_mins=15):
        # skip signal
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

OVERRIDES_FILE = PROJECT_ROOT / "config" / "news_overrides.json"


# ---------------------------------------------------------------------------
# FOMC & ECB meeting dates (known schedule, updated yearly)
# These are the announcement dates. Add new years as they're published.
# ---------------------------------------------------------------------------

FOMC_DATES = {
    2023: ["02-01", "03-22", "05-03", "06-14", "07-26", "09-20", "11-01", "12-13"],
    2024: ["01-31", "03-20", "05-01", "06-12", "07-31", "09-18", "11-07", "12-18"],
    2025: ["01-29", "03-19", "05-07", "06-18", "07-30", "09-17", "10-29", "12-17"],
    2026: ["01-28", "03-18", "05-06", "06-17", "07-29", "09-16", "10-28", "12-16"],
}

ECB_DATES = {
    2023: ["02-02", "03-16", "05-04", "06-15", "07-27", "09-14", "10-26", "12-14"],
    2024: ["01-25", "03-07", "04-11", "06-06", "07-18", "09-12", "10-17", "12-12"],
    2025: ["01-30", "03-06", "04-17", "06-05", "07-17", "09-11", "10-30", "12-18"],
    2026: ["01-22", "03-05", "04-16", "06-04", "07-16", "09-10", "10-29", "12-10"],
}


def _first_friday(year: int, month: int) -> int:
    """Return the day-of-month of the first Friday."""
    from calendar import weekday, FRIDAY
    for day in range(1, 8):
        if weekday(year, month, day) == FRIDAY:
            return day
    return 1  # fallback


def _second_week_midweek(year: int, month: int) -> int:
    """Return a day around the 12th-14th (typical CPI release).

    US CPI is usually the 2nd Tuesday or Wednesday of the month.
    """
    from calendar import weekday, TUESDAY, WEDNESDAY
    for day in range(10, 16):
        if weekday(year, month, day) in (TUESDAY, WEDNESDAY):
            return day
    return 13  # fallback


def generate_events(year: int) -> list[dict]:
    """Generate all high-impact EURUSD events for a given year.

    Returns list of {"name", "datetime", "impact"} dicts.
    """
    events = []

    # NFP — first Friday of each month, 13:30 UTC
    for month in range(1, 13):
        day = _first_friday(year, month)
        events.append({
            "name": "NFP",
            "datetime": datetime(year, month, day, 13, 30),
            "impact": "high",
        })

    # US CPI — ~12th-14th of each month, 13:30 UTC
    for month in range(1, 13):
        day = _second_week_midweek(year, month)
        events.append({
            "name": "US_CPI",
            "datetime": datetime(year, month, day, 13, 30),
            "impact": "high",
        })

    # FOMC — specific dates, 19:00 UTC
    fomc_dates = FOMC_DATES.get(year, [])
    for date_str in fomc_dates:
        month, day = date_str.split("-")
        events.append({
            "name": "FOMC",
            "datetime": datetime(year, int(month), int(day), 19, 0),
            "impact": "high",
        })

    # ECB — specific dates, 13:15 UTC
    ecb_dates = ECB_DATES.get(year, [])
    for date_str in ecb_dates:
        month, day = date_str.split("-")
        events.append({
            "name": "ECB",
            "datetime": datetime(year, int(month), int(day), 13, 15),
            "impact": "high",
        })

    return sorted(events, key=lambda e: e["datetime"])


def _load_overrides() -> list[dict]:
    """Load ad-hoc events from config/news_overrides.json if it exists."""
    if not OVERRIDES_FILE.exists():
        return []
    try:
        with open(OVERRIDES_FILE) as f:
            data = json.load(f)
        return [
            {
                "name": e["name"],
                "datetime": datetime.fromisoformat(e["datetime"]),
                "impact": e.get("impact", "high"),
            }
            for e in data
        ]
    except Exception as exc:
        logger.warning(f"Failed to load news overrides: {exc}")
        return []


@lru_cache(maxsize=8)
def _events_for_year(year: int) -> list[dict]:
    """Cached event list for a year (generated + overrides)."""
    events = generate_events(year)
    for ov in _load_overrides():
        if ov["datetime"].year == year:
            events.append(ov)
    return sorted(events, key=lambda e: e["datetime"])


def is_news_blocked(
    timestamp: datetime,
    before_mins: int = 30,
    after_mins: int = 15,
) -> bool:
    """Check if a timestamp falls within a news block window.

    Returns True if `timestamp` is within [event - before_mins, event + after_mins]
    for any high-impact event.
    """
    events = _events_for_year(timestamp.year)

    before_delta = timedelta(minutes=before_mins)
    after_delta = timedelta(minutes=after_mins)

    for event in events:
        event_time = event["datetime"]
        if event_time - before_delta <= timestamp <= event_time + after_delta:
            logger.debug(
                f"News blocked: {event['name']} at {event_time} "
                f"(window: -{before_mins}m/+{after_mins}m)"
            )
            return True
        # Early exit — events are sorted, skip past ones
        if event_time - before_delta > timestamp:
            break

    return False


def next_event(timestamp: datetime) -> dict | None:
    """Return the next upcoming event after timestamp, or None."""
    events = _events_for_year(timestamp.year)
    for event in events:
        if event["datetime"] > timestamp:
            return event
    # Check next year
    next_year = _events_for_year(timestamp.year + 1)
    return next_year[0] if next_year else None
