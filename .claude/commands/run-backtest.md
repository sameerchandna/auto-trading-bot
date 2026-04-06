# Run Backtest

Run backtests for one pair, a list of pairs, or all active pairs. Uses the production config by default. Runs pairs in parallel via sub-agents. Updates ITERATIONS.md and restarts the dashboard when done.

## Input

Determine the scope from the user's message:
- **Single pair** — e.g. "run backtest for EURUSD"
- **Multiple pairs** — e.g. "run backtest for EURUSD and XAUUSD"
- **All pairs** — e.g. "run backtest for all pairs" → read `ACTIVE_ASSETS` from `config/assets.py`

If no pair is specified, default to EURUSD only.

Also note any custom flags the user provides (e.g. `--start`, `--min-score`). If none, use the production defaults.

---

## Key Files (no searching needed)

| What | Where |
|------|-------|
| CLI entry point | `main.py` |
| Backtest engine | `backtest/engine.py` |
| Backtest config classes | `backtest/config.py` — `BacktestConfig`, pre-built configs: `BASELINE`, `PRODUCTION`, etc. |
| Params (weights, threshold, SL/TP) | `config/optimized_params.json` — loaded at runtime via `config/params.py:load_strategy_params()` |
| Asset registry | `config/assets.py` — `ASSETS` dict and `ACTIVE_ASSETS` list |
| Global settings | `config/settings.py` — `STARTING_CAPITAL`, `TIMEFRAMES`, `HISTORY_START` |
| Confluence / signal logic | `analysis/confluence.py` |
| Risk manager | `agents/risk_manager.py` |
| Data cache | `storage/trading.db` — `candles` table |
| Backtest results DB | `storage/trading.db` — `backtest_runs` table, `positions` table (tagged `bt_<id>`) |
| Iteration log | `ITERATIONS.md` |
| Dashboard app | `dashboard/app.py` — served by `python main.py dashboard` on port 8050 |

---

## Production Backtest Command

This is the standard production config. Use it unless the user specifies otherwise:

```bash
python main.py backtest --pair {PAIR} --start 2023-01-01 --no-overlap --block-hours "17,18,19,20"
```

Flags explained:
- `--pair` — canonical asset name: EURUSD, XAUUSD, US30, AUDUSD, WTICO
- `--start` — history start date (default 2023-01-01 for intraday-safe history)
- `--no-overlap` — blocks same-direction entries if a position is already open
- `--block-hours "17,18,19,20"` — skips entries during illiquid UTC hours

Optional overrides the user may request:
- `--end YYYY-MM-DD` — backtest end date (default: today)
- `--capital N` — starting capital in GBP (default: 10000)
- `--min-score 0.50` — extra confluence filter
- `--block-days "thu,fri"` — skip specific weekdays
- `--cooldown N` — skip N signals after N consecutive losses
- `--label "my-label"` — custom DB label
- `--no-overlap` / no flag — toggle same-direction filter

---

## Steps

Follow these steps in order. Report progress after each one.

### 1. Resolve target pairs

If "all pairs", read `config/assets.py` and collect every name in `ACTIVE_ASSETS`. Otherwise, use the pair(s) from the user's message.

Print the resolved list before continuing:
```
Running backtests for: EURUSD, XAUUSD, US30
```

### 2. Run backtests (parallel for multiple pairs)

**Single pair:** run the backtest command directly in a Bash call and wait for output.

**Multiple pairs:** launch one Agent sub-agent per pair **in parallel** (single message, multiple tool calls). Each sub-agent should:
1. Run: `python main.py backtest --pair {PAIR} --start 2023-01-01 --no-overlap --block-hours "17,18,19,20"` (plus any user overrides)
2. Capture the full terminal output
3. Return the output including the BT# line, trade count, win rate, P&L, drawdown, Sharpe

Wait for all sub-agents to complete before continuing.

Capture the BT# assigned to each pair (printed as `Saved to database: BT#NN`). You'll need these for the next steps.

### 3. Parse results

From each backtest output, extract:
- BT# (e.g. BT#24)
- Config label (e.g. "no-overlap, block-h[17,18,19,20]")
- Start/end date range
- Total trades
- Win rate (%)
- Profit factor
- Total P&L (£)
- Max drawdown (%)
- Sharpe ratio
- Expectancy (pips/trade)

If any backtest failed or produced 0 trades, report the error and skip that pair in subsequent steps.

### 4. Update ITERATIONS.md

Read `ITERATIONS.md` to find the last iteration number. Add a new row per pair that completed successfully.

Format: follow the exact table format already in ITERATIONS.md — same columns, same alignment. Fill all metric columns with real values (not `pending`). One row per pair.

If multiple pairs, add them as consecutive rows with sequential iteration numbers.

### 5. Update optimized_params.json backtest_results

For the primary pair (EURUSD if it ran, otherwise the first pair), update the `backtest_results` section in `config/optimized_params.json` with the new metrics:

```json
"backtest_results": {
  "bt_id": "BT#NN",
  "period": "2023-01-01 to YYYY-MM-DD",
  "trades": NNN,
  "win_rate": N.NN,
  "profit_factor": N.NN,
  "total_pnl": NNNN.NN,
  "max_drawdown": NN.N,
  "sharpe_ratio": N.NN
}
```

Skip this step if EURUSD was not in the run.

### 6. Kill dashboard and restart

```bash
# Kill any running dashboard (Windows)
taskkill /F /FI "WINDOWTITLE eq *dashboard*" /IM python.exe 2>/dev/null || true
# Fallback: kill any python process on port 8050
for pid in $(netstat -ano 2>/dev/null | grep ":8050" | awk '{print $NF}' | sort -u); do
  taskkill /F /PID $pid 2>/dev/null || true
done

# Start dashboard in background
python main.py dashboard &
```

Wait ~3 seconds for the server to start, then confirm it's up by noting the URL.

Dashboard URL: **http://127.0.0.1:8050**

Relevant dashboard views after a backtest run:
- **Backtests tab** — shows all runs including the new BT#(s), sortable by pair/date
- **Trades tab** — filter by `bt_id` to see individual trades for a specific run
- **Comparisons tab** — side-by-side comparison of runs (grouped by period and pair)

### 7. Summary

Print a clean summary table:

```
=== Backtest Complete ===

Pair     BT#   Trades  WR%   PF    P&L       DD%   Sharpe
------   ----  ------  ----  ----  --------  ----  ------
EURUSD   BT#24  NNN    NN.N  N.NN  £NNNNN    NN.N  N.NN
XAUUSD   BT#25  NNN    NN.N  N.NN  £NNNNN    NN.N  N.NN

ITERATIONS.md — updated (rows NN–NN added)
optimized_params.json — updated (EURUSD BT#24)
Dashboard — restarted at http://127.0.0.1:8050
  → Backtests tab: all runs visible
  → Trades tab: filter by bt_id to drill into trades
  → Comparisons tab: grouped side-by-side
```

---

## Notes

- **Don't re-fetch data** before running backtests unless the user asks. The DB cache is assumed current.
- **Production config is the default.** If the user wants a different config, they'll say so explicitly.
- **Parallel = one Agent per pair.** Never loop sequentially when multiple pairs are requested.
- **ITERATIONS.md row format** — always match the existing column order exactly. Read the file first.
- **BT# is assigned by the DB** — don't predict it. Parse it from the output.
- If a pair's backtest errors out, log it clearly in the summary and continue with the others.
