"""Data ingestion - OANDA only, with local caching.

Yahoo Finance fallback is disabled. All data must come from OANDA to ensure
consistent 21:00/22:00 UTC timestamps across all assets.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

from config.assets import get_asset, DEFAULT_ASSET, resolve_pair_name
from config.settings import PAIR, PAIR_NAME, TIMEFRAMES, HISTORY_START
from sqlalchemy import insert as sa_insert

from data.models import Candle
from storage.database import CandleRecord, get_session

load_dotenv()
logger = logging.getLogger(__name__)


def _resolve_instrument(pair: str) -> str:
    """Resolve any pair string to an OANDA instrument code."""
    asset_name = resolve_pair_name(pair)
    return get_asset(asset_name).oanda_instrument


def fetch_candles(
    pair: str = PAIR,
    timeframe: str = "1d",
    count: int = 500,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    # disabled — kept for call-site compatibility but ignored
    days_back: Optional[int] = None,
) -> list[Candle]:
    """Fetch OHLCV candles from OANDA."""
    from data.oanda import fetch_candles as oanda_fetch

    instrument = _resolve_instrument(pair)

    if start and end:
        return oanda_fetch(timeframe=timeframe, from_time=start, to_time=end, instrument=instrument)
    return oanda_fetch(timeframe=timeframe, count=count, instrument=instrument)


# _fetch_yahoo is disabled — Yahoo data produces inconsistent timestamps.
# All fetches must go through OANDA.
def _fetch_yahoo(*args, **kwargs):
    raise RuntimeError("Yahoo Finance is disabled. Use OANDA for all data fetching.")


def fetch_all_timeframes(pair: str = PAIR) -> dict[str, list[Candle]]:
    """Fetch candles for all configured timeframes."""
    all_candles = {}
    for tf in TIMEFRAMES:
        candles = fetch_candles(pair=pair, timeframe=tf)
        all_candles[tf] = candles
    return all_candles


def save_candles(candles: list[Candle], pair: str = PAIR):
    """Save candles to database, replacing duplicates in bulk."""
    if not candles:
        return

    session = get_session()
    try:
        rows = [
            {
                "pair": pair,
                "timeframe": c.timeframe,
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
        # SQLite caps parameters per statement (default 999, 32766 on newer builds).
        # 8 columns per row → batch at 2000 rows to stay well under both limits.
        BATCH = 2000
        for i in range(0, len(rows), BATCH):
            stmt = sa_insert(CandleRecord).prefix_with("OR REPLACE").values(rows[i:i + BATCH])
            session.execute(stmt)
        session.commit()
        logger.info(f"Upserted {len(rows)} candles for {pair}")
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
        records.reverse()

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


def get_live_price(pair: str = PAIR) -> dict:
    """Get current live price from OANDA."""
    from data.oanda import get_current_price
    return get_current_price(instrument=_resolve_instrument(pair))
