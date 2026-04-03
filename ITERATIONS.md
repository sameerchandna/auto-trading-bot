# Iteration Log

| # | Change | Config | Trades | WR | PF | P&L | DD | Sharpe |
|---|--------|--------|--------|----|----|-----|----|----|
| 1 | Baseline — multi-TF confluence scoring, HTF bias via combined daily+weekly | baseline | 88 | 40.9% | 1.28 | £3,600 | 27.2% | 0.01 |
| 2 | Filter: no-overlap + block evening hours (17-20) | no-overlap, block-h[17-20] | 53 | 45.3% | 1.54 | £3,747 | 18.8% | -0.05 |
| 3 | Filter: no-overlap (no same-direction entry while in position) | no-overlap | 55 | 45.5% | 1.56 | £3,978 | 19.0% | -0.03 |
| 4 | Filter: raise min confluence score to 0.70 | score>=0.70 | 14 | 42.9% | 1.43 | £756 | 9.1% | -0.72 |
| 5 | Filter: block evening hours (17-20) | block-h[17-20] | 77 | 42.9% | 1.38 | £4,164 | 23.8% | 0.02 |
| 6 | Filter: cooldown 2 candles after consecutive losses | cooldown-2 | 63 | 39.7% | 1.20 | £1,861 | 24.6% | -0.11 |
| 7 | Filter: block Thursday + Friday trading | block-Thu+Fri | 60 | 35.0% | 1.01 | £107 | 25.7% | -0.29 |
| 8 | **Split HTF bias** — score daily & weekly individually instead of combined; daily leading weekly ranging now scores 0.8 instead of 0.3 | baseline | 63 | 52.4% | 1.98 | £7,474 | 17.3% | 0.20 |
| 9 | BT#9/10 — reruns of #8 (identical results, duplicate DB entries) | baseline | 63 | 52.4% | 1.98 | £7,474 | 17.3% | 0.20 |
| 10 | **BT/Pine sync** — step=1 (was 4), threshold=0.30 (was 0.60), SL=1.0xATR (was 1.5), optimized weights, daily reset bug fix | no-overlap, block-h[17-20] | 1,677 | 41.1% | 1.30 | £60,633 | 34.4% | 0.15 |
| 11 | Param sweep: thr=0.40, SL=1.0 | thr=0.40 | 1,078 | 41.0% | 1.27 | £31,716 | 39.8% | 0.01 |
| 12 | Param sweep: thr=0.30, SL=1.0 (rerun of #10) | thr=0.30 | 1,677 | 41.1% | 1.30 | £60,633 | 34.4% | 0.15 |
| 13 | Param sweep: thr=0.45, SL=1.0 | thr=0.45 | 833 | 43.3% | 1.38 | £28,385 | 41.8% | -0.01 |
| 14 | Param sweep: thr=0.50, SL=1.0 | thr=0.50 | 521 | 44.0% | 1.46 | £23,167 | 34.3% | -0.06 |
| 15 | Param sweep: thr=0.60, SL=1.0 | thr=0.60 | 227 | 42.7% | 1.36 | £9,187 | 28.6% | -0.35 |
| 16 | Param sweep: thr=0.45, SL=1.5 | thr=0.45, SL=1.5 | 561 | 42.8% | 1.45 | £31,608 | 31.7% | -0.00 |
| 17 | Param sweep: thr=0.30, SL=1.5 | thr=0.30, SL=1.5 | 1,067 | 39.1% | 1.26 | £40,796 | 37.4% | 0.05 |
| 18 | Param sweep: thr=0.50, SL=1.5 | thr=0.50, SL=1.5 | 368 | 43.2% | 1.48 | £21,403 | 29.0% | -0.09 |
| 19 | Param sweep: thr=0.60, SL=1.5 | thr=0.60, SL=1.5 | 174 | 42.0% | 1.41 | £8,650 | 20.5% | -0.36 |
| 20 | Param sweep: thr=0.40, SL=1.5 | thr=0.40, SL=1.5 | 698 | 39.8% | 1.27 | £26,077 | 32.1% | -0.04 |
| 21 | **Production config** — threshold 0.30→0.45, SL 1.0→1.5xATR (param sweep winner) | no-overlap, block-h[17-20] | 561 | 42.8% | 1.45 | £31,608 | 31.7% | -0.00 |
| 22 | **Drop 15m candles** — exclude 15m TF from analysis to match PineScript (1H entry) | no-overlap, block-h[17-20], no-15m | 371 | 40.4% | 1.36 | £22,392 | 31.7% | -0.08 |

**Notes:**
- BT#1-7 used combined `get_htf_bias()` which suppressed all short trades (0 shorts across all runs)
- BT#8 onwards uses split daily/weekly scoring — unlocked 13 short trades (7W/6L, £1,330 P&L)
- BT#8 vs BT#1: +108% P&L, -10% DD, +12% WR with 25 fewer trades — higher quality entries
- BT#9 onwards: BT engine synced with PineScript — single source of truth via `config/optimized_params.json`
  - Fixed: step=1 (every 1H candle, was step=4), threshold from params (was hardcoded 0.60), SL/TP from params, daily reset bug
- BT#11-20 (rows 11-20): 10-scenario param sweep (5 thresholds × 2 SL multipliers). All use no-overlap + block-h[17-20]. Winner: thr=0.45, SL=1.5xATR (best expectancy at 4.0p/trade)
- BT#21 (row 21) = BT#22 in DB: production run confirming sweep winner with full filters
