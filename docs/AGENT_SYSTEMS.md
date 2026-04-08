# Auto Trading Bot — Agent Systems Architecture
**Repo:** https://github.com/sameerchandna/auto-trading-bot  
**Last updated:** 2026-04-06  
**Purpose:** Reference document for new chat sessions. Captures current state, what has been built, what needs building, and the full design for the two automated agent systems.

---

## Current State (as of 2026-04-06)

### Strategy
- SMC / price action: BOS, CHoCH, wave phases, liquidity sweeps, SR zones, confluence scoring
- Multi-timeframe: 15M, 1H, 4H, 1D, Weekly
- EURUSD primary pair (expanding to multi-asset)
- Paper trading on OANDA practice account, £10,000 starting capital

### Stack
- Python, OANDA (data + execution), SQLAlchemy/SQLite, FastAPI+HTMX dashboard
- Optuna for parameter optimisation, Typer CLI, local dashboard on PC

### Issues Fixed This Session
✅ Simultaneous long/short signals — dominance margin logic in confluence.py  
✅ Signal type always BOS_CONTINUATION — `_derive_signal_type()` derives from scores  
✅ Trigger timeframe hardcoded — threaded through from `_score_direction()`  
✅ Catalyst weight 10% on permanently zero score — set to 0.0 in settings.py  
✅ Structure-based SL — A/B testable via `--sl-method structure`  
✅ Drawdown % wrong (initial_capital denominator) — fixed to use peak equity  
✅ Sharpe ratio wrong (1H curve with √252) — fixed with daily resampling  
✅ save_candles N+1 queries — bulk INSERT OR REPLACE  
✅ `_tracked_closed_count` hasattr — already in `__init__`, confirmed no change needed  
✅ `apply_optimized_params()` silent failure — explicit warning logged, exposed in `get_status()`  
✅ Executor Bug 1 — phantom positions on OANDA rejection fixed  
✅ Executor Bug 2 — oanda_trade_id persisted to DB, survives restarts  
✅ Executor Bug 3 — double-close handled (404/TRADE_DOESNT_EXIST = INFO not ERROR)  
✅ quote_currency field added to AssetSpec (all assets = "USD", conversion logic pending)  
✅ Lookahead bias — bisect_right → bisect_left in backtest/engine.py  
✅ LearnerAgent confirmed stats-only — comment updated, live weight adjustment blocked

### Issues Identified, Not Yet Fixed
- Reconnection/retry logic for OANDA disconnections (not blocking demo)

### Phase 2 Fixes Applied (2026-04-08)
✅ FX conversion helper `data/fx.py` — reads last GBPUSD candle from DB (1h→4h→1d→15m fallback), converts quote-currency P&L to GBP
✅ `risk_manager.close_position` — applies `to_gbp()` using asset.quote_currency before updating capital/daily/weekly P&L
✅ Daily loss limit force-closes — `daily_limit_breached()` + `update_positions()` closes open positions at current price when breached, not just blocks new entries
✅ Startup reconciliation — `pipeline._reconcile_open_positions()` pulls OANDA open trades, rebuilds risk_mgr.open_positions + executor.oanda_trade_map from DB via oanda_trade_id; marks DB-open-but-OANDA-missing rows closed; logs OANDA orphans
✅ LearnerAgent frozen — `LearnerAgent(frozen=True)` default; skips `_optimize_parameters` and param mutation during demo
✅ Smoke test: `fetch` + `analyze` run clean on 2026-04-08 (no signals, ranging EURUSD — expected)

### Backtest Results (2023-2026, EURUSD, corrected interpretation)
| Metric | ATR SL (score≥0.60) | ATR SL (score≥0.65) | Structure SL |
|---|---|---|---|
| Trades | 1,112 | 694 | 519 |
| Win Rate | 40.6% | 40.7% | 42.4% |
| Profit Factor | 1.28 | 1.31 | 1.44 |
| Expectancy | 3.3 pips | 3.6 pips | 4.4 pips |
| Peak-to-trough Drawdown | ~25% | ~22% | ~17.5% |

