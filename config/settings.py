"""Central configuration for the trading system."""
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "storage" / "trading.db"
LOG_DIR = PROJECT_ROOT / "logs"

# Trading pair
PAIR = "EURUSD=X"
PAIR_NAME = "EURUSD"

# Capital
STARTING_CAPITAL = 10_000  # GBP
CURRENCY = "GBP"

# Timeframes (Yahoo Finance format)
TIMEFRAMES = ["15m", "1h", "4h", "1d", "1wk"]
TIMEFRAME_MINUTES = {
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
    "1wk": 10080,
}

# Timeframe hierarchy (higher = more weight)
TIMEFRAME_WEIGHT = {
    "15m": 1,
    "1h": 2,
    "4h": 3,
    "1d": 4,
    "1wk": 5,
}

# Risk management (HARD LIMITS - never modified by learning)
MAX_RISK_PER_TRADE = 0.02        # 2% of capital per trade
MAX_CONCURRENT_POSITIONS = 3
MAX_PORTFOLIO_RISK = 0.06        # 6% total exposure
DAILY_LOSS_LIMIT = 0.04          # 4% - stop trading for the day
WEEKLY_LOSS_LIMIT = 0.08         # 8% - reduce size next week

# Trading rules (evidence-based, from backtest A/B analysis)
NO_OVERLAP_ENTRIES = True         # Block same-direction entry if position already open
BLOCK_HOURS_UTC = [17, 18, 19, 20]  # Skip signals during these UTC hours (low-quality window)

# Learnable parameters (defaults - adjusted by optimizer)
CONFLUENCE_THRESHOLD = 0.60       # Minimum score to generate signal
SWING_LOOKBACK = 5                # Bars for swing point detection
ATR_PERIOD = 14                   # ATR for volatility measurement
SL_ATR_MULTIPLIER = 1.5          # Stop loss = entry +/- ATR * multiplier
TP_RISK_REWARD = 2.0             # Take profit = 2:1 risk/reward minimum

# Confluence weights (learnable)
CONFLUENCE_WEIGHTS = {
    "htf_bias": 0.25,            # Higher timeframe bias alignment
    "bos": 0.20,                 # Break of structure
    "wave_position": 0.15,       # Trading with impulse
    "liquidity_sweep": 0.15,     # Key level swept
    "sr_reaction": 0.10,         # At significant S/R level
    "catalyst": 0.10,            # News supports direction
    "wave_ending": 0.05,         # Wave exhaustion signal
}

# Learnable parameter bounds (optimizer cannot go outside these)
PARAM_BOUNDS = {
    "confluence_threshold": (0.40, 0.80),
    "swing_lookback": (3, 10),
    "sl_atr_multiplier": (1.0, 3.0),
    "tp_risk_reward": (1.5, 4.0),
    "htf_bias_weight": (0.10, 0.40),
    "bos_weight": (0.10, 0.35),
    "wave_position_weight": (0.05, 0.25),
    "liquidity_sweep_weight": (0.05, 0.25),
    "sr_reaction_weight": (0.05, 0.20),
    "catalyst_weight": (0.0, 0.20),
    "wave_ending_weight": (0.0, 0.15),
}

# Data fetching
MAX_HISTORY_DAYS = {
    "15m": 59,      # Yahoo limit for intraday
    "1h": 729,
    "4h": 729,
    "1d": 3650,     # ~10 years
    "1wk": 3650,
}

# Learning
MIN_TRADES_FOR_STATS = 30         # Minimum trades before computing setup stats
OPTIMIZATION_INTERVAL = 50        # Re-optimize after N completed trades
LEARNING_RATE = 0.1               # How fast parameters adjust

# Dashboard
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8050
