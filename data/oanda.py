"""OANDA API integration for live price data and trading."""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv

from data.models import Candle

load_dotenv()
logger = logging.getLogger(__name__)

API_KEY = os.getenv("OANDA_API_KEY", "")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
ACCOUNT_TYPE = os.getenv("OANDA_ACCOUNT_TYPE", "practice")

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if ACCOUNT_TYPE == "practice"
    else "https://api-fxtrade.oanda.com"
)
STREAM_URL = (
    "https://stream-fxpractice.oanda.com"
    if ACCOUNT_TYPE == "practice"
    else "https://stream-fxtrade.oanda.com"
)

HEADERS = {"Authorization": f"Bearer {API_KEY}"}

# OANDA instrument name for EURUSD
INSTRUMENT = "EUR_USD"

# Map our timeframes to OANDA granularities
TF_MAP = {
    "15m": "M15",
    "1h": "H1",
    "4h": "H4",
    "1d": "D",
    "1wk": "W",
    "5m": "M5",
    "30m": "M30",
}


def get_account_summary() -> dict:
    """Get account details including balance."""
    r = httpx.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/summary",
        headers=HEADERS,
    )
    r.raise_for_status()
    return r.json()["account"]


def get_current_price() -> dict:
    """Get current bid/ask price for EUR/USD."""
    r = httpx.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/pricing",
        headers=HEADERS,
        params={"instruments": INSTRUMENT},
    )
    r.raise_for_status()
    prices = r.json()["prices"][0]
    return {
        "bid": float(prices["bids"][0]["price"]),
        "ask": float(prices["asks"][0]["price"]),
        "mid": (float(prices["bids"][0]["price"]) + float(prices["asks"][0]["price"])) / 2,
        "spread": float(prices["asks"][0]["price"]) - float(prices["bids"][0]["price"]),
        "time": prices["time"],
        "tradeable": prices["tradeable"],
    }


