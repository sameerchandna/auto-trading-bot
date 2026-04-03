"""Backtest configuration — toggleable rules for A/B testing."""
from dataclasses import dataclass, field


@dataclass
class BacktestConfig:
    """Controls which rules are active during a backtest run.

    Each flag can be toggled independently so you can test the impact
    of one change at a time vs. the baseline.
    """

    # --- Position filtering ---
    no_overlap: bool = False
    """Block a new entry if a position in the same direction is already open."""

    min_score: float = 0.0
    """Additional min score filter on top of params threshold (0=disabled)."""

    # --- Time filtering ---
    block_hours: list[int] = field(default_factory=list)
    """UTC hours to skip entirely (e.g. [17, 18, 19] blocks 17:00-19:59 UTC)."""

    block_days: list[int] = field(default_factory=list)
    """Weekdays to skip: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun."""

    # --- Loss management ---
    cooldown_after_losses: int = 0
    """Skip the next N signals after a run of this many consecutive losses."""

    # --- Timeframe filtering ---
    exclude_timeframes: list[str] = field(default_factory=list)
    """Timeframes to exclude from analysis (e.g. ['15m'] to skip 15-min candles)."""

    def label(self) -> str:
        """Short human-readable label for this config."""
        parts = []
        if self.no_overlap:
            parts.append("no-overlap")
        if self.min_score > 0:
            parts.append(f"score>={self.min_score:.2f}")
        if self.block_hours:
            parts.append(f"block-h{self.block_hours}")
        if self.block_days:
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            parts.append(f"block-{'+'.join(day_names[d] for d in self.block_days)}")
        if self.cooldown_after_losses:
            parts.append(f"cooldown-{self.cooldown_after_losses}")
        if self.exclude_timeframes:
            parts.append(f"no-{'+'.join(self.exclude_timeframes)}")
        return ", ".join(parts) if parts else "baseline"

    def tags(self) -> list[str]:
        """Tags to store on each trade record."""
        t = ["backtest"]
        if self.no_overlap:
            t.append("no_overlap")
        if self.min_score > 0:
            t.append(f"min_score_{self.min_score:.2f}")
        if self.block_hours:
            t.append("block_hours")
        if self.block_days:
            t.append("block_days")
        if self.cooldown_after_losses:
            t.append(f"cooldown_{self.cooldown_after_losses}")
        if self.exclude_timeframes:
            t.append(f"no_{'_'.join(self.exclude_timeframes)}")
        return t


# Pre-defined configs for common tests
BASELINE = BacktestConfig()

# Current production config — matches live trading rules in settings.py
# Based on A/B analysis: +8.7% WR, profit factor 2.07, max DD 9% vs 26.4%
PRODUCTION = BacktestConfig(
    no_overlap=True,
    block_hours=[17, 18, 19, 20],
)

NO_OVERLAP = BacktestConfig(no_overlap=True)
HIGH_SCORE = BacktestConfig(min_score=0.50)
BLOCK_BAD_HOURS = BacktestConfig(block_hours=[17, 18, 19, 20])
BLOCK_BAD_DAYS = BacktestConfig(block_days=[3, 4])  # Thu, Fri
COOLDOWN_2 = BacktestConfig(cooldown_after_losses=2)

# Production without 15m — test if removing 15m candles matches PineScript (1H entry TF)
PRODUCTION_NO_15M = BacktestConfig(
    no_overlap=True,
    block_hours=[17, 18, 19, 20],
    exclude_timeframes=["15m"],
)
