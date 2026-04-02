"""Data ingestion from Yahoo Finance with local caching."""
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from config.settings import PAIR, TIMEFRAMES, MAX_HISTORY_DAYS
from data.models import Candle
from storage.database import CandleRecord, get_session

logger = logging.getLogger(__name__)


def fetch_candles(
    pair: str = PAIR,
    timeframe: str = "1d",
    days_back: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[Candle]:
    """Fetch OHLCV candles from Yahoo Finance."""
    if days_back is None:
        days_back = MAX_HISTORY_DAYS.get(timeframe, 60)

    if end is None:
        end = datetime.utcnow()
    if start is None:
        start = end - timedelta(days=days_back)

    logger.info(f"Fetching {pair} {timeframe} from {start.date()} to {end.date()}")

    ticker = yf.Ticker(pair)
    df = ticker.history(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval=timeframe,
    )

    if df.empty:
        logger.warning(f"No data returned for {pair} {timeframe}")
        return []

    candles = []
    for ts, row in df.iterrows():
        ts = pd.Timestamp(ts)
        if ts.tzinfo:
            ts = ts.tz_localize(None)
        candles.append(Candle(
            timestamp=ts.to_pydatetime(),
            timeframe=timeframe,
            open=round(float(row["Open"]), 5),
            high=round(float(row["High"]), 5),
            low=round(float(row["Low"]), 5),
            close=round(float(row["Close"]), 5),
            volume=float(row.get("Volume", 0)),
        ))

    logger.info(f"Fetched {len(candles)} candles for {pair} {timeframe}")
    return candles


def fetch_all_timeframes(pair: str = PAIR) -> dict[str, list[Candle]]:
    """Fetch candles for all configured timeframes."""
    all_candles = {}
    for tf in TIMEFRAMES:
        candles = fetch_candles(pair=pair, timeframe=tf)
        all_candles[tf] = candles
    return all_candles


def save_candles(candles: list[Candle], pair: str = PAIR):
    """Save candles to database, skipping duplicates."""
    if not candles:
        return

    session = get_session()
    saved = 0
    try:
        for c in candles:
            existing = session.query(CandleRecord).filter_by(
                pair=pair,
                timeframe=c.timeframe,
                timestamp=c.timestamp,
            ).first()

            if existing:
                # Update with latest data
                existing.open = c.open
                existing.high = c.high
                existing.low = c.low
                existing.close = c.close
                existing.volume = c.volume
            else:
                session.add(CandleRecord(
                    pair=pair,
                    timeframe=c.timeframe,
                    timestamp=c.timestamp,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                ))
                saved += 1

        session.commit()
        logger.info(f"Saved {saved} new candles for {pair}")
    except Exception as e:
        session.rollback()
        logger.error(f"Error saving candles: {e}")
        raise
    finally:
        session.close()


def load_candles(
    pair: str = PAIR,
    timeframe: str = "1d",
    limit: int = 500,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> list[Candle]:
    """Load candles from local database."""
    session = get_session()
    try:
        query = session.query(CandleRecord).filter_by(
            pair=pair, timeframe=timeframe
        )
        if start:
            query = query.filter(CandleRecord.timestamp >= start)
        if end:
            query = query.filter(CandleRecord.timestamp <= end)

        records = query.order_by(CandleRecord.timestamp.desc()).limit(limit).all()
        records.reverse()  # Oldest first

        return [
            Candle(
                timestamp=r.timestamp,
                timeframe=r.timeframe,
                open=r.open,
                high=r.high,
                low=r.low,
                close=r.close,
                volume=r.volume,
            )
            for r in records
        ]
    finally:
        session.close()


def fetch_and_cache(pair: str = PAIR) -> dict[str, list[Candle]]:
    """Fetch all timeframes and cache to database."""
    all_data = fetch_all_timeframes(pair)
    for tf, candles in all_data.items():
        save_candles(candles, pair)
    return all_data
