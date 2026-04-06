"""Asset registry for multi-instrument support."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AssetSpec:
    """Specification for a tradeable asset."""
    name: str              # Display name: "EURUSD", "XAUUSD", etc.
    oanda_instrument: str  # OANDA API code: "EUR_USD", "XAU_USD", etc.
    yahoo_ticker: str      # Yahoo Finance ticker: "EURUSD=X", "GC=F", etc.
    pip_value: float       # Smallest meaningful price unit: 0.0001 (forex), 0.01 (gold)
    price_decimals: int    # Decimal places for order formatting
    lot_size: int          # Standard lot size in base units
    asset_class: str       # "forex", "commodity", "index"
    quote_currency: str    # Quote currency for GBP conversion: "USD", "EUR", etc.


ASSETS: dict[str, AssetSpec] = {
    "EURUSD": AssetSpec(
        name="EURUSD",
        oanda_instrument="EUR_USD",
        yahoo_ticker="EURUSD=X",
        pip_value=0.0001,
        price_decimals=5,
        lot_size=100_000,
        asset_class="forex",
        quote_currency="USD",
    ),
    "AUDUSD": AssetSpec(
        name="AUDUSD",
        oanda_instrument="AUD_USD",
        yahoo_ticker="AUDUSD=X",
        pip_value=0.0001,
        price_decimals=5,
        lot_size=100_000,
        asset_class="forex",
        quote_currency="USD",
    ),
    "XAUUSD": AssetSpec(
        name="XAUUSD",
        oanda_instrument="XAU_USD",
        yahoo_ticker="GC=F",
        pip_value=0.01,
        price_decimals=2,
        lot_size=100,
        asset_class="commodity",
        quote_currency="USD",
    ),
    "US30": AssetSpec(
        name="US30",
        oanda_instrument="US30_USD",
        yahoo_ticker="US30=X",
        pip_value=1.0,
        price_decimals=1,
        lot_size=1,
        asset_class="index",
        quote_currency="USD",
    ),
    "WTICO": AssetSpec(
        name="WTICO",
        oanda_instrument="WTICO_USD",
        yahoo_ticker="CL=F",
        pip_value=0.01,
        price_decimals=2,
        lot_size=1000,
        asset_class="commodity",
        quote_currency="USD",
    ),
}

# Which assets are actively fetched/traded
ACTIVE_ASSETS: list[str] = ["EURUSD", "AUDUSD", "US30", "XAUUSD", "WTICO"]
DEFAULT_ASSET = "EURUSD"


def get_asset(name: str) -> AssetSpec:
    """Look up an asset by name. Raises KeyError if not found."""
    if name not in ASSETS:
        raise KeyError(f"Unknown asset '{name}'. Available: {list(ASSETS.keys())}")
    return ASSETS[name]


def resolve_pair_name(pair: str) -> str:
    """Resolve a pair string to a canonical asset name.

    Handles: 'EURUSD', 'EURUSD=X', 'GC=F', 'EUR_USD', etc.
    """
    if pair in ASSETS:
        return pair
    # Strip Yahoo '=X' suffix
    stripped = pair.replace("=X", "")
    if stripped in ASSETS:
        return stripped
    # Reverse-lookup by yahoo_ticker or oanda_instrument
    for name, spec in ASSETS.items():
        if spec.yahoo_ticker == pair or spec.oanda_instrument == pair:
            return name
    raise KeyError(f"Cannot resolve pair '{pair}'. Available: {list(ASSETS.keys())}")
