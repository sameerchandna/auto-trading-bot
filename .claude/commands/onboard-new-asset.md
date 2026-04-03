# Onboard New Asset

Add a new trading asset to the bot: auto-detect metadata from OANDA, register it, fetch full history, and verify data alignment against existing assets.

## Input

Ask the user which asset they want to add. They might say a pair name ("GBPUSD"), a common name ("Gold", "Oil", "Dow"), or an OANDA code ("GBP_USD"). Accept any of these.

## Steps

Follow these steps in order. Report progress as you go.

### 1. Find the Instrument on OANDA

Query the OANDA instruments API to search for the user's asset:

```python
import httpx, os
from dotenv import load_dotenv
load_dotenv()

API_KEY = os.getenv("OANDA_API_KEY")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
BASE_URL = "https://api-fxpractice.oanda.com" if os.getenv("OANDA_ACCOUNT_TYPE", "practice") == "practice" else "https://api-fxtrade.oanda.com"

r = httpx.get(
    f"{BASE_URL}/v3/accounts/{ACCOUNT_ID}/instruments",
    headers={"Authorization": f"Bearer {API_KEY}"},
    timeout=30,
)
instruments = r.json()["instruments"]
```

Search the returned instruments by matching the user's input (case-insensitive) against:
- `name` field (e.g. "EUR_USD")
- `displayName` field (e.g. "EUR/USD", "Gold", "US Wall St 30")
- The name with underscores removed (e.g. "EURUSD" matches "EUR_USD")

If multiple matches, show all with their type and displayName, and ask the user to pick. If exactly one match, show it and ask for confirmation.

**Before proceeding:** Check if this asset is already in `config/assets.py` ASSETS dict. If it is, tell the user and ask if they want to re-fetch history instead.

### 2. Auto-Detect Metadata

From the matched instrument object, extract the AssetSpec fields:

| OANDA Field | AssetSpec Field | Conversion |
|---|---|---|
| `name` | `oanda_instrument` | Direct (e.g. "EUR_USD") |
| `pipLocation` | `pip_value` | `10 ** pipLocation` (e.g. -4 → 0.0001, -2 → 0.01, 0 → 1.0) |
| `displayPrecision` | `price_decimals` | Direct (e.g. 5, 3, 1) |
| `type` | `asset_class` | CURRENCY → "forex", METAL → "commodity", CFD → "index" |

Derive `name` (the short name used as the dict key):
- For CURRENCY: concatenate the two currencies, e.g. "EUR_USD" → "EURUSD"
- For METAL/CFD: use the part before "_USD" or "_GBP" etc., e.g. "XAU_USD" → "XAUUSD", "US30_USD" → "US30"

Derive `yahoo_ticker`:
- CURRENCY: `"{NAME}=X"` (e.g. "EURUSD=X")
- METAL/CFD: check known mappings or use `"{NAME}=X"` as fallback

Derive `lot_size` from asset class:
- forex: 100,000
- commodity (METAL): 100
- index (CFD): 1

Show the complete AssetSpec to the user and ask them to confirm. Example:

```
Detected AssetSpec for US30:
  name:             US30
  oanda_instrument: US30_USD
  yahoo_ticker:     US30=X
  pip_value:        1.0
  price_decimals:   1
  lot_size:         1
  asset_class:      index

Does this look correct?
```

### 3. Add to Asset Registry

Edit `config/assets.py`:

1. Add a new entry to the `ASSETS` dict, matching the exact style of existing entries:
```python
"NEWASSET": AssetSpec(
    name="NEWASSET",
    oanda_instrument="NEW_ASSET",
    yahoo_ticker="NEWASSET=X",
    pip_value=0.0001,
    price_decimals=5,
    lot_size=100_000,
    asset_class="forex",
),
```

2. Add the asset name to the `ACTIVE_ASSETS` list.

There is no maximum limit on the number of assets.

### 4. Validation Fetch

Run a quick test fetch to confirm the asset works end-to-end:

```python
from data.ingestion import fetch_candles
candles = fetch_candles(pair="{NAME}=X", timeframe="1d", count=10)
print(f"Validation: got {len(candles)} candles")
for c in candles[:3]:
    print(f"  {c.timestamp} O={c.open} H={c.high} L={c.low} C={c.close}")
```

