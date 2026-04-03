# Multi-Asset Backtest Flow & Dashboard Changes

## Context
The bot has expanded from EURUSD-only to 5 active assets (EURUSD, AUDUSD, XAUUSD, US30, WTICO). The backtest CLI and dashboard need to catch up: the backtest command only runs one asset at a time, params are stored in a single global file, the optimizer is hardcoded to EURUSD, and the dashboard has no portfolio-level view across all assets.

**Terminology**: keep `pair` as the internal variable/column name throughout (zero migration risk, already understood). "Asset" only appears in user-facing CLI flags and the new Portfolio UI where it reads more naturally.

---

## Part 1 — Per-Asset Parameter Files

### File convention
Create `config/params/` directory. One JSON file per asset:
```
config/params/EURUSD.json
config/params/XAUUSD.json
config/params/US30.json
config/params/WTICO.json
config/params/AUDUSD.json
```
Bootstrap by copying `config/optimized_params.json` → `config/params/EURUSD.json`.

### Changes to `config/params.py`
Add `pair` param to `load_strategy_params` and `save_strategy_params`. Resolution order in load:
1. `config/params/{pair}.json`
2. `config/optimized_params.json` (legacy fallback — log a warning)
3. Hardcoded defaults from `settings.py`

```python
PARAMS_DIR = Path(__file__).parent / "params"

def load_strategy_params(pair: str = "EURUSD") -> dict:
    asset_file = PARAMS_DIR / f"{pair}.json"
    if asset_file.exists():
        with open(asset_file) as f:
            return _extract_params(json.load(f))
    if PARAMS_FILE.exists():
        logger.warning("Using legacy optimized_params.json; run 'optimize --asset %s'", pair)
        with open(PARAMS_FILE) as f:
            return _extract_params(json.load(f))
    return { ... hardcoded defaults ... }

def save_strategy_params(params: dict, pair: str = "EURUSD", backtest_results: dict | None = None):
    PARAMS_DIR.mkdir(exist_ok=True)
    target = PARAMS_DIR / f"{pair}.json"
    data = {"pair": pair, **params}
    if backtest_results:
        data["backtest_results"] = backtest_results
    with open(target, "w") as f:
        json.dump(data, f, indent=2)
```

### Wire into `backtest/engine.py` line 43
```python
self.params = load_strategy_params(pair=self.pair)   # was load_strategy_params()
```

---

## Part 2 — CLI: Multi-Asset Backtest Command (`main.py`)

### Add shared helper `_resolve_asset_list()`
User-facing flags say "asset"; internally the list is still pairs:
```python
def _resolve_asset_list(asset: str, assets: str, all_assets: bool) -> list[str]:
    if all_assets:
        return list(ACTIVE_ASSETS)
    if assets:
        return [a.strip() for a in assets.split(",")]
    if asset:
        return [asset]
    return [DEFAULT_ASSET]
```

### New flags on `backtest` command
- `--asset EURUSD` — single asset (user-facing name for what was `--pair`)
- `--assets EURUSD,XAUUSD` — comma-separated
- `--all-assets` — run all `ACTIVE_ASSETS`
- `--parallel` — `ThreadPoolExecutor(max_workers=3)` (cap at 3 for Yahoo rate limits)

Extract `_run_backtest_for_asset(pair, start, end, capital, cfg, label) -> dict` from existing body so it can be called from both the sequential loop and the executor.

### Rich summary table after multi-asset run
After all assets complete, print a table: Asset | Trades | Win Rate | P&L | Max DD | Sharpe, with a TOTAL row summing P&L and trades.

### New `optimize` command (mirrors `backtest`)
```
python main.py optimize --asset XAUUSD --trials 50
python main.py optimize --all-assets --trials 30
```

---

## Part 3 — Optimizer (`backtest/optimizer.py`)

Add `pair` param to `objective()` and `run_optimization()`:
```python
def objective(trial, pair: str = "EURUSD") -> float:
    engine = BacktestEngine(..., pair=pair)   # was missing pair

def run_optimization(pair: str = "EURUSD", n_trials: int = 50) -> dict:
    study = optuna.create_study(study_name=f"{pair.lower()}_optimizer", ...)
    study.optimize(lambda t: objective(t, pair=pair), n_trials=n_trials)
    ...
    save_strategy_params(result, pair=pair, backtest_results=result["metrics"])
```

Delegate saving to `config.params.save_strategy_params` (remove inline open/write in optimizer).

**Optional**: add `storage="sqlite:///storage/optuna.db"` to `create_study()` to persist trials across restarts.

---

## Part 4 — Database Cleanup (`storage/database.py`)

No column rename. Just remove hardcoded `default="EURUSD"` from three ORM columns (all callers already handle nulls via `r.pair or DEFAULT_ASSET`):
- `PositionRecord.pair` (line ~60) → `default=None`
- `TradeJournalRecord.pair` (line ~82) → `default=None`
- `BacktestRecord.pair` (line ~105) → `default=None`

Update `_migrate_add_pair_columns` to use `DEFAULT NULL`.

---

## Part 5 — Settings Cleanup (`config/settings.py`, `dashboard/app.py`)

In `dashboard/app.py`:
- `get_live_price(pair: str = PAIR_NAME)` → `pair: str = DEFAULT_ASSET`
- `get_candles(..., pair: str = PAIR_NAME)` → `pair: str = DEFAULT_ASSET`
- Remove `PAIR, PAIR_NAME` from the import

