"""Core data models used throughout the trading system."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


class WavePhase(str, Enum):
    IMPULSE_UP = "impulse_up"
    CORRECTION_DOWN = "correction_down"
    IMPULSE_DOWN = "impulse_down"
    CORRECTION_UP = "correction_up"
    UNKNOWN = "unknown"


class StructureBreak(str, Enum):
    BOS = "bos"       # Break of Structure (trend continuation)
    CHOCH = "choch"   # Change of Character (potential reversal)
    NONE = "none"


class SignalType(str, Enum):
    BOS_CONTINUATION = "bos_continuation"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    WAVE_ENTRY = "wave_entry"
    SR_BOUNCE = "sr_bounce"
    WAVE_ENDING = "wave_ending"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


# --- Core Data ---

class Candle(BaseModel):
    timestamp: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low


class SwingPoint(BaseModel):
    timestamp: datetime
    price: float
    is_high: bool
    timeframe: str
    confirmed: bool = False
    strength: int = 1  # How many bars on each side confirm it


class MarketStructure(BaseModel):
    timeframe: str
    bias: Bias = Bias.RANGING
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    last_break: StructureBreak = StructureBreak.NONE
    break_price: Optional[float] = None
    break_timestamp: Optional[datetime] = None
    swing_points: list[SwingPoint] = Field(default_factory=list)


class WaveState(BaseModel):
    timeframe: str
    phase: WavePhase = WavePhase.UNKNOWN
    wave_count: int = 0
    impulse_strength: float = 0.0
    correction_depth: float = 0.0  # How deep the correction went (0-1)
    is_exhausted: bool = False


class SRZone(BaseModel):
    price_low: float
    price_high: float
    timeframe: str
    strength: int = 1  # Number of touches
    zone_type: str = "support"  # support or resistance
    last_touch: Optional[datetime] = None


class LiquiditySweep(BaseModel):
    timestamp: datetime
    price: float
    swept_level: float
    direction: Direction
    timeframe: str
    confirmed: bool = False


# --- Analysis Output ---

class TimeframeAnalysis(BaseModel):
    timeframe: str
    structure: MarketStructure
    wave: WaveState
    sr_zones: list[SRZone] = Field(default_factory=list)
    liquidity_sweeps: list[LiquiditySweep] = Field(default_factory=list)
    atr: float = 0.0
    current_price: float = 0.0


class PriceContext(BaseModel):
    """Aggregated analysis across all timeframes."""
    pair: str
    timestamp: datetime
    analyses: dict[str, TimeframeAnalysis] = Field(default_factory=dict)
    overall_bias: Bias = Bias.RANGING
    bias_strength: float = 0.0  # 0-1, how aligned are the timeframes

    def get_htf_bias(self) -> Bias:
        """Get bias from daily + weekly."""
        daily = self.analyses.get("1d")
        weekly = self.analyses.get("1wk")
        if daily and weekly:
            if daily.structure.bias == weekly.structure.bias:
                return daily.structure.bias
        if weekly:
            return weekly.structure.bias
        if daily:
            return daily.structure.bias
        return Bias.RANGING


# --- Signals & Trades ---

class Signal(BaseModel):
    timestamp: datetime
    pair: str
    direction: Direction
    signal_type: SignalType
    entry_price: float
    stop_loss: float
    take_profit: float
    confluence_score: float  # 0-1
    rationale: dict = Field(default_factory=dict)
    entry_timeframe: str = "15m"
    trigger_timeframe: str = "4h"


class Position(BaseModel):
    id: Optional[int] = None
    signal: Signal
    status: TradeStatus = TradeStatus.OPEN
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    size: float = 0.0  # Units
    risk_amount: float = 0.0  # GBP at risk
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    pnl: float = 0.0
    pnl_pips: float = 0.0
    tags: list[str] = Field(default_factory=list)


class TradeResult(BaseModel):
    position: Position
    analysis_snapshot: Optional[PriceContext] = None
    max_favorable: float = 0.0  # Max pips in our favor
    max_adverse: float = 0.0    # Max pips against us
    duration_minutes: int = 0


# --- Learning ---

class SetupStats(BaseModel):
    setup_type: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    avg_win_pips: float = 0.0
    avg_loss_pips: float = 0.0
    win_rate: float = 0.0
    expectancy: float = 0.0  # Average pips per trade
    profit_factor: float = 0.0


class ParameterSet(BaseModel):
    version: int = 1
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    confluence_threshold: float = 0.60
    swing_lookback: int = 5
    sl_atr_multiplier: float = 1.5
    tp_risk_reward: float = 2.0
    confluence_weights: dict[str, float] = Field(default_factory=dict)
    performance_score: float = 0.0