If this returns 0 candles or errors, stop and debug. Common issues:
- Wrong instrument code
- OANDA API key not set in `.env`
- Instrument not available on practice account

### 5. Full History Backfill

Fetch extended history for ALL timeframes. All data comes from OANDA only. Use `HISTORY_START` from `config/settings.py` — these are fixed start dates matched to EURUSD's history depth:
- 15m: 2023-01-01
- 1h:  2023-01-01
- 4h:  2016-04-04
- 1d:  2016-04-04
- 1wk: 2016-04-01

```python
import logging
logging.basicConfig(level=logging.INFO)
from data.oanda import fetch_extended_history
from data.ingestion import save_candles
from config.settings import HISTORY_START, TIMEFRAMES

pair = "{NAME}=X"
instrument = "{OANDA_INSTRUMENT}"

for tf in TIMEFRAMES:
    start = HISTORY_START[tf]
    print(f"Fetching {tf} from {start.date()}...")
    candles = fetch_extended_history(timeframe=tf, start=start, instrument=instrument)
    print(f"  Got {len(candles)} candles, saving...")
    save_candles(candles, pair=pair)
    print(f"  Done: {tf}")
```

This will take several minutes for intraday timeframes due to pagination. Report progress per timeframe.

### 6. Data Alignment Comparison

After backfill, compare the new asset's data against existing assets. The comparison must account for the fact that different asset classes have different trading hours (e.g. indices trade fewer hours than 24h forex), so we compare **within the shared date range** rather than raw totals.

```python
import sqlite3

conn = sqlite3.connect("storage/trading.db")
cur = conn.cursor()

new_pair = "{NAME}=X"
ref_pair = "EURUSD=X"

# Get date ranges for the new asset
print(f"=== {new_pair} Data Summary ===")
for tf in ["15m", "1h", "4h", "1d", "1wk"]:
    cur.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles WHERE pair=? AND timeframe=?", (new_pair, tf))
    count, min_ts, max_ts = cur.fetchone()
    print(f"  {tf:5} {count:7} candles  {min_ts} to {max_ts}")

# Compare within shared date range (the new asset's date range)
print(f"\n=== Alignment: {new_pair} vs {ref_pair} (within shared date range) ===")
print(f"{'TF':5} {'New':>7} {'Ref(overlap)':>13} {'Shared':>7} {'NewCovered':>11} {'Status'}")
print("-" * 60)

for tf in ["15m", "1h", "4h", "1d", "1wk"]:
    # Get the new asset's date range
    cur.execute("SELECT MIN(timestamp) FROM candles WHERE pair=? AND timeframe=?", (new_pair, tf))
    start = cur.fetchone()[0]
    if not start:
        print(f"{tf:5} NO DATA")
        continue

    # Count new asset candles
    cur.execute("SELECT COUNT(*) FROM candles WHERE pair=? AND timeframe=?", (new_pair, tf))
    new_count = cur.fetchone()[0]

    # Count reference candles within new asset's date range
    cur.execute("SELECT COUNT(*) FROM candles WHERE pair=? AND timeframe=? AND timestamp >= ?",
                (ref_pair, tf, start))
    ref_count = cur.fetchone()[0]

    # Count shared timestamps within the range
    cur.execute("""SELECT COUNT(*) FROM candles a
                   INNER JOIN candles b ON a.timestamp = b.timestamp AND a.timeframe = b.timeframe
                   WHERE a.pair=? AND b.pair=? AND a.timeframe=? AND a.timestamp >= ?""",
                (new_pair, ref_pair, tf, start))
    shared = cur.fetchone()[0]

    # "New covered" = what % of the new asset's candles have a matching reference timestamp
    # This catches missing data. It's expected to be <100% for non-forex (fewer trading hours).
    new_covered = (shared / new_count * 100) if new_count > 0 else 0

    # For 1d/1wk, expect >95%. For intraday, >90% means the timestamps align well
    # (indices/commodities may have fewer candles per day than forex, which is fine)
    threshold = {"15m": 90, "1h": 90, "4h": 90, "1d": 95, "1wk": 95}[tf]
    status = "OK" if new_covered >= threshold else "LOW"

    print(f"{tf:5} {new_count:7} {ref_count:13} {shared:7} {new_covered:10.1f}% {status}")

# Hour distribution check for 1d/1wk (must be 21/22 only, no 00:00 Yahoo artifacts)
print()
print("=== Hour Distribution (1d/1wk should be 21/22 only) ===")
for tf in ["1d", "1wk"]:
    cur.execute("""SELECT substr(timestamp,12,2) as hour, COUNT(*)
                   FROM candles WHERE pair=? AND timeframe=?
                   GROUP BY hour ORDER BY hour""", (new_pair, tf))
    hours = cur.fetchall()
    bad_hours = [h for h, c in hours if h not in ("21", "22")]
    print(f"{new_pair} {tf}: {hours} {'BAD - has non-OANDA hours!' if bad_hours else 'OK'}")

conn.close()
```

