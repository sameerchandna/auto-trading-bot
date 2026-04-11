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
SIGNAL_DOMINANCE_MARGIN = 0.10    # Winning direction must outscore loser by this much
SWING_LOOKBACK = 5                # Bars for swing point detection
ATR_PERIOD = 14                   # ATR for volatility measurement
SL_ATR_MULTIPLIER = 1.5          # Stop loss = entry +/- ATR * multiplier
TP_RISK_REWARD = 2.0             # Take profit = 2:1 risk/reward minimum

# Regime filter (toggleable — disabled by default until backtested)
REGIME_FILTER_ENABLED = False    # When True, block signals in ranging markets
REGIME_ADX_THRESHOLD = 25.0      # ADX below this = ranging (Wilder's classic)
REGIME_ADX_TIMEFRAME = "4h"      # Which TF to read ADX from

# Regime-aware parameter switching (per-regime overrides for SL/TP/threshold)
REGIME_PARAMS_ENABLED = False    # When True, apply per-regime param overrides
ATR_VOLATILITY_THRESHOLD = 80.0  # ATR percentile above this = VOLATILE regime
DEFAULT_REGIME_PARAMS = {
    "trending": {},              # Empty = use base params unchanged
    "ranging": {},               # Will be populated by research agent
    "volatile": {},              # Will be populated by research agent
}

# News filter (toggleable — disabled by default until backtested)
NEWS_FILTER_ENABLED = False      # When True, block signals around high-impact news
NEWS_BLOCK_BEFORE_MINS = 30      # Minutes before event to block
NEWS_BLOCK_AFTER_MINS = 15       # Minutes after event to block

# Auto-promotion (research pipeline applies winners without manual approval)
AUTO_PROMOTE_ENABLED = False     # When True, AUTO_PROMOTED candidates apply immediately

# Confluence weights (learnable)
CONFLUENCE_WEIGHTS = {
    "htf_bias": 0.25,            # Higher timeframe bias alignment
    "bos": 0.20,                 # Break of structure
    "wave_position": 0.15,       # Trading with impulse
    "liquidity_sweep": 0.15,     # Key level swept
    "sr_reaction": 0.10,         # At significant S/R level
    "catalyst": 0.0,             # Disabled until news feed is implemented
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
    "regime_adx_threshold": (15.0, 35.0),
    "signal_model_min_confidence": (0.35, 0.65),
    "news_block_before_mins": (15, 60),
    "news_block_after_mins": (5, 30),
    "atr_volatility_threshold": (60.0, 95.0),
}

# Data fetching — always OANDA, fixed start dates matched to EURUSD history depth
from datetime import datetime
HISTORY_START = {
    "15m": datetime(2023, 1, 1),
    "1h":  datetime(2023, 1, 1),
    "4h":  datetime(2016, 4, 4),
    "1d":  datetime(2016, 4, 4),
    "1wk": datetime(2016, 4, 1),
}

# Learning
MIN_TRADES_FOR_STATS = 30         # Minimum trades before computing setup stats
OPTIMIZATION_INTERVAL = 50        # Re-optimize after N completed trades
LEARNING_RATE = 0.1               # How fast parameters adjust
LEARNER_ENABLED = False            # Learner defaults to frozen (stats-only)
LEARNER_MAX_WEIGHT_DELTA = 0.05   # Max weight change per factor per cycle
LEARNER_DD_KILL_PCT = 0.15        # Freeze learner if rolling DD exceeds 15%
LEARNER_MIN_WR_FOR_BOOST = 0.45   # Only boost weights for setups with WR >= this
LEARNER_MIN_PF_FOR_BOOST = 1.20   # Only boost weights for setups with PF >= this

# Dashboard
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8050
