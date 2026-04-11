# Plan: `backtest-folds` — Walk-Forward Multi-Fold Backtest Command

## Status
- **Phase 1: SHIPPED** (commit `c7f32fa`, 2026-04-09). Command live, tests green, smoke + parallel + kfold_shuffled runs verified against a single-range BT over the same window. Saved as `backtest_folds_runs` id 1/2/3. Parallel wall time ~36s vs single-range ~111s. Headline on EURUSD baseline post overfitting-fix: 11/13 complete quarters profitable, Sharpe mean 1.87, combined OOS £61.5k on £10k (PF 1.35, DD 13.7%).
- **Phase 2: SHIPPED** — fit-on-IS / test-on-OOS via `--optimize`/`--trials`. `backtest/folds/optimizer.py` runs Optuna (TPESampler seed=42) per fold with `params_override` (no global mutation); folds with `IS < MIN_IS_DAYS=180` fall back to baseline. Aggregator reports IS→OOS degradation and param drift. Confirmed scale-linear with IS length (180d→376s, 546d→1328s per fold).
- **Dashboard UI for fold runs: SHIPPED** — Folds tab with list table + click-through detail (combined equity Plotly chart, per-fold table, param drift block). `/api/folds` + `/api/folds/{id}`. `BacktestRecord.fold_parent_id` filter keeps fold children out of the main Backtests tab.
- **Deferred (not requested):** incremental persistence so interrupted fold runs are salvageable; rolling IS window option to cap per-fold cost instead of expanding IS each quarter.

## Context

Backtest engine was recently patched for overfitting bugs (commit `e33c8cb`: lookahead removed, daily-resampled Sharpe/Sortino, fixed drawdown, live learner paused). All prior single-range backtest numbers are no longer comparable.

Beyond rerunning, we want a **reusable robustness framework**: instead of judging a strategy from one contiguous 2024–2025 run, slice the usable data (2023-01-01 → latest available candle, currently ~2026-04; 15m/1h history floor is 2023-01-01, upper bound grows with ingestion) into many **rolling quarterly OOS windows**, run the baseline on each in **parallel**, and aggregate per-fold metrics plus a combined OOS equity curve. This tells us whether the strategy is consistent across regimes or just lucky in one window.

Scope is **validation-only** (phase 1): no per-fold refitting — just run the current baseline params on each window and report consistency. A later phase will add fit-on-IS / test-on-OOS using the same splitter + aggregator with zero rework.

Framework must be **reusable** across pairs and strategies: pair comes from `--pair`, strategy params from the standard `optimized_params.json` loader, split scheme selectable.

## Design decisions (already agreed with user)

- **Command name**: `backtest-folds` (new typer command, does NOT replace `backtest`)
- **Default scheme**: rolling quarterly walk-forward over `[--start, --end]`. Fold count is **dynamic**, computed from the date range at runtime — today that's 2023Q1 through 2026Q2 (partial) = 14 folds. Tomorrow's run may still be 14, next quarter it becomes 15. Nothing in the code hardcodes a fold count or an end year.
- **Alternate scheme**: `kfold_shuffled` (yearly IS/OOS permutations, for curiosity/comparison)
- **Parallelism**: `concurrent.futures.ProcessPoolExecutor`; each fold = one worker
- **Phase 1 = validation-only**: IS is informational (period label) only; we do not refit
- **Data window**: `--start` defaults to `2023-01-01` (15m/1h history floor from `config/settings.py:83-89`); `--end` defaults to **"latest available candle in DB for this pair"**, queried at command start. Not hardcoded — every run auto-extends as ingestion appends new data. User can still override `--end` for reproducible historical runs.
- **Partial final fold**: the splitter emits a final `partial=True` fold for the in-progress quarter (e.g. today 2026-04-09 → OOS 2026-04-01..2026-04-09). Partial folds are displayed but excluded from summary stats (mean/median/% profitable) to avoid low-sample noise.
- **Min-trades guard**: folds with `< MIN_TRADES_PER_FOLD` (default 20) are marked `insufficient_data`, shown in the per-fold table, excluded from summary aggregation. Mirrors `research/backtest_runner.py:28` pattern.
- **Cumulative re-runs**: each invocation creates a new `backtest_folds_runs` parent row — running weekly gives a history of how the robustness picture evolves as new candles arrive. No mutation of prior rows.
- **Results**: persisted to SQLite so dashboard can later surface them

