"""Fold-splitting schemes for walk-forward backtest validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from calendar import monthrange


@dataclass
class Fold:
    fold_id: str
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date
    label: str
    partial: bool = False


def _quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    last_day = monthrange(year, end_month)[1]
    return date(year, start_month, 1), date(year, end_month, last_day)


def walk_forward_quarterly(start: date, end: date) -> list[Fold]:
    """Emit one fold per quarter touching [start, end].

    IS = all prior data within the usable window (informational for phase 1).
    OOS = the quarter itself, clamped to [start, end]. Final fold is marked
    partial=True when end lands before the quarter's natural end.
    """
    if end < start:
        return []

    folds: list[Fold] = []
    year, q = start.year, _quarter_of(start)
    while True:
        q_start, q_end = _quarter_bounds(year, q)
        if q_start > end:
            break

        oos_start = max(q_start, start)
        oos_end = min(q_end, end)
        partial = oos_end < q_end

        fold_id = f"{year}Q{q}"
        is_start = start
        is_end = oos_start - timedelta(days=1) if oos_start > start else oos_start

        folds.append(Fold(
            fold_id=fold_id,
            is_start=is_start,
            is_end=is_end,
            oos_start=oos_start,
            oos_end=oos_end,
            label=f"OOS:{fold_id}",
            partial=partial,
        ))

        q += 1
        if q > 4:
            q = 1
            year += 1

    return folds


def kfold_shuffled_yearly(start: date, end: date) -> list[Fold]:
    """Yearly IS/OOS permutations: for each full year in range, OOS=that year,
    IS=all other years. Partial years at either end are excluded.
    """
    if end < start:
        return []

    years = []
    y = start.year
    while y <= end.year:
        y_start = date(y, 1, 1)
        y_end = date(y, 12, 31)
        if y_start >= start and y_end <= end:
            years.append(y)
        y += 1

    folds: list[Fold] = []
    for y in years:
        y_start = date(y, 1, 1)
        y_end = date(y, 12, 31)
        folds.append(Fold(
            fold_id=f"{y}",
            is_start=start,
            is_end=end,
            oos_start=y_start,
            oos_end=y_end,
            label=f"OOS:{y}",
            partial=False,
        ))
    return folds


def latest_candle_date(pair: str) -> date:
    """Query candles table for max(timestamp) for this pair's primary ticker."""
    from config.assets import get_asset
    from storage.database import get_engine
    from sqlalchemy import text

    asset = get_asset(pair)
    ticker = asset.yahoo_ticker
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT MAX(timestamp) FROM candles WHERE pair = :p"),
            {"p": ticker},
        ).fetchone()
    if not row or not row[0]:
        return datetime.utcnow().date()
    ts = row[0]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return ts.date() if isinstance(ts, datetime) else ts


SCHEMES = {
    "walkforward": walk_forward_quarterly,
    "kfold_shuffled": kfold_shuffled_yearly,
}
