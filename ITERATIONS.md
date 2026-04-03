# Iteration Log

| # | Change | Config | Trades | WR | PF | P&L | DD | Sharpe |
|---|--------|--------|--------|----|----|-----|----|----|
| 22 | **Drop 15m candles** — exclude 15m TF from analysis to match PineScript (1H entry) | no-overlap, block-h[17-20], no-15m | 371 | 40.4% | 1.36 | £22,392 | 31.7% | -0.08 |

**Notes:**
- BT#22 = DB run 22: production config (thr=0.45, SL=1.5xATR), no-overlap + block-h[17-20]
- BT#23 = DB run 23: drop 15m candles to match PineScript 1H entry logic