Structure SL is better quality. ATR generates more trades. Neither is ready for live.

**Demo config locked at baseline (ATR SL, threshold 0.60).** Manual threshold testing
concluded — research agent will handle systematic search from here.

Key finding: confluence scoring is not strongly predictive at current state — higher
scores do not reliably produce better win rates. Raising threshold 0.60→0.65 cuts trade
count by 38% but improves win rate by only 0.1pp. The scoring system needs work before
threshold tuning will yield meaningful gains.

---

## Remaining Fixes Before Demo (next session)

- `agents/risk_manager.py` — daily loss limit must close existing positions when breached, not just block new entries
- `agents/risk_manager.py` — GBP/USD conversion at close time using OANDA pricing API (quote_currency field already added to AssetSpec)
- `engine/pipeline.py` — on startup, read all open trades from OANDA and reconcile with internal state so restarts don't lose position tracking
- `agents/learner.py` — add `frozen: bool = False` flag; set to True for demo period to prevent any param changes until research agent is built

---

## System 1: Automated Code Review Pipeline

### Purpose
Daily automated review of the codebase against a defined ruleset.
Proposes fixes. You approve at night. Nothing is applied without your sign-off.

### Schedule
- **07:00 UTC** — ReviewAgent runs
- **09:00 UTC** — Report emailed + dashboard updated

### Pipeline Flow
```
ReviewAgent
├── Reads all .py files in: agents/, analysis/, backtest/, engine/, data/
├── Checks against REVIEW_RULES.md
├── Produces findings: CRITICAL / WARNING / SUGGESTION
│
FixAgent (runs only if findings exist)
├── Proposes code diffs for each finding
├── Never applies changes — diffs only
├── Tags each fix: what changes, why, risk level (LOW/MEDIUM/HIGH)
├── Saves to: reports/code_review/YYYY-MM-DD.md
│
Email + Dashboard
├── Summary email sent to your Gmail
├── Dashboard shows pending fixes awaiting approval
│
Your Evening Review
├── Read report
├── Approve specific fixes → CC applies next morning before market open
└── Rejected fixes logged in reports/rejected_fixes.json
    (agents will not re-propose rejected fixes)
```

### Files to Create
- `agents/review_agent.py` — code review logic
- `agents/fix_agent.py` — diff proposal logic
- `reports/code_review/` — directory for daily reports
- `reports/rejected_fixes.json` — rejection log
- `REVIEW_RULES.md` — the ruleset (see below)
- `scheduler/code_review_job.py` — Windows Task Scheduler entry point

---

## REVIEW_RULES.md (Draft)

These are the rules ReviewAgent checks on every run. Based on issues found in this session.

```markdown
# Code Review Rules

## CRITICAL (must fix — blocks live trading)
- CR-001: Any hardcoded values that should be config (prices, thresholds, multipliers)
- CR-002: Risk limits not enforced in backtest (daily/weekly loss limits)
- CR-003: Currency conversion missing for non-USD P&L calculations  
- CR-004: Lookahead bias — using current unfinished candle in analysis
- CR-005: Position sizing formula errors
- CR-006: Signal skipping not propagating correctly (None checks)

## WARNING (fix soon — degrades results or reliability)
- WR-001: N+1 database query patterns (individual queries in loops)
- WR-002: Hardcoded timeframes or trigger TFs that should be dynamic
- WR-003: Metrics calculations using wrong denominators or periods
- WR-004: Silent failures (bare except, swallowed exceptions)
- WR-005: Placeholder code left active with non-zero weight (e.g. catalyst=0 with weight>0)
- WR-006: Duplicate logic across backtest and live pipeline

## SUGGESTION (improve when convenient)
- SG-001: Missing type hints
- SG-002: Functions longer than 60 lines (split into helpers)
- SG-003: Missing docstrings on public functions
- SG-004: Unused imports
- SG-005: Test coverage gaps on critical path (signal generation, risk sizing)

## NEVER FLAG
- Style preferences (formatting, naming conventions already in use)
- Working code that passes backtests even if not optimal
- Any change that would alter live trading behaviour without a backtest comparison
```