**Interpreting results:**
- "NewCovered" shows what percentage of the new asset's candles share a timestamp with EURUSD. This should be >90% for intraday and >95% for daily/weekly.
- If the new asset is a different asset class (e.g. index vs forex), it will have fewer intraday candles per day — that's expected and not a data issue. The key is that the timestamps it DOES have should overlap with the reference.
- If "NewCovered" is low (<90%), that suggests a real data issue (wrong timestamps, gaps, etc.).

### 7. Diagnostics & Gap Filling

If Step 6 shows issues:

**Low overlap:** Identify the missing date ranges and re-fetch:
```python
# Find timestamps in reference but not in new asset
cur.execute("""SELECT MIN(c1.timestamp), MAX(c1.timestamp), COUNT(*)
               FROM candles c1
               WHERE c1.pair=? AND c1.timeframe=?
               AND c1.timestamp NOT IN (SELECT timestamp FROM candles WHERE pair=? AND timeframe=?)""",
            (ref_pair, tf, new_pair, tf))
```
Then use `fetch_candles(pair=..., timeframe=..., start=..., end=...)` to fill the specific gaps.

**Bad hour distribution (00:00 timestamps):** This means Yahoo data leaked in. Purge and re-fetch:
```python
# Purge non-OANDA timestamps
cur.execute("""DELETE FROM candles WHERE pair=? AND timeframe=?
               AND substr(timestamp,12,2) NOT IN ('21','22')""", (new_pair, tf))
conn.commit()
# Then re-fetch from OANDA using fetch_extended_history
```

Re-run the comparison after fixing to confirm alignment is now clean.

### 8. Summary

Print the final onboarding report:

```
=== Onboarding Complete: {NAME} ===

Asset Spec:
  Name:             {NAME}
  OANDA Instrument: {OANDA_CODE}
  Pip Value:        {pip_value}
  Price Decimals:   {price_decimals}
  Lot Size:         {lot_size}
  Asset Class:      {asset_class}

Data Summary:
  15m:  XXXXX candles (from YYYY-MM-DD to YYYY-MM-DD)
  1h:   XXXXX candles (from YYYY-MM-DD to YYYY-MM-DD)
  4h:   XXXXX candles (from YYYY-MM-DD to YYYY-MM-DD)
  1d:   XXXXX candles (from YYYY-MM-DD to YYYY-MM-DD)
  1wk:  XXXXX candles (from YYYY-MM-DD to YYYY-MM-DD)

Alignment: OK (all timeframes within threshold)

The asset is now available in the dashboard via the pair selector dropdown.
```

## Important Rules

- **OANDA only**: Never fetch from Yahoo Finance. All data must come from OANDA to ensure consistent 21:00/22:00 UTC timestamps.
- **No asset limit**: The system supports unlimited assets. Never reject an onboard because "too many assets exist."
- **Use existing functions**: Always use `fetch_extended_history()` for history (handles 5000-candle pagination) and `save_candles()` for storage (handles upsert/dedup).
- **Match existing depth**: Use `MAX_HISTORY_DAYS` from settings so all assets have comparable history ranges.
- **Verify before done**: Always run the alignment comparison. Don't skip it even if the backfill "looked fine."