In `config/settings.py`: remove `PAIR` and `PAIR_NAME` (last step, after all imports cleaned).

Existing `?pair=` API query params stay as-is — no breaking change to the existing API contract.

---

## Part 6 — New Dashboard API Endpoint (`dashboard/app.py`)

### `GET /api/portfolio`
Returns the latest backtest result per asset, aggregated into a portfolio view:
```json
{
  "aggregate": { "total_pnl": 44650, "total_trades": 938, "win_rate": 0.433 },
  "per_asset": [
    { "pair": "EURUSD", "total_pnl": 18420, "total_trades": 312,
      "win_rate": 0.433, "max_drawdown": 0.214, "sharpe_ratio": 1.12,
      "profit_factor": 1.67, "last_run": "2026-04-03T10:00:00" },
    ...
  ],
  "best_asset": "EURUSD",
  "worst_asset": "US30"
}
```
Logic: query `BacktestRecord` ordered by timestamp, keep only the most recent run per pair. Sort `per_asset` by `total_pnl` descending.

---

## Part 7 — Dashboard Frontend

### `index.html`
1. Add `<button class="tab" data-tab="portfolio">Portfolio</button>` to nav
2. Add `<section id="portfolio" class="tab-content">` with:
   - Aggregate stats div (`#portfolio-aggregate`)
   - Per-asset breakdown table (`#portfolio-body`) — columns: Asset | Trades | Win Rate | P&L | Profit Factor | Max DD | Sharpe | Last Run
   - Combined equity chart div (`#portfolio-equity-chart`)
3. Add "Asset" column to backtest results table (second column after `#`)
4. Bump cache-bust version on `app.js` and `styles.css` references

### `app.js`

**Asset selector — add "All" button (keep `selectedPair` variable name):**
```javascript
// Prepend before individual asset buttons in loadAssets()
html = `<button class="tf-btn ${selectedPair==='ALL'?'active':''}" data-pair="ALL">All</button>` + html;
```

**`pairFilter` pattern across all fetch calls:**
```javascript
const pairFilter = selectedPair === 'ALL' ? '' : selectedPair;
// Use pairFilter instead of selectedPair in all ?pair= query params
// Applies to: loadEquityCurve, loadRecentSignals, loadTrades, loadChart, loadBacktests
```

**New `loadPortfolio()` function:**
- Fetches `/api/portfolio`
- Renders aggregate stats into `#portfolio-aggregate` (total P&L, trades, win rate, best/worst asset names)
- Renders rows into `#portfolio-body` with colour-coded P&L and drawdown cells
- Calls `loadPortfolioEquity(pairs)` to build combined chart

**New `loadPortfolioEquity(pairs)` function:**
- `Promise.all` fetches `/api/equity?pair=X` for each pair
- Collects all (date, delta) events, sorts by date
- Builds combined curve: start at `10000 * pairs.length`, apply deltas
- Renders via `Plotly.newPlot('#portfolio-equity-chart', ...)`

**Wire `loadTabData`:**
```javascript
case 'portfolio': loadPortfolio(); break;
```

**Backtest table row:** add `<td>${r.pair}</td>` after `<td>${r.id}</td>`.

---

## Implementation Order

1. `config/params.py` — per-asset load/save, `PARAMS_DIR`, `_extract_params` helper
2. `config/params/EURUSD.json` — bootstrap from `optimized_params.json`
3. `backtest/engine.py` line 43 — `load_strategy_params(pair=self.pair)`
4. `storage/database.py` — change 3 × `default="EURUSD"` to `default=None`
5. `backtest/optimizer.py` — add `pair` param, delegate save to `config.params`
6. `main.py` — `_resolve_asset_list`, multi-asset `backtest` flags, new `optimize` command
7. `dashboard/app.py` — `/api/portfolio` endpoint, replace `PAIR_NAME` defaults with `DEFAULT_ASSET`
8. `index.html` — portfolio tab + section, Asset column in backtest table, cache bust
9. `app.js` — "All" button, `pairFilter` pattern, `loadPortfolio`, `loadPortfolioEquity`, wire `loadTabData`
10. `config/settings.py` — remove `PAIR`/`PAIR_NAME`

---

## Verification

```bash
# 1. Single-asset backtest (no regression)
python main.py backtest --asset EURUSD

# 2. All-assets backtest
python main.py backtest --all-assets

# 3. Parallel subset
python main.py backtest --assets EURUSD,XAUUSD --parallel

# 4. Per-asset optimizer
python main.py optimize --asset XAUUSD --trials 10

# 5. Dashboard — Portfolio tab
python main.py dashboard
# Portfolio tab: per-asset table + combined equity chart
# "All" button on asset selector: other tabs show unfiltered data

# 6. Params fallback
rm config/params/EURUSD.json
python main.py backtest --asset EURUSD
# Logs warning, falls back to config/optimized_params.json
```

## Potential Pitfalls

- **SQLite write contention under `--parallel`**: if `OperationalError: database is locked`, add `connect_args={"timeout": 30}` to `create_engine` in `database.py`, or write results sequentially in main thread after executor completes.
- **Portfolio equity chart semantics**: combined curve = N independent £10k accounts summed, not a single shared capital pool. True single-capital allocation requires per-asset weight in the engine — out of scope here.
- **`optimized_params.json` goes stale**: still used as fallback but won't update after first `optimize --asset EURUSD`. Warning log makes this visible.