---

## System 2: Automated Backtest Research Pipeline

### Purpose
Daily automated search for better parameters. Tests on in-sample, validates, 
reports candidates. You approve promotion to `params.json`.

### Schedule
- **08:00 UTC** — Research loop runs (after data fetch at 06:00)
- **09:00 UTC** — Results in report alongside code review

### Overfitting Protection — Rotating Window Design

```
Year A (In-Sample)  → parameter search
Year B (Validation) → confirm winners generalise
Year C (Out-of-Sample) → never touched during search
                         only used for manual spot checks
                         every 3 months

Rotation (quarterly):
Q1 2026: In-Sample=2023, Validation=2024, OOS=2025
Q2 2026: In-Sample=2024, Validation=2025, OOS=2023
Q3 2026: In-Sample=2025, Validation=2024, OOS=2023+2026H1
```

This prevents regime dependency — a parameter set must work across 
different years (different volatility regimes, trends, ranging periods).

### Overfit Rejection Rules
- Trades < 50 in sample → result rejected (insufficient sample)
- In-sample improvement > 30% vs baseline → flagged as suspicious, needs manual review
- Validation degrades > 20% vs in-sample on profit factor → rejected as overfit
- Any parameter at its PARAM_BOUNDS limit → flagged (hitting the wall = likely overfit)

### Parameters to Test (from `settings.py` PARAM_BOUNDS)

**Numeric (tested daily, small increments):**
- `confluence_threshold`: 0.40–0.80
- `sl_atr_multiplier`: 1.0–3.0
- `tp_risk_reward`: 1.5–4.0
- `sl_method`: "atr" vs "structure"
- `dominance_margin`: 0.05–0.20

**Weight distribution (tested weekly, not daily — too many combinations):**
- `htf_bias_weight`: 0.10–0.40
- `bos_weight`: 0.10–0.35
- `wave_position_weight`: 0.05–0.25
- `liquidity_sweep_weight`: 0.05–0.25
- `sr_reaction_weight`: 0.05–0.20

**Structural (tested monthly — high impact, needs careful validation):**
- `block_hours`: which UTC hours to skip
- `block_days`: which weekdays to skip
- Timeframe inclusion: with/without 15M
- `cooldown_after_losses`: 0–5

### Daily Test Budget
- Max 20 combinations per day (prevents exhaustive curve fitting)
- Prioritise params not tested in last 7 days
- Never test more than 3 structural params in same run

### Pipeline Flow
```
ParameterAgent
├── Reads current params.json as baseline
├── Reads test_history.json (what was tested when, results)
├── Selects next 20 combinations using priority queue
│
BacktestRunner
├── Runs each combination on IN-SAMPLE year only
├── Compares to baseline on same period
├── Applies overfit rejection rules
│
ValidationAgent (top 3 survivors only)
├── Re-runs on VALIDATION year
├── Checks degradation < 20%
├── Marks as PROMOTED_CANDIDATE or REJECTED_OVERFIT
│
Report
├── Full results table saved to reports/research/YYYY-MM-DD.md
├── Email section: "Today's research — N tested, M passed validation"
├── Shows: what was tested, in-sample vs validation side by side
├── Recommended promotions clearly marked
│
Your Evening Review
├── Approve promotion → params.json updated
└── Reject → logged, not re-tested for 30 days
```

### Files to Create
- `research/parameter_agent.py` — combination generation + priority queue
- `research/backtest_runner.py` — batch backtest execution
- `research/validation_agent.py` — overfit checking
- `research/test_history.json` — log of all tests run
- `reports/research/` — daily research reports
- `scheduler/research_job.py` — Windows Task Scheduler entry point

