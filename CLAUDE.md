# Auto Trading Bot - EURUSD

## Overview
Automated trading system for EURUSD using multi-timeframe analysis, market structure, and wave-based entries. Paper trading with £10,000 starting capital.

## Tech Stack
- Python 3.11+
- yfinance for data
- SQLAlchemy + SQLite for storage
- FastAPI + HTMX for dashboard
- Optuna for parameter optimization

## Architecture
Multi-agent pipeline: Data Ingestion -> Analysis -> Signal Generation -> Risk Management -> Execution -> Learning

## Key Concepts
- Markets are fractal across 15M, 1H, 4H, 1D, Weekly
- Trade impulses after corrections (wave structure)
- Break of Structure (BOS) on higher TF, entry on lower TF
- Liquidity sweeps before real moves
- Self-learning: track what works, adjust parameters automatically

## Commands
- `python main.py run` - Start the trading bot (paper trading)
- `python main.py analyze` - Run current market analysis
- `python main.py backtest` - Run backtests
- `python main.py dashboard` - Launch web dashboard
- `python main.py fetch` - Fetch latest data

## Rules
- Max 2% risk per trade
- Max 3 concurrent positions
- Daily loss limit 4%, weekly 8%
- Paper trading only until backtests prove positive expectancy over 200+ trades