def fetch_candles(
    timeframe: str = "1h",
    count: int = 200,
    from_time: Optional[datetime] = None,
    to_time: Optional[datetime] = None,
) -> list[Candle]:
    """Fetch candles from OANDA.

    OANDA provides up to 5000 candles per request with full history.
    """
    granularity = TF_MAP.get(timeframe)
    if not granularity:
        logger.warning(f"Unknown timeframe: {timeframe}")
        return []

    params = {
        "granularity": granularity,
        "price": "M",  # Midpoint candles
    }

    if from_time and to_time:
        # Auto-paginate: OANDA allows max 5000 candles per request
        # Pre-chunk by time to stay under the limit
        CHUNK_HOURS = {
            "H1": 4000, "H4": 16000, "M15": 1000,
            "D": 100000, "W": 500000, "M1": 240,
        }
        hours_per_chunk = CHUNK_HOURS.get(granularity, 4000)

        all_candles = []
        chunk_start = from_time
        while chunk_start < to_time:
            chunk_end = min(chunk_start + timedelta(hours=hours_per_chunk), to_time)
            chunk_params = {
                "granularity": granularity,
                "price": "M",
                "from": chunk_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            r = httpx.get(
                f"{BASE_URL}/v3/instruments/{INSTRUMENT}/candles",
                headers=HEADERS,
                params=chunk_params,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()

            chunk_candles = data.get("candles", [])
            if not chunk_candles:
                chunk_start = chunk_end
                continue

            for c in chunk_candles:
                if not c.get("complete", True) and not c.get("mid"):
                    continue
                mid = c["mid"]
                ts = datetime.fromisoformat(c["time"].replace("Z", "+00:00")).replace(tzinfo=None)
                all_candles.append(Candle(
                    timestamp=ts,
                    timeframe=timeframe,
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=float(c.get("volume", 0)),
                ))

            chunk_start = chunk_end

        logger.info(f"OANDA: fetched {len(all_candles)} {timeframe} candles (paginated)")
        return all_candles
    else:
        params["count"] = min(count, 5000)

    r = httpx.get(
        f"{BASE_URL}/v3/instruments/{INSTRUMENT}/candles",
        headers=HEADERS,
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    candles = []
    for c in data.get("candles", []):
        if not c.get("complete", True) and not c.get("mid"):
            continue

        mid = c["mid"]
        ts = datetime.fromisoformat(c["time"].replace("Z", "+00:00")).replace(tzinfo=None)

        candles.append(Candle(
            timestamp=ts,
            timeframe=timeframe,
            open=float(mid["o"]),
            high=float(mid["h"]),
            low=float(mid["l"]),
            close=float(mid["c"]),
            volume=float(c.get("volume", 0)),
        ))

    logger.info(f"OANDA: fetched {len(candles)} {timeframe} candles")
    return candles


def fetch_all_timeframes(
    timeframes: list[str] | None = None,
    count: int = 500,
) -> dict[str, list[Candle]]:
    """Fetch candles for multiple timeframes from OANDA."""
    if timeframes is None:
        timeframes = ["15m", "1h", "4h", "1d", "1wk"]

    all_candles = {}
    for tf in timeframes:
        try:
            candles = fetch_candles(timeframe=tf, count=count)
            all_candles[tf] = candles
        except Exception as e:
            logger.error(f"OANDA fetch failed for {tf}: {e}")
            all_candles[tf] = []

    return all_candles


def fetch_extended_history(
    timeframe: str = "1h",
    days_back: int = 365,
) -> list[Candle]:
    """Fetch extended history by making multiple requests.

    OANDA allows 5000 candles per request, so we batch.
    """
    all_candles = []
    end = datetime.utcnow()
    start = end - timedelta(days=days_back)

    current_from = start
    while current_from < end:
        try:
            batch = fetch_candles(
                timeframe=timeframe,
                from_time=current_from,
                to_time=end,
                count=5000,
            )
            if not batch:
                break

            all_candles.extend(batch)
            current_from = batch[-1].timestamp + timedelta(minutes=1)

            if len(batch) < 5000:
                break  # Got all available data
        except Exception as e:
            logger.error(f"Extended history error: {e}")
            break

    # Remove duplicates
    seen = set()
    unique = []
    for c in all_candles:
        key = (c.timestamp, c.timeframe)
        if key not in seen:
            seen.add(key)
            unique.append(c)

    logger.info(f"OANDA: fetched {len(unique)} {timeframe} candles ({days_back} days)")
    return unique


# --- Trading (Paper for now, but ready for live) ---

def place_order(
    direction: str,
    units: int,
    stop_loss: float,
    take_profit: float,
) -> dict:
    """Place a market order on OANDA practice account."""
    if direction == "short":
        units = -abs(units)
    else:
        units = abs(units)

    order_data = {
        "order": {
            "type": "MARKET",
            "instrument": INSTRUMENT,
            "units": str(units),
            "stopLossOnFill": {
                "price": f"{stop_loss:.5f}",
            },
            "takeProfitOnFill": {
                "price": f"{take_profit:.5f}",
            },
            "timeInForce": "FOK",
        }
    }

    r = httpx.post(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/orders",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=order_data,
        timeout=10,
    )
    r.raise_for_status()
    result = r.json()
    logger.info(f"Order placed: {result}")
    return result


def get_open_trades() -> list[dict]:
    """Get all open trades on the account."""
    r = httpx.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/openTrades",
        headers=HEADERS,
    )
    r.raise_for_status()
    return r.json().get("trades", [])


def close_trade(trade_id: str) -> dict:
    """Close a specific trade."""
    r = httpx.put(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/trades/{trade_id}/close",
        headers=HEADERS,
    )
    r.raise_for_status()
    return r.json()


def get_trade_history(count: int = 50) -> list[dict]:
    """Get recent closed trades."""
    r = httpx.get(
        f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/transactions",
        headers=HEADERS,
        params={"count": count, "type": "ORDER_FILL"},
    )
    r.raise_for_status()
    return r.json().get("transactions", [])