---

## Email Report System

### Setup
- Gmail App Password — **not OAuth** (simpler, no token refresh)
- Requires 2FA enabled on Google account, then generate an App Password
- Store in `.env`: `GMAIL_USER=you@gmail.com`, `GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx`
- Library: `smtplib` (stdlib, no extra dependency)

### Approval Method
- Clickable links in the email body hitting the **local FastAPI endpoint** (already running for the dashboard)
- Example: `http://localhost:8050/approve/research/R1` or `/approve/code/C1`
- No inbox polling needed — you click, FastAPI handles it, state updates immediately
- Dashboard also shows pending approvals — both sync to the same DB state
- If you're not at your PC, approvals can wait; nothing is applied automatically

### Timing
- **19:00 UTC** daily (20:00 BST summer, 19:00 GMT winter) — combined report
- **Immediate** — pipeline crash alerts sent any time, regardless of hour
- **Sunday 19:00 UTC** — weekly summary included in the regular report

### Email Template
```
Subject: 🤖 Trading Bot — Daily Report [DATE] | ⚠️ 2 actions needed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ACTIONS NEEDED (2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[RESEARCH] Promote threshold=0.65, sl=structure, tp_rr=2.5?
  In-sample:  PF=1.61  WR=44%  DD=14%  Trades=89
  Validation: PF=1.52  WR=43%  DD=16%  ← passes validation
  Reply: APPROVE-R1 or REJECT-R1

[CODE] Fix WR-001: save_candles N+1 query pattern?
  File: data/ingestion.py  Risk: LOW
  Reply: APPROVE-C1 or REJECT-C1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SYSTEM STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Account:     £10,247.32 (+£247 today)
Open:        2 positions (EURUSD long, XAUUSD short)
Using:       Optimised params ✅ (last updated 2026-04-05)
Data:        All pairs current as of 06:00 UTC ✅
Pipeline:    Running ✅  Last cycle: 08:47 UTC

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TODAY'S TRADING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Closed:  1 trade | EURUSD SHORT | +18.2 pips | +£47.30
Open P&L: EURUSD LONG  +12.1 pips unrealised
          XAUUSD SHORT  -8.4 pips unrealised
Week:    3 trades | 2W 1L | +£89.40 | WR 66.7%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESEARCH (ran 20 combinations today)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tested:   20  |  Passed in-sample: 6  |  Passed validation: 1
Winner:   threshold=0.65, sl=structure, tp_rr=2.5 (see action above)
Rejected: 5 (overfit — validation degraded >20%)
Skipped:  14 (tested recently, deprioritised)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CODE REVIEW (weekly — next run: Thursday)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Last run:   2026-04-03 | 1 WARNING found (see action above)
No new issues since last run.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
READINESS STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Demo:  🟡 6/8 checks passing (currency conversion, reconciliation pending)
Live:  🔴 Not ready (demo required first)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Full report: http://localhost:8050/reports/2026-04-07
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Files to Create
- `notifications/email_reporter.py` — email composition + send via smtplib
- `notifications/report_builder.py` — assembles code review + research + readiness into one report
- `notifications/approval_handler.py` — FastAPI routes: `/approve/{type}/{id}`, `/reject/{type}/{id}`

---

## ReadinessAgent

### Purpose
Weekly automated checklist that tells you whether the system is ready for demo trading
or live trading. Advisory only — never blocks the bot, never makes the decision for you.

### Schedule
- **Weekly** (not daily) — runs as part of the Sunday 19:00 UTC email report

### Output
Traffic light status with two separate checklists:

```
Demo checklist   → 🟡 6/8 checks passing
Live checklist   → 🔴 Not ready (demo required first)
```

- `🔴` Not ready — critical checks failing
- `🟡` Demo ready — all demo checks pass, live checks still failing
- `🟢` Live ready — all checks passing on both lists

### Demo Checklist (example items)
- [ ] Backtest profit factor > 1.3 on most recent 3-month window
- [ ] Backtest win rate > 40%
- [ ] Drawdown < 20%
- [ ] Currency conversion implemented
- [ ] Daily loss limit closes positions (not just blocks entries)
- [ ] At least 200 backtest trades (sufficient sample)
- [ ] Bulk upsert implemented (no N+1)
- [ ] Reconciliation: live P&L matches backtest expectancy within 25%

### Live Checklist (example items)
- [ ] All demo checks pass
- [ ] 30+ days of demo trading with positive expectancy
- [ ] Demo drawdown < 15% over the demo period
- [ ] Manual review of 20+ live-equivalent trades
- [ ] Capital at risk confirmed acceptable by you

### Behaviour
- Never blocks trading — purely advisory
- You make the call — the agent just surfaces the information
- Appears in Sunday email report and dashboard readiness panel
- Checklist definitions live in `reports/readiness/checklist.json` (editable)

### Files to Create
- `agents/readiness_agent.py` — runs checks, produces traffic light status
- `reports/readiness/YYYY-MM-DD.md` — weekly snapshot of checklist state
- `reports/readiness/checklist.json` — checklist definitions (demo + live)

---

## Windows Task Scheduler Setup

Five scheduled jobs:

```
Job 1: Data Fetch       06:00 UTC  (daily)          →  python main.py fetch
Job 2: Code Review      07:00 UTC  (daily)          →  python scheduler/code_review_job.py
Job 3: Research         08:00 UTC  (daily)          →  python scheduler/research_job.py
Job 4: Email Report     19:00 UTC  (daily)          →  python scheduler/email_report_job.py
Job 5: Deep Fetch       05:00 UTC  (Sunday only)    →  python main.py fetch --deep
```

Note: Jobs 2 and 3 write their results to the DB/reports directory.
Job 4 assembles the combined daily email at 19:00 UTC from whatever reports exist.
This means the email always goes out at a predictable time regardless of how long
research or review took to complete.

### Job 1 — Daily Incremental Fetch (06:00 UTC)

Runs `python main.py fetch` for **all active pairs across all timeframes** (15M, 1H, 4H, 1D, Weekly).

This is an **incremental update only** — it appends new candles since the last stored timestamp for each pair/timeframe combination. It does not re-fetch historical data. The candle database grows continuously, so backtests always have access to the most recent data without needing to re-download anything.

Behaviour:
- For each pair × timeframe, reads the latest stored candle timestamp from the DB
- Fetches only candles newer than that timestamp from OANDA
- Appends them (no deletions, no re-writes of existing rows)
- If no data is returned (market closed, weekend), the job exits cleanly with no DB changes

### Job 4 — Weekly Deep Fetch (Sunday 05:00 UTC)

Runs `python main.py fetch --deep` for all active pairs across all timeframes.

This is a **gap-fill fetch** — it re-fetches the last 500 candles per timeframe per pair and upserts them into the database. Any candles missing due to daily job failures, OANDA outages, or connectivity drops during the week are backfilled.

Behaviour:
- For each pair × timeframe, fetches the last 500 candles unconditionally
- Upserts into the DB (insert new rows, overwrite existing rows if OANDA has corrected data)
- Does not truncate or delete older history
- Runs before market open Sunday evening (US session), so Monday's backtest data is complete

---

## Build Order for Next Sessions

### ✅ Phase 1 — Core bugs fixed (COMPLETE)
All 16 items above done.

### ✅ Phase 2 — Demo unblocking (COMPLETE 2026-04-08)
1. ✅ Daily loss limit closes positions
2. ✅ GBP/USD currency conversion (via DB GBPUSD rate, not OANDA pricing API)
3. ✅ Position reconciliation on startup
4. ✅ Freeze LearnerAgent
5. ⏳ `python main.py run` live-loop smoke test — deferred, will run on PC

### ✅ Phase 3 — Email + Approval system (COMPLETE 2026-04-08)
1. ✅ Shared `email_utils` package created one dir up (`../email_utils`), installed editable — reusable across projects
2. ✅ `notifications/email_reporter.py` — thin wrapper, loads `GMAIL_USER`/`GMAIL_APP_PASSWORD`/`REPORT_TO_EMAIL` from `.env`
3. ✅ `notifications/report_builder.py` — assembles daily report; real sections (status, today's trading, open positions, weekly summary), stub sections for research/code-review/readiness (Phases 4–6)
4. ✅ `notifications/approval_handler.py` — FastAPI `/approve/{kind}/{id}`, `/reject/{kind}/{id}`, `/api/approvals`; idempotent, persists to `reports/approvals.json`
5. ✅ Routes mounted into `dashboard/app.py`
6. ✅ `scheduler/email_report_job.py` — entry point for Task Scheduler 19:00 UTC job
7. ✅ `.env.example` updated with Gmail placeholders
8. ✅ Manual end-to-end test passed: report sent successfully to Sameer.Chandna@gmail.com (2026-04-08)
9. ⏳ Windows Task Scheduler registration — deferred until all phases done

### Phase 4 — Research pipeline (2 sessions)
1. `research/parameter_agent.py`
2. `research/backtest_runner.py`
3. `research/validation_agent.py`
4. `research/test_history.json` schema
5. `scheduler/research_job.py`
6. End-to-end test run

### Phase 5 — Code review pipeline (1 session)
1. `REVIEW_RULES.md` finalised
2. `agents/review_agent.py`
3. `agents/fix_agent.py`
4. `scheduler/code_review_job.py`
5. End-to-end test run

### Phase 6 — ReadinessAgent (1 session)
1. `agents/readiness_agent.py`
2. `reports/readiness/checklist.json` — define demo + live checklists
3. Wire into Sunday email report
4. End-to-end test run

### Phase 7 — Dashboard integration (1 session)
1. Add pending approvals panel to existing dashboard
2. Show readiness traffic light on dashboard home
3. Show research history chart (param performance over time)
4. Show code review history

---

## Key Decisions Made

| Decision | Choice | Reason |
|---|---|---|
| SL method | Both ATR and Structure, A/B testable | Don't guess, test empirically |
| Data source | OANDA only (Yahoo disabled) | Consistent timestamps |
| Entry TF | 15M (under evaluation) | Run compare --exclude-tf 15m to test |
| Overfitting protection | Rotating 3-window split | Prevents regime dependency |
| Daily test budget | Max 20 combinations | Prevents exhaustive curve fitting |
| Your approval required | Always, before any live change | Hard rule, not optional |
| Demo config | ATR SL, threshold 0.60, no filters | Baseline is most honest representation of current system |
| LearnerAgent | Stats tracker only, frozen for demo | OOS validation belongs in Optuna pipeline not learner |
| Confluence scoring | Not strongly predictive at current state | Higher scores don't reliably improve win rate — research agent will investigate |

---

## Commands Reference

```bash
# Analysis
python main.py analyze --pair EURUSD

# Single backtest
python main.py backtest --start 2023-01-01 --end 2024-01-01

# A/B comparison
python main.py compare --start 2023-01-01 --sl-method structure
python main.py compare --start 2023-01-01 --exclude-tf 15m
python main.py compare --start 2023-01-01 --min-score 0.65

# Live (paper)
python main.py run

# Dashboard
python main.py dashboard
```

---

## Context for New Chat Sessions

When starting a new chat, share this document and say:
> "I am building an auto trading bot. Here is the architecture document. 
> Today I want to work on [specific phase/task]."

Repo is public: https://github.com/sameerchandna/auto-trading-bot  
Raw file access: paste `https://raw.githubusercontent.com/sameerchandna/auto-trading-bot/master/[filepath]`
