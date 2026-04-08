"""FX conversion helpers — convert quote-currency P&L to account currency (GBP).

Uses the most recent GBPUSD candle stored in the local DB as the reference rate.
Acceptable for paper/demo P&L; revisit before live trading.
"""
import logging

from data.ingestion import load_candles

logger = logging.getLogger(__name__)

# Fallback if DB has no GBPUSD data at all (e.g. fresh install before first fetch).
_FALLBACK_GBPUSD = 1.27


def get_gbpusd_rate() -> float:
    """Return the most recent GBPUSD close from the DB, across any available TF.

    Checks 1h first (most granular routinely updated), then 4h, 1d, 15m.
    """
    for tf in ("1h", "4h", "1d", "15m"):
        candles = load_candles(pair="GBPUSD", timeframe=tf, limit=1)
        if candles:
            return candles[-1].close
    logger.warning(
        f"No GBPUSD candles in DB — using fallback rate {_FALLBACK_GBPUSD}"
    )
    return _FALLBACK_GBPUSD


def to_gbp(amount: float, quote_currency: str) -> float:
    """Convert an amount expressed in `quote_currency` into GBP.

    Supported quote currencies: USD, GBP. Unknown currencies log a warning
    and pass the amount through unchanged (i.e. treated as GBP).
    """
    if quote_currency == "GBP":
        return amount
    if quote_currency == "USD":
        rate = get_gbpusd_rate()  # GBPUSD = USD per 1 GBP
        if rate <= 0:
            logger.warning(f"Invalid GBPUSD rate {rate} — returning amount unchanged")
            return amount
        return amount / rate
    logger.warning(
        f"Unsupported quote currency '{quote_currency}' — no FX conversion applied"
    )
    return amount