## Files to create

### 1. `backtest/folds/__init__.py`
Package marker.

### 2. `backtest/folds/splits.py`
```python
@dataclass
class Fold:
    fold_id: str          # "2023Q1", "2026Q2", ...
    is_start: date        # informational for phase 1
    is_end: date
    oos_start: date
    oos_end: date
    label: str            # "OOS:2023Q1"
    partial: bool = False # True if oos_end < quarter's natural end (current quarter)

def walk_forward_quarterly(start: date, end: date) -> list[Fold]:
    """Emits one fold per quarter touching [start, end]. The final quarter
    is emitted with partial=True if end < quarter_end."""

def kfold_shuffled_yearly(start: date, end: date) -> list[Fold]: ...

def latest_candle_date(pair: str) -> date:
    """Query the candles table for max(timestamp) on the primary timeframe
    for this pair. Used as --end default when user doesn't specify."""

SCHEMES = {
    "walkforward": walk_forward_quarterly,
    "kfold_shuffled": kfold_shuffled_yearly,
}
```
Walk-forward quarterly produces: OOS=Qn of year Y, IS=all prior data within the usable window. Pure-function, no I/O, trivially unit-testable.

### 3. `backtest/folds/runner.py`
```python
def run_single_fold(args) -> dict:
    """Top-level picklable worker. Constructs BacktestEngine for OOS window,
    calls .run(), returns {fold_id, label, metrics, equity_curve, trades_json}."""

def run_folds(
    pair: str, folds: list[Fold], capital: float,
    config: BacktestConfig, max_workers: int | None = None,
) -> list[dict]:
    """ProcessPoolExecutor.map over folds. Returns list of fold result dicts."""
```
Uses existing `BacktestEngine(start_date, end_date, capital, config, pair)` from `backtest/engine.py:33-52`. Engine already constructs a fresh `RiskManagerAgent` per instance (line 51), so workers are independent. `run()` returns a pure dict (`backtest/engine.py:60-223`), picklable.

Must verify worker-side: SQLite connections, cached data fetch. Plan: in `run_single_fold`, import and construct engine inside the worker (no shared state passed across process boundary beyond config dataclass + dates + pair string).

### 4. `backtest/folds/aggregator.py`
```python
def aggregate(fold_results: list[dict], initial_capital: float) -> dict:
    """Returns:
      per_fold: list of {fold_id, label, metrics_subset}
      summary: mean/median/std/min/max across folds for sharpe, pf, win_rate, dd, pnl
      pct_profitable_folds: float
      combined_equity_curve: list[(ts, value)]  # concatenated OOS segments,
                                                # each fold starts from prior fold's ending value
      combined_metrics: dict  # calculate_metrics() on concatenated trades + curve
    """
```
Reuses `backtest/metrics.py:calculate_metrics()` (trades list + equity curve → 19 metrics) — confirmed it accepts arbitrary trade subsets / curves.

### 5. `backtest/folds/report.py`
Rich console output: per-fold table (fold_id, trades, win_rate, PF, Sharpe, DD, P&L), summary block, % profitable folds, pointer to DB run id.

### 6. `storage/database.py` — new table `BacktestFoldsRun` (parent)
```python
class BacktestFoldsRun(Base):
    __tablename__ = "backtest_folds_runs"
    id: int PK
    pair: str
    scheme: str                  # "walkforward" | "kfold_shuffled"
    num_folds: int
    start_date, end_date: str
    params_json: str             # baseline params used
    summary_json: str            # aggregator.summary
    combined_metrics_json: str
    combined_equity_curve_json: str
    label: str
    created_at: datetime
```
Each child fold is saved as a normal `BacktestRecord` row (existing table at `storage/database.py:102-116`) tagged with `fold_parent_id` = the new parent's id. **Minimal change**: add nullable `fold_parent_id: int | None` column to `BacktestRecord`. Positions already tagged `bt_<id>` (existing pattern from `main.py:282`) — no change to `positions`.

Schema migration: existing repo appears to use `Base.metadata.create_all()` style — confirm and add the new table + column there. If there's an Alembic setup, use it; otherwise a one-off ALTER TABLE for the new column guarded by a try/except (sqlite).

