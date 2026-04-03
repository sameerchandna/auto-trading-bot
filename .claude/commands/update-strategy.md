# Update Strategy

Run the full strategy update pipeline. This ensures the backtest engine, PineScript indicator, and dashboard all stay in sync.

## Steps

Follow these steps in order. Report progress as you go.

### 1. Confirm the change

Ask the user what strategy change they want to make. This could be:
- Updating parameters in `config/optimized_params.json` (weights, threshold, SL/TP, swing lookback)
- Changing confluence logic in `analysis/confluence.py`
- Updating filters in `backtest/config.py`
- Any other strategy-related code change

Make the requested code changes.

### 2. Sync PineScript

Run `python -m tradingview.generate_pine` to regenerate the PineScript indicator from the current `config/optimized_params.json`. If the strategy change was to confluence logic (not just params), manually update the corresponding PineScript scoring section in `tradingview/bot_indicator.pine` to match.

Verify sync: `python -m tradingview.generate_pine --check`

### 3. Log the change to ITERATIONS.md

**Do NOT run the backtest yet.** First, prepare the iteration log entry:
- Read ITERATIONS.md to find the last iteration number
- Prepare a new row with the change description and config label
- Leave metrics columns as `pending` — they'll be filled after the backtest

### 4. Run backtest on full available data

Run the backtest using the current production config:
```bash
python main.py backtest --start 2023-01-01 --no-overlap --block-hours "17,18,19,20" --min-score 0.30
```

Wait for it to complete. This may take a few minutes.

### 5. Update ITERATIONS.md with results

Parse the backtest output and fill in the metrics for the new iteration row:
- Trades, WR, PF, P&L, DD, Sharpe

### 6. Update optimized_params.json backtest_results

Update the `backtest_results` section in `config/optimized_params.json` with the new backtest metrics.

### 7. Restart dashboard

Kill any running dashboard process and restart it:
```bash
# Find and kill existing dashboard
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *dashboard*" 2>/dev/null || true
# Or more targeted:
# ps aux | grep "main.py dashboard" | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null || true

# Start fresh
python main.py dashboard &
```

### 8. Summary

Print a summary showing:
- What changed
- Before/after comparison (if this is an iteration on existing params)
- New backtest metrics
- Remind user to copy the updated PineScript to TradingView if params changed
