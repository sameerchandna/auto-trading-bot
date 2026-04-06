# Code Review — Fix List #1

Source: Claude.ai chat review, 2026-04-06

---

## Critical Issues

**1. Confluence can generate opposing signals simultaneously**
`score_confluence` evaluates LONG then SHORT independently. If both score above threshold, you get buy and sell signals at the same time. The `no_overlap` filter only blocks same-direction duplicates — it doesn't handle this. In backtesting, both could get executed.

**2. Catalyst score is permanently zero**
```python
scores["catalyst"] = 0.0
```
Whatever weight you've assigned to "catalyst" in your config is dead weight right now. This silently reduces your effective threshold. If catalyst weight is 15%, your actual scoring ceiling is 85% not 100% — meaning your threshold is miscalibrated relative to what you think it is.

**3. Signal type is always the same regardless of what triggered**
```python
signal_type = SignalType.BOS_CONTINUATION  # Default
```
This is a placeholder that was never finished. You're losing the ability to analyse which signal *type* performs best — BOS vs CHoCH vs liquidity sweep entries are meaningfully different setups.

**4. SL/TP is ATR-based, not structure-based**
For an SMC strategy, this is a philosophical mismatch. Your stop should be below the sweep low or above the BOS level — not an arbitrary ATR multiple from entry. ATR-based stops will frequently be placed inside noise rather than beyond the invalidation point.

---

## Significant Issues

**5. Lookahead bias risk in the backtest**
The `_bisect_candles` logic correctly finds candles up to `current_date`, but `build_price_context` receives the current unfinished candle. In real trading you don't know that candle's close until it closes. Whether this matters depends on what `build_price_context` does internally — worth verifying.

**6. Overfitting risk is high**
You have block_hours, block_days, cooldown, min_score, no_overlap, and Optuna optimising weights — all tunable against historical data. Each parameter you fit reduces out-of-sample validity. You have no out-of-sample validation mentioned anywhere. The `LearnerAgent` is particularly dangerous if it's adjusting weights on live results without a holdout set.

**7. OANDA data limits your backtest validity**
OANDA history is limited — typically 6 months for 15m, 2-3 years for 1H. With 50-candle warmup burned off, you're potentially running backtests on a very thin sample. For 15m specifically, you may not have enough trades to draw statistically valid conclusions.

**8. `save_candles` does N individual DB queries**
One `SELECT` per candle to check for duplicates. For large fetches this will be extremely slow. Should be a bulk upsert.

---

## Minor Issues

- `self._tracked_closed_count` is lazily initialised with `hasattr` — should be in `__init__`
- `trigger_timeframe="4h"` is hardcoded in `_build_signal` — should reflect what actually triggered
- Pipeline's `apply_optimized_params()` silently swallows failures — you could be running on default params indefinitely without knowing

---

## Priority Order to Fix

1. Fix the simultaneous long/short signal problem
2. Fix catalyst or remove its weight entirely
3. Fix signal_type to reflect actual trigger
4. Move SL/TP to structure-based (sweep lows/highs) not ATR
5. Add out-of-sample validation before trusting the learner
6. Fix the bulk upsert in save_candles
