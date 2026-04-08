# Code Review Rules

These are the rules ReviewAgent (`agents/review_agent.py`) checks on every run.
Each rule has an ID. Findings are tagged with the ID so the FixAgent and the
approval queue can reference them precisely.

Scanned directories: `agents/`, `analysis/`, `backtest/`, `engine/`, `data/`.

---

## CRITICAL — must fix, blocks live trading

- **CR-001** — Hardcoded values that should be config (prices, thresholds, multipliers)
- **CR-002** — Risk limits not enforced in backtest (daily/weekly loss limits)
- **CR-003** — Currency conversion missing for non-USD P&L calculations
- **CR-004** — Lookahead bias: using current unfinished candle in analysis
- **CR-005** — Position sizing formula errors
- **CR-006** — Signal skipping not propagating correctly (None checks)

## WARNING — fix soon, degrades results or reliability

- **WR-001** — N+1 database query patterns (individual queries inside loops)
- **WR-002** — Hardcoded timeframe strings (`"15m"`, `"1H"`, `"4h"`, etc.)
  outside `config/`
- **WR-003** — Metrics calculations using wrong denominators or periods
- **WR-004** — Silent failures: bare `except:` or `except Exception: pass`
- **WR-005** — Placeholder code left active with non-zero weight
- **WR-006** — Duplicate logic across backtest and live pipeline

## SUGGESTION — improve when convenient

- **SG-001** — Missing type hints on public function signatures
- **SG-002** — Functions longer than 60 lines
- **SG-003** — Missing docstrings on public functions
- **SG-004** — Unused imports
- **SG-005** — Test coverage gaps on critical path

## NEVER FLAG

- Style preferences (formatting, naming conventions already in use)
- Working code that passes backtests even if not optimal
- Any change that would alter live trading behaviour without a backtest comparison

---

## Detection notes

Some rules above are checked heuristically by `review_agent.py`. The current
implementation covers the rules listed in `agents/review_agent.py:RULE_CHECKS`;
others remain as references for manual review and can be added incrementally.
A rule that's listed here but not yet automated still belongs in the doc — it
defines the *spec*, the agent catches up over time.
