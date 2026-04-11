# Improvement Plan — EURUSD Signal & Automation Quality

> Created: 2026-04-10
> Status: In Progress (Priorities 1-7 done, Priority 8 next)

---

## Priority 1: Regime Detection & Filtering — DONE (2026-04-10)

**Problem:** Strategy uses identical params in trending, ranging, and volatile markets. Losing trades cluster in unfavorable regimes.

**Implemented:**
- ADX calculation added to `analysis/context.py` (Wilder's smoothed ADX, per-timeframe)
- `Regime` enum (TRENDING/RANGING) added to `data/models.py`
- Regime classified on 4H ADX (fallback: 1D → 1H) in `build_price_context()`
- `score_confluence()` accepts `regime_filter_enabled` — blocks signals when RANGING
- Config-driven: `regime_filter_enabled` (bool) + `regime_adx_threshold` (float) in `optimized_params.json`
- PARAM_BOUNDS: `regime_adx_threshold` (15.0–35.0)
- Research agent: toggles `regime_filter_enabled` + mutates ADX threshold (±2.5 step)
- Live pipeline wired: `MarketAnalyzerAgent.adx_threshold` + `SignalGeneratorAgent._regime_filter_enabled`
- **Defaults to OFF** — must be promoted via research pipeline

**Backtest Results (2023-01 to 2026-04, EURUSD):**

| Config | Trades | Win Rate | PF | Expectancy | Max DD | PnL |
|---|---|---|---|---|---|---|
| Baseline (OFF) | 1202 | 38.6% | 1.22 | 2.9 pips | 34.3% | £32,082 |
| ADX >= 15 | 1113 | 38.1% | 1.18 | 2.5 pips | 37.1% | £21,572 |
| ADX >= 17.5 | 932 | 38.3% | 1.17 | 1.9 pips | 53.5% | £15,580 |
| ADX >= 20 | 742 | 37.5% | 1.12 | 1.5 pips | 46.3% | £8,534 |
| ADX >= 25 | 508 | 36.2% | 1.03 | 0.9 pips | 34.4% | £1,187 |

**Conclusion:** Simple ADX-only regime filter does not improve this strategy — SMC setups
(liquidity sweeps, S/R bounces) may actually perform well in ranging conditions. The
infrastructure is built and defaults to OFF. The research agent will systematically test
thresholds daily. Future work: try regime-*aware* param switching (Option B) rather than
outright blocking, or combine ADX with ATR percentile for volatility-based filtering.

**Files changed:**
- `data/models.py` — `Regime` enum, `adx` field on `TimeframeAnalysis`, `regime` field on `PriceContext`
- `analysis/context.py` — `calculate_adx()`, `classify_regime()`, ADX in `analyze_timeframe()` + `build_price_context()`
- `analysis/confluence.py` — `regime_filter_enabled` param in `score_confluence()`
- `config/settings.py` — `REGIME_FILTER_ENABLED`, `REGIME_ADX_THRESHOLD`, `REGIME_ADX_TIMEFRAME`, PARAM_BOUNDS
- `config/params.py` — load/save regime params
- `config/optimized_params.json` — regime fields (defaults OFF)
- `backtest/engine.py` — passes `regime_adx_threshold` + `regime_filter_enabled` through
- `agents/signal_generator.py` — `_regime_filter_enabled` flag
- `agents/market_analyzer.py` — `adx_threshold` param
- `engine/pipeline.py` — wires regime config on setup
- `research/parameter_agent.py` — `_toggle_regime_filter()`, ADX threshold mutations

**Why first:** Cutting bad trades is faster and more reliable than finding better entries. Even a crude filter (e.g., no entries when ADX < 20 on 4H) could eliminate a large chunk of losers without touching the scoring model.

---

## Priority 2: Learned Scoring Model — DONE (2026-04-10)

**Problem:** The 7-factor linear weighted confluence score isn't strongly predictive — higher scores don't reliably produce better win rates.

**Implemented:**
- Factor scores (all 7 + ADX, ATR, regime) now stored in `Signal.rationale["scores"]`
- `analysis/signal_model.py` — full ML pipeline:
  - `generate_training_data()` — runs backtest, extracts (12-feature vector, win/loss) per trade
  - `train_model()` — logistic regression with walk-forward CV (no lookahead bias)
  - `predict_win_probability()` — runtime prediction, cached model
- 12 features: 7 confluence factors + confluence_score + bias_strength + ADX + direction + hour (cyclic)
- CLI: `python main.py train-model` — generates data, trains, shows CV results + feature importance
- Config-driven: `signal_model_enabled` (bool) + `signal_model_min_confidence` (float) in `optimized_params.json`
- PARAM_BOUNDS: `signal_model_min_confidence` (0.35–0.65)
- Research agent: toggles `signal_model_enabled` + mutates confidence threshold (±0.05 step)
- Wired through: backtest engine, live signal generator, pipeline setup
- **Defaults to OFF** — must be promoted via research pipeline

**Model Training Results (walk-forward CV, 1206 trades):**
- Mean AUC: 0.549 (improving with more data — Fold 4: 0.627)
- Top positive features: `confluence_score` (+0.25), `bos` (+0.23)
- Top negative features: `bias_strength` (-0.21), `adx` (-0.15)
- Insight: high bias alignment and high ADX correlate with *worse* outcomes — confirms regime filter findings

**Backtest Results (2023-01 to 2026-04, EURUSD):**

| Config | Trades | Win Rate | PF | Expectancy | Max DD | PnL |
|---|---|---|---|---|---|---|
| Baseline (OFF) | 1202 | 38.6% | 1.22 | 2.9 pips | 34.3% | £32,082 |
| Model >= 0.40 | 1077 | 40.9% | 1.36 | 3.9 pips | **21.7%** | £45,878 |
| **Model >= 0.45** | **897** | **43.1%** | **1.47** | **4.9 pips** | 34.4% | **£49,775** |
| Model >= 0.50 | 674 | 42.9% | 1.46 | 3.8 pips | 49.5% | £30,216 |
| Model >= 0.55 | 488 | 45.3% | 1.50 | 4.7 pips | 24.2% | £29,008 |

**Best in-sample config: Model >= 0.45** — PF +20%, WR +4.5pp, expectancy +69%, PnL +55% vs baseline.

**Out-of-Sample Validation (trained 2023-01 to 2025-06, tested 2025-07 to 2026-04):**

| Config | Trades | Win Rate | PF | Expectancy | Max DD | PnL |
|---|---|---|---|---|---|---|
| OOS Baseline | 543 | 38.3% | 1.17 | 0.7 pips | 52.4% | £8,001 |
| OOS Model >= 0.45 | 492 | 38.2% | 1.13 | 0.3 pips | 43.7% | £5,419 |
| OOS Model >= 0.40 | 502 | 37.1% | 1.12 | 0.2 pips | 56.1% | £4,613 |

**Verdict: OVERFIT.** In-sample PF 1.47 collapsed to OOS PF 1.13 (>50% drop). The model
learned patterns specific to 2023-2025 that don't generalize forward. CV AUC on the restricted
training set was only 0.303 (worse than random).

**Why it overfit:**
- The 12 features are mostly compressed summaries (the 7 confluence scores) that the linear
  scorer already uses — the model re-learned the weights and overfit to noise
- ~1200 trades / 12 features is borderline sample size
- Logistic regression may be too simple to capture non-linear interactions

**What would fix it (future iteration):**
The model needs features the linear scorer *can't* see:
- Distance to nearest S/R zone (continuous, not binary 0/1)
- ATR percentile vs rolling history (is volatility unusually high/low?)
- Session context (London/NY/Asia overlap flags)
- Time since last structure break (bars, not just present/absent)
- Recent trade outcomes (rolling win streak / loss streak)
- Candle body-to-wick ratio, rejection patterns

**Overfitting rules of thumb for trading models:**
- OOS PF within 20% of in-sample → reasonable generalization
- OOS PF drops >50% → overfit, don't trust
- AUC 0.55-0.65 on OOS → genuinely useful filter
- Need ~10-20x samples per feature (12 features → 120-240+ per test fold)

**Status: Infrastructure complete, model OFF by default.** The pipeline, CLI, config, and
research agent integration all work. The model will become useful once richer features are
added and more trade data accumulates. Research agent will keep testing it daily.

**Files changed:**
- `analysis/signal_model.py` — NEW: full ML pipeline (training, prediction, caching)
- `analysis/confluence.py` — `signal_model_enabled` + `signal_model_min_confidence` params, model filter, scores in rationale
- `config/settings.py` — PARAM_BOUNDS for `signal_model_min_confidence`
- `config/params.py` — load/save model params
- `config/optimized_params.json` — model fields (defaults OFF)
- `backtest/engine.py` — passes model params through to `score_confluence()`
- `agents/signal_generator.py` — `_signal_model_enabled`, `_signal_model_min_confidence`
- `agents/market_analyzer.py` — (unchanged, ADX already wired from Priority 1)
- `engine/pipeline.py` — loads model config on setup
- `research/parameter_agent.py` — `_toggle_signal_model()`, confidence threshold mutations
- `main.py` — `train-model` CLI command
- `requirements.txt` — added `scikit-learn>=1.3.0`
- `.gitignore` — `models/*.pkl`
- `models/signal_model.pkl` + `signal_model_meta.json` — trained model artifacts

**Why second:** This is the highest-ceiling improvement but needs labeled data from backtests. The regime filter (Priority 1) cleans the training data, making this model more effective.

---

## Priority 3: News / Economic Calendar Filter — DONE (2026-04-10)

**Problem:** News weight is hardcoded to 0.0. High-impact events (NFP, ECB, FOMC) cause erratic price action that the strategy can't handle.

**Implemented:**
- `data/economic_calendar.py` — rule-based generation of 40 high-impact EURUSD events/year:
  - NFP (first Friday monthly, 13:30 UTC)
  - US CPI (~12th-14th monthly, 13:30 UTC)
  - FOMC (8 meetings/year, 19:00 UTC — hardcoded dates 2023-2026)
  - ECB (6-8 meetings/year, 13:15 UTC — hardcoded dates 2023-2026)
- `is_news_blocked(timestamp, before_mins, after_mins)` — O(1) lookup with sorted events + early exit
- Override file support: `config/news_overrides.json` for ad-hoc events
- `next_event(timestamp)` utility for dashboard/logging
- Config-driven: `news_filter_enabled` (bool) + `news_block_before_mins` (int) + `news_block_after_mins` (int)
- PARAM_BOUNDS: before (15–60), after (5–30)
- Research agent: toggles `news_filter_enabled` + mutates window sizes (±5 min step)
- Wired through: `score_confluence()`, backtest engine (passes candle timestamp), live pipeline
- **Defaults to OFF**

**Backtest Results (EURUSD):**

| Config | Trades | WR | PF | Exp | Max DD | PnL |
|---|---|---|---|---|---|---|
| Full Baseline | 1202 | 38.6% | 1.22 | 2.9 | 34.3% | £32,082 |
| Full News 30/15 | 1198 | 38.6% | 1.23 | 3.1 | 33.3% | £33,548 |
| Full News 60/30 | 1190 | 38.9% | 1.25 | 3.3 | **31.6%** | £36,012 |
| OOS Baseline | 543 | 38.3% | 1.17 | 0.7 | 52.4% | £8,001 |
| OOS News 30/15 | 539 | 38.0% | 1.16 | 0.6 | 52.9% | £7,556 |

**Conclusion:** Small positive effect in-sample (PF 1.22→1.25 with 60/30 window), flat on OOS.
The filter only removes 4-12 trades because the bot iterates on 1H candles — it rarely lands
exactly in a 45-minute news window. The real value is **live trading protection** where the
5-minute loop will catch news windows that 1H backtesting misses. No overfitting risk (rule-based,
not data-fitted). Marginal but safe to enable in production as a protective measure.

**Files changed:**
- `data/economic_calendar.py` — NEW: rule-based event generation, block window check
- `analysis/confluence.py` — `news_filter_enabled`, `news_block_before_mins`, `news_block_after_mins`, `current_time` params
- `config/settings.py` — news filter defaults + PARAM_BOUNDS
- `config/params.py` — load/save news filter params
- `config/optimized_params.json` — news filter fields (defaults OFF)
- `backtest/engine.py` — passes `current_time=current_date` + news params
- `agents/signal_generator.py` — news filter fields
- `engine/pipeline.py` — loads news config on setup
- `research/parameter_agent.py` — `_toggle_news_filter()`, window size mutations

**Why third:** Simple to implement, low risk, but impact is narrower than Priorities 1-2 (only affects ~5-10 signals/month around major events).

---

## Priority 4: Auto-Promote Research Winners — DONE (2026-04-10)

**Problem:** Promoted parameter candidates sit in the approval queue until manually approved, creating a bottleneck.

**Implemented:**
- New `AUTO_PROMOTED` verdict in `research/validation_agent.py` with stricter gates than `PROMOTED_CANDIDATE`:
  - 100% walk-forward window pass rate (not just 75%)
  - PF >= 1.50 in **every** individual window (not just median)
  - Median WR >= 45%
  - Median DD <= 30%
  - OOS PF degradation < 15% (vs 25% for normal promotion)
  - Not flagged suspicious or at param bound (checked before auto-promote)
- Config-driven: `auto_promote_enabled` (bool) in `optimized_params.json` — **defaults to OFF**
- When `auto_promote_enabled=true` and verdict is `AUTO_PROMOTED`:
  - Params applied directly to `optimized_params.json` (bypass approval queue)
  - Rolling baseline re-synced immediately
  - PineScript regenerated
  - Still logged in test history + daily report + email for audit trail
- When `auto_promote_enabled=false`, `AUTO_PROMOTED` falls through to normal `PENDING` approval queue
- Rollback mechanism:
  - Previous params snapshot stored in `test_history.json["auto_promote_rollback"]` before every auto-apply
  - CLI: `python main.py rollback` reverts to pre-auto-promotion params
  - `rollback_last_auto_promotion()` function available programmatically
- Fixed `_strip_for_save()` in `promotion.py` — was dropping regime/model/news params on promotion (latent bug since Priority 1-3)

**Files changed:**
- `research/validation_agent.py` — `AUTO_PROMOTED` verdict, `_check_auto_promote()` with strict gates, auto-promote constants
- `research/promotion.py` — `auto_apply_promotion()`, `rollback_last_auto_promotion()`, `_store_rollback_snapshot()`, fixed `_strip_for_save()` to include all params
- `scheduler/research_job.py` — handles `AUTO_PROMOTED` verdict: auto-apply when enabled, fall through to approval queue when disabled
- `config/settings.py` — `AUTO_PROMOTE_ENABLED` default
- `config/params.py` — load/save `auto_promote_enabled`
- `config/optimized_params.json` — `auto_promote_enabled: false`
- `main.py` — `rollback` CLI command

**Why fourth:** The research pipeline already runs daily — this just removes the human bottleneck. But it's only valuable once the signal quality (Priorities 1-2) gives the research agent better material to work with.

---

## Priority 5: Three-Tier Memory System — DONE (2026-04-11)

> Inspired by: auto-stock-bot's tiered memory (working → episodic → strategic)

**Problem:** The forex bot has no structured memory across runs. Pipeline state is ephemeral,
trade outcomes aren't analyzed historically, and there's no audit trail of agent decisions.
When the bot restarts, it loses all context about recent performance, market conditions, and
why decisions were made. This limits the learner, makes debugging live trades difficult, and
prevents the kind of rolling analysis the stock bot's learner performs.

**Approach — Three tiers:**

### Tier 1: Short-Term / Working Memory (per cycle)
- Already exists: the `data` dict flowing through the pipeline
- **Enhancement:** Add a `CycleContext` object that accumulates structured state:
  - Current regime + confidence
  - Signals generated/filtered/executed this cycle (with reasons)
  - Open positions snapshot
  - Account equity + drawdown at cycle start
- Pass `CycleContext` through all agents instead of loose dict keys
- Discarded after each cycle — no persistence needed

### Tier 2: Medium-Term / Episodic Memory (trade & signal history)
- **Audit trail:** New `audit_log.db` (write-only, separate from `portfolio.db`):
  - Every agent decision: signal generated, signal filtered (with reason), position opened/closed
  - Every research verdict, every param promotion/rollback
  - Schema: `(timestamp, agent, action, pair, details_json)`
  - `log_audit(agent, action, details)` helper callable from anywhere
- **Equity snapshots:** New `EquitySnapshotRecord` in main DB:
  - EOD record: `(date, equity, cash, unrealized_pnl, daily_return, drawdown_pct, regime)`
  - Enables rolling performance analysis without replaying trades
  - Written by a scheduled post-cycle job or at end of each `run_once()`
- **Signal outcome tracking:** Enrich `SignalRecord` with the full feature vector from
  `rationale["scores"]` so the learner and feature importance modules can query historical
  signals without re-running backtests

### Tier 3: Long-Term / Strategic Memory (baselines & learned patterns)
- Already exists: `test_history.json` (anchor/rolling baselines, test log, blacklist)
- **Enhancement:** Add a `strategy_insights` section:
  - Rolling 90-day performance summary (updated daily): win rate, PF, expectancy, avg hold time
  - Per-regime performance breakdown (how does the strategy perform in TRENDING vs RANGING?)
  - Feature importance snapshot (updated weekly when Priority 6 runs)
  - Learner proposals history (what was tried, what worked)
- This section is the "institutional memory" — it persists what the bot has *learned about itself*

### Memory Consolidation
- **Daily:** Equity snapshot written, audit trail accumulates
- **Weekly:** Learner reads 90-day episodic memory → proposes weight adjustments (feeds into Priority 5b)
- **Monthly:** Feature importance recalculated → stored in strategic memory → biases research agent mutations

**Why fifth:** Memory is the foundation for everything adaptive. The learner (Priority 5b) needs
episodic memory to analyze. Feature importance (Priority 6) needs signal outcome history to compute.
The audit trail is immediately useful for debugging live trades. This should come before unfreezing
the learner because the learner needs something to learn *from*.

**Implemented:**
- **Tier 1 — CycleContext:** `CycleContext` + `CycleSignalRecord` Pydantic models in `data/models.py`.
  Passed through pipeline via `data["cycle"]`. Tracks regime, bias, signals lifecycle
  (generated/filtered with reason/executed), open positions snapshot, equity at cycle start.
- **Tier 2 — Episodic Memory:**
  - `storage/audit_log.db` — separate write-only SQLite DB with `AuditLogRecord` table.
    `log_audit(agent, action, pair, details)` fire-and-forget helper (swallows exceptions).
    Agents logged: market_analyzer, signal_generator, risk_manager, executor, pipeline.
  - `EquitySnapshotRecord` in main DB — written at end of every `run_once()` cycle.
    Tracks equity, cash, unrealized PnL (from cached live prices), drawdown %, open position count.
  - `SignalRecord` enriched with 10 feature vector columns (score_htf_bias through atr) —
    populated from `signal.rationale["scores"]` at execution time for future model training.
- **Tier 3 — Strategic Memory:** `strategy_insights` section in `test_history.json`,
  keyed per-asset (EURUSD, AUDUSD, GBPUSD, XAUUSD, US30, WTICO). Each asset has
  `rolling_90d_summary`, `per_regime_performance`, `feature_importance` (placeholder).
  Shared `learner_proposals_history` list (capped at 50). CRUD helpers in `research/history.py`.
- **Consolidation:** `_maybe_run_consolidation()` placeholder in pipeline — weekly/monthly hooks
  for Priority 5b (learner) and Priority 6 (feature importance).

**Files changed:**
- `data/models.py` — `CycleContext`, `CycleSignalRecord` models
- `storage/database.py` — `EquitySnapshotRecord`, `AuditLogRecord`, `AuditBase`, `log_audit()`,
  `get_audit_session()`, signal feature vector columns + migration
- `engine/pipeline.py` — CycleContext creation/population in `_run_pair()`, `_write_equity_snapshot()`,
  `_maybe_run_consolidation()`, `_filter_signals()` tracks reasons, `_last_prices` cache
- `agents/executor.py` — populates signal score columns, audit logging
- `agents/market_analyzer.py` — audit logging
- `agents/signal_generator.py` — audit logging
- `agents/risk_manager.py` — audit logging (rejections + approvals)
- `research/history.py` — `get_strategy_insights()`, `update_rolling_90d_summary()`,
  `update_regime_performance()`, `record_learner_proposal()`
- `research/test_history.json` — `strategy_insights` section initialized per-asset

---

## Priority 5b: Unfreeze Live Learner with Guardrails — DONE (2026-04-11)

> Inspired by: auto-stock-bot's LearnerAgent (active, proposes ±5% weight changes via approval queue)

**Problem:** `LearnerAgent` is frozen during demo. No live adaptation to shifting market conditions between daily research runs.

**Implemented:**
- `learner_enabled` config flag in `optimized_params.json` (defaults OFF)
- Every 50 closed trades (OPTIMIZATION_INTERVAL), learner:
  1. Queries last 50 closed positions from DB via `query_recent_closed_positions()`
  2. Computes rolling metrics: WR, PF, expectancy, max DD, per-setup breakdown
  3. Kill switch check: if rolling DD > 15% (`LEARNER_DD_KILL_PCT`), auto-freezes
  4. Maps setup types to confluence weights via `SETUP_WEIGHT_MAP`
  5. Proposes ±0.05 weight deltas for outperforming/underperforming setups
  6. Clamps to PARAM_BOUNDS, normalizes weights to sum=1.0
  7. Routes through approval queue or auto-applies (if `auto_promote_enabled`)
  8. Records in strategic memory (`learner_proposals_history`) + audit log

**Guardrails:**
- Max ±0.05 per weight per cycle — can't drastically shift strategy
- PARAM_BOUNDS enforced — weights stay in valid ranges
- Weights normalized to 1.0 — no inflation
- MIN_TRADES_FOR_STATS (30) gate — won't act on tiny samples
- Kill switch — auto-freezes if rolling DD > 15%
- Approval queue — human gate unless auto_promote is on
- Rollback — `python main.py rollback` reverts last change
- Audit trail — every proposal logged to audit_log.db + strategic memory
- Defaults OFF — `learner_enabled: false`

**Setup → Weight Mapping:**
| Signal Type | Confluence Weight |
|---|---|
| bos_continuation | bos |
| liquidity_sweep | liquidity_sweep |
| wave_entry | wave_position |
| sr_bounce | sr_reaction |
| wave_ending | wave_ending |

**Boost criteria:** WR >= 45% AND PF >= 1.20 → nudge weight +0.05
**Reduce criteria:** WR < overall avg AND PF < 1.0 → nudge weight -0.05

**Weekly consolidation hook filled:** Every Monday midnight UTC, computes rolling 90-day
per-asset performance summary and writes to strategic memory (`rolling_90d_summary`).

**Files changed:**
- `config/settings.py` — `LEARNER_ENABLED`, `LEARNER_MAX_WEIGHT_DELTA`, `LEARNER_DD_KILL_PCT`, `LEARNER_MIN_WR_FOR_BOOST`, `LEARNER_MIN_PF_FOR_BOOST`
- `config/params.py` — `learner_enabled` field in load/save
- `config/optimized_params.json` — `learner_enabled: false`
- `agents/learner.py` — Rewritten: `_query_rolling_trades()` removed (uses `query_recent_closed_positions()`), `_compute_rolling_metrics()`, `_check_kill_switch()`, `_propose_weight_adjustment()`, `_auto_apply()`, `_record_proposal()`, `SETUP_WEIGHT_MAP`
- `research/promotion.py` — `push_learner_proposal()`, `apply_decisions()` handles `kind="learner"`
- `engine/pipeline.py` — wires `learner_enabled`/`auto_promote_enabled` to learner, `_run_weekly_consolidation()` fills placeholder
- `storage/database.py` — `query_recent_closed_positions(n)`

**Why here:** Intra-day adaptation sounds appealing but is high-risk for overfitting to noise. Better to have memory infrastructure (Priority 5) and a working auto-promotion loop (Priority 4) first.

---

## Priority 6: Feature Importance Tracking — DONE (2026-04-11)

> Inspired by: auto-stock-bot's `analysis/feature_importance.py` (correlation + SHAP)

**Problem:** No visibility into which confluence factors actually drive winning trades vs. which are noise.

**Implemented:**
- `analysis/feature_importance.py` — full empirical importance pipeline:
  - Joins `PositionRecord` (outcome: pnl_pips) with `SignalRecord` (feature vector) via `signal_id`
  - **Point-biserial correlation** of each of the 7 confluence factor scores + ADX + ATR vs win/loss
  - **Mean win / mean loss** score per factor (effect size = difference)
  - **SHAP approximation** when trained signal model exists: `mean(|coef * x_scaled|)` per feature
    (exact for logistic regression; avoids heavy SHAP library dependency)
  - Configurable lookback (default 180 days), per-pair or combined
  - `MIN_TRADES = 30` gate — won't compute on insufficient data
- CLI: `python main.py feature-importance` — computes, displays Rich table, saves to strategic memory
  - `--pair EURUSD` for single pair, `--days 90` for custom lookback, `--no-save` to skip persistence
- **Monthly consolidation** wired: `_run_monthly_feature_importance()` in pipeline runs on 1st of each month
  - Calls `compute_all_pairs()`, stores per-asset results in `strategy_insights.{pair}.feature_importance`
- **Research agent biasing** — `_importance_weight()` in `parameter_agent.py`:
  - Reads feature importance from strategic memory before generating candidates
  - Mutations touching factors with high absolute correlation get higher selection weight
  - Uses importance-biased weighted shuffle instead of pure random shuffle
  - `_PARAM_TO_FACTOR` maps mutation param keys to confluence factor names
  - Weight formula: `1.0 + |correlation| * 10.0` (e.g., correlation of 0.1 -> 2x priority)
- `research/history.py` — `update_feature_importance()` + `get_feature_importance()` helpers

**Output format (per factor):**
| Field | Description |
|---|---|
| `point_biserial` | Correlation with win/loss (-1 to +1, positive = helps wins) |
| `mean_win` | Average factor score on winning trades |
| `mean_loss` | Average factor score on losing trades |
| `effect_size` | mean_win - mean_loss (positive = factor higher in wins) |
| `shap_mean_abs` | Mean absolute SHAP value (when model exists) |
| `trade_count` | Number of trades with non-null data for this factor |

**Files changed:**
- `analysis/feature_importance.py` — NEW: correlation + SHAP pipeline, per-pair and combined computation
- `research/history.py` — `update_feature_importance()`, `get_feature_importance()` helpers
- `engine/pipeline.py` — `_run_monthly_feature_importance()` fills monthly placeholder
- `research/parameter_agent.py` — `_importance_weight()`, `_PARAM_TO_FACTOR`, importance-biased candidate ordering
- `main.py` — `feature-importance` CLI command

**Why sixth:** This is an enabler for Priorities 2 and 5b, but requires a meaningful sample of labeled trades first (~200+). Start collecting data now via episodic memory, act on it later.

---

## Priority 7: Regime-Aware Parameter Switching — DONE (2026-04-11)

> Inspired by: auto-stock-bot's `REGIME_ADJUSTMENTS` (different strategy weights per regime)

**Problem:** Priority 1 showed that simple ADX-based signal blocking hurts this strategy (SMC
works well in ranging markets). But using *identical* params in all conditions is still suboptimal.

**Implemented:**
- **Expanded `Regime` enum** — Added `VOLATILE` to existing TRENDING / RANGING
- **ATR percentile detection** — `calculate_atr_percentile()` in `analysis/context.py`:
  - Computes where current ATR sits relative to rolling 100-bar ATR history (0-100 percentile)
  - ATR percentile >= `atr_volatility_threshold` (default 80) → VOLATILE regime
  - VOLATILE takes priority over TRENDING/RANGING — a trending AND volatile market uses volatile params
- **Per-regime param overrides** in `score_confluence()`:
  - When `regime_params_enabled=True`, current regime's overrides are merged onto base params
  - Overridable params: `threshold`, `sl_multiplier`, `tp_risk_reward`
  - TRENDING uses base params (no override needed), RANGING and VOLATILE get custom overrides
  - Example: RANGING might use threshold=0.50, tp_risk_reward=1.5 (pickier entry, tighter TP)
  - Example: VOLATILE might use sl_multiplier=2.0 (wider SL to survive whipsaws)
- **Config structure** in `optimized_params.json`:
  ```json
  "regime_params_enabled": false,
  "atr_volatility_threshold": 80.0,
  "regime_params": {
    "trending": {},
    "ranging": {},
    "volatile": {}
  }
  ```
- **Research agent mutations** — when `regime_params_enabled` is ON:
  - Per-regime override mutations: `ranging.threshold ±0.05`, `volatile.sl_multiplier ±0.25`, etc.
  - ATR volatility threshold mutations: ±5.0 (PARAM_BOUNDS: 60-95)
  - All per-regime mutations are non-structural (daily budget, not capped)
  - Toggle `regime_params_enabled` is structural (capped at 2/run)
- **Defaults to OFF** — must be promoted via research pipeline
- Wired through: backtest engine, signal generator, market analyzer, pipeline setup, promotion

**Files changed:**
- `data/models.py` — `Regime.VOLATILE` added
- `analysis/context.py` — `calculate_atr_percentile()`, `classify_regime()` updated with volatile detection, `build_price_context()` passes ATR percentile
- `analysis/confluence.py` — `regime_params_enabled` + `regime_params` params, per-regime override merge
- `config/settings.py` — `REGIME_PARAMS_ENABLED`, `ATR_VOLATILITY_THRESHOLD`, `DEFAULT_REGIME_PARAMS`, PARAM_BOUNDS for `atr_volatility_threshold`
- `config/params.py` — load/save `regime_params_enabled`, `atr_volatility_threshold`, `regime_params`
- `config/optimized_params.json` — regime_params section (defaults OFF, empty overrides)
- `backtest/engine.py` — passes `regime_params_enabled`, `regime_params`, `atr_volatility_threshold`
- `agents/signal_generator.py` — `_regime_params_enabled`, `_regime_params` fields
- `agents/market_analyzer.py` — `atr_volatility_threshold` field, passed to `build_price_context()`
- `engine/pipeline.py` — loads regime_params config on setup
- `research/parameter_agent.py` — `_toggle_regime_params()`, `_perturb_regime_param()`, `OVERRIDE_REGIMES`, `REGIME_OVERRIDE_PARAMS`, `atr_volatility_threshold` in NUMERIC_STEPS
- `research/promotion.py` — `_strip_for_save()` includes regime_params fields

**Why here:** The regime infrastructure is already built (Priority 1). This is the "Option B"
noted in Priority 1's conclusion — param switching instead of signal blocking.

---

## Priority 8: Warm-Start Optuna from Prior Bests

**Problem:** Each Optuna optimization cold-starts from scratch, re-exploring already-tested regions of the parameter space.

**Approach:**
- Seed new Optuna studies with top-N trials from prior runs (warm-start via `study.add_trial()`)
- Maintain a persistent trial archive in `research/trial_archive.json`
- Reduce trial budget from 50 to 30 while maintaining or improving convergence

**Why last:** Performance optimization for the research pipeline. Nice-to-have, but the current 50-trial budget already works and runs within the 1-hour scheduler window.

---

## Execution Order

```
Phase A (Signal Quality):     Priority 1 -> Priority 2 -> Priority 3       [DONE]
Phase B (Automation Loop):    Priority 4 -> Priority 5 -> Priority 5b      [DONE]
Phase C (Observability):      Priority 6 -> Priority 7 -> Priority 8       [6-7 DONE, 8 NEXT]
```

Phase A is the foundation — everything else builds on having a better signal.
Phase B builds memory first, then unfreezes the learner to use it.
Phase C uses accumulated data to guide continuous improvement.