### 7. `main.py` — new typer command (~lines 246+)
```python
@app.command("backtest-folds")
def backtest_folds(
    pair: str = DEFAULT_ASSET,
    scheme: str = "walkforward",
    start: str = "2023-01-01",
    end: str | None = None,   # None → latest_candle_date(pair)
    capital: float = 10_000,
    workers: int | None = None,          # None → os.cpu_count()-1
    label: str = "",
    # same filter flags as backtest: --no-overlap, --min-score, --block-hours, etc.
):
    folds = SCHEMES[scheme](parse(start), parse(end))
    cfg = build_config_from_flags(...)   # reuse existing helper from backtest cmd
    results = run_folds(pair, folds, capital, cfg, workers)
    agg = aggregate(results, capital)
    parent_id = _save_folds_run(pair, scheme, folds, cfg, agg, results, label)
    render_report(agg, parent_id)
```
Reuses the exact same flag parsing helpers the existing `backtest` command uses (`main.py:189-243`) — extract them into a small `_build_backtest_config(...)` helper if not already factored, so both commands share it.

### 8. Unit tests
- `tests/backtest/folds/test_splits.py` — use fixed synthetic date ranges (not "today") so tests are deterministic. Cases: (a) `walk_forward_quarterly(2023-01-01, 2025-12-31)` → exactly 12 complete folds, none partial, Q4-2023 OOS = 2023-10-01..2023-12-31. (b) `walk_forward_quarterly(2023-01-01, 2026-04-09)` → 13 complete + 1 partial (2026Q2, oos_end=2026-04-09, partial=True). (c) start mid-quarter → first fold oos_start clamped to `start`.
- `tests/backtest/folds/test_aggregator.py` — synthetic fold results → check mean/median/std, pct_profitable, combined curve continuity.

## Files to read/modify (reference)

- `main.py:189-243` — existing `backtest` command, copy flag pattern
- `backtest/engine.py:33-223` — `BacktestEngine` API, confirmed `run()` is picklable dict
- `backtest/metrics.py:8-108` — `calculate_metrics(trades, initial_capital, equity_curve)` reused by aggregator
- `backtest/config.py` — `BacktestConfig`, `BASELINE` — pass into each fold
- `storage/database.py:55-116` — `PositionRecord`, `BacktestRecord` (add `fold_parent_id`); add new `BacktestFoldsRun`
- `config/params.py:14-40` — `load_strategy_params()` (unchanged, read via engine as today)
- `research/backtest_runner.py` — prior walk-forward precedent (sequential); confirms the pattern but we don't reuse it directly because its purpose is parameter research, not validation reporting
- `dashboard/app.py:326-372` — `/api/backtests`; dashboard integration is **out of scope for this plan** but schema leaves room for it

## Out of scope (explicitly deferred)

- Incremental persistence for fold runs (salvage partial results on interrupt)
- Rolling IS window option (cap per-fold optimize cost)
- Live learner re-enable

## Verification

1. **Unit tests**: `pytest tests/backtest/folds -v` — splits + aggregator math.
2. **Smoke run, single worker**:
   `python main.py backtest-folds --pair EURUSD --workers 1 --label smoke`
   → expect 12 fold rows in console, summary block, parent row in `backtest_folds_runs`, 12 child rows in `backtest_runs` with `fold_parent_id` set.
3. **Parallel run**:
   `python main.py backtest-folds --pair EURUSD --label baseline-post-overfit-fix`
   → wall time meaningfully lower than `--workers 1`; identical numerical results.
4. **Sanity check the baseline** (the whole reason we're doing this): compare `combined_metrics` from the run in step 3 against a single-range run over the same dates, e.g. `python main.py backtest --start 2023-01-01 --end <latest>` where `<latest>` matches what `backtest-folds` auto-resolved. They should be in the same ballpark; a large divergence is itself interesting.
5. **Scheme switch**:
   `python main.py backtest-folds --scheme kfold_shuffled --pair EURUSD`
   → runs, produces 6 yearly IS/OOS permutations, no errors.
6. **DB inspection**: `sqlite3 storage/trading.db "select id,pair,scheme,num_folds,label from backtest_folds_runs;"` and confirm child linking: `select id, fold_parent_id, label from backtest_runs where fold_parent_id is not null;`.
