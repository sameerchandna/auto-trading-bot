# Iteration Log

| # | Change | Config | Trades | WR | PF | P&L | DD | Sharpe |
|---|--------|--------|--------|----|----|-----|----|----|
| 22 | **Drop 15m candles** — exclude 15m TF from analysis to match PineScript (1H entry) | no-overlap, block-h[17-20], no-15m | 371 | 40.4% | 1.36 | £22,392 | 31.7% | -0.08 |
| 24 | **Production run — EURUSD** (2026-04-03 multi-asset sweep) | no-overlap, block-h[17-20] | 551 | 41.6% | 1.34 | £26,258 | 46.2% | -0.04 |
| 25 | **Production run — AUDUSD** (2026-04-03 multi-asset sweep) | no-overlap, block-h[17-20] | 530 | 35.8% | 1.11 | £7,107 | 60.3% | -0.17 |
| 26 | **Production run — XAUUSD** (2026-04-03 multi-asset sweep) | no-overlap, block-h[17-20] | 472 | 42.4% | 1.41 | £91,841 | 352.7% | 0.21 |
| 27 | **Production run — US30** (2026-04-03 multi-asset sweep) | no-overlap, block-h[17-20] | 383 | 39.2% | 1.43 | £20,852 | 32.6% | -0.14 |
| 28 | **Production run — WTICO** (2026-04-03 multi-asset sweep) | no-overlap, block-h[17-20] | 427 | 37.2% | 1.18 | £13,632 | 99.8% | -0.11 |

**Notes:**
- BT#22 = DB run 22: production config (thr=0.45, SL=1.5xATR), no-overlap + block-h[17-20]
- BT#23 = DB run 23: drop 15m candles to match PineScript 1H entry logic
- BT#24 = EURUSD production run, 2026-04-03 multi-asset sweep
- BT#25 = AUDUSD production run, 2026-04-03 multi-asset sweep
- BT#26 = XAUUSD production run, 2026-04-03 multi-asset sweep (352.7% DD due to compounding position sizes on large pip moves)
- BT#27 = US30 production run, 2026-04-03 multi-asset sweep
- BT#28 = WTICO production run, 2026-04-03 multi-asset sweep (99.8% DD — near-wipeout risk)
