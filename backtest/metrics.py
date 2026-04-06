"""Performance metrics calculation for backtests and live trading."""
import numpy as np
from datetime import datetime

from data.models import Position


def calculate_metrics(
    trades: list[Position],
    initial_capital: float,
    equity_curve: list[tuple[datetime, float]],
) -> dict:
    """Calculate comprehensive trading performance metrics."""
    if not trades:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_win_pips": 0.0,
            "avg_loss_pips": 0.0,
            "profit_factor": 0.0,
            "total_pnl": 0.0,
            "total_pnl_pips": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "expectancy_pips": 0.0,
            "avg_trade_duration_min": 0,
            "best_trade_pips": 0.0,
            "worst_trade_pips": 0.0,
            "consecutive_wins": 0,
            "consecutive_losses": 0,
            "long_trades": 0,
            "long_wins": 0,
            "long_pnl": 0,
            "short_trades": 0,
            "short_wins": 0,
            "short_pnl": 0,
        }

    from data.models import Direction
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    longs = [t for t in trades if t.signal.direction == Direction.LONG]
    shorts = [t for t in trades if t.signal.direction == Direction.SHORT]

    win_pips = [t.pnl_pips for t in wins]
    loss_pips = [abs(t.pnl_pips) for t in losses]
    all_pips = [t.pnl_pips for t in trades]

    total_pnl = sum(t.pnl for t in trades)
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    # Max drawdown
    max_dd, max_dd_pct = _calculate_drawdown(equity_curve, initial_capital)

    # Sharpe ratio (annualized, using daily returns)
    sharpe = _calculate_sharpe(equity_curve)

    # Sortino ratio
    sortino = _calculate_sortino(equity_curve)

    # Consecutive wins/losses
    max_consec_wins, max_consec_losses = _consecutive_streaks(trades)

    # Average duration
    durations = []
    for t in trades:
        if t.opened_at and t.closed_at:
            dur = (t.closed_at - t.opened_at).total_seconds() / 60
            durations.append(dur)

    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = np.mean(win_pips) if win_pips else 0
    avg_loss = np.mean(loss_pips) if loss_pips else 0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "avg_win_pips": round(float(avg_win), 1),
        "avg_loss_pips": round(float(avg_loss), 1),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pips": round(sum(all_pips), 1),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "expectancy_pips": round(
            win_rate * float(avg_win) - (1 - win_rate) * float(avg_loss), 1
        ),
        "avg_trade_duration_min": round(np.mean(durations)) if durations else 0,
        "best_trade_pips": round(max(all_pips), 1) if all_pips else 0,
        "worst_trade_pips": round(min(all_pips), 1) if all_pips else 0,
        "consecutive_wins": max_consec_wins,
        "consecutive_losses": max_consec_losses,
        "long_trades": len(longs),
        "long_wins": sum(1 for t in longs if t.pnl > 0),
        "long_pnl": round(sum(t.pnl for t in longs), 2),
        "short_trades": len(shorts),
        "short_wins": sum(1 for t in shorts if t.pnl > 0),
        "short_pnl": round(sum(t.pnl for t in shorts), 2),
    }


def _calculate_drawdown(
    equity_curve: list[tuple[datetime, float]],
    initial_capital: float,
) -> tuple[float, float]:
    """Calculate maximum drawdown in absolute and percentage terms."""
    if not equity_curve:
        return 0.0, 0.0

    values = [v for _, v in equity_curve]
    peak = values[0]
    max_dd = 0.0
    peak_at_max_dd = peak

    for value in values:
        if value > peak:
            peak = value
        dd = peak - value
        if dd > max_dd:
            max_dd = dd
            peak_at_max_dd = peak

    max_dd_pct = max_dd / peak_at_max_dd if peak_at_max_dd > 0 else 0
    return max_dd, max_dd_pct


def _daily_excess_returns(
    equity_curve: list[tuple[datetime, float]],
    risk_free_rate: float = 0.04,
) -> list[float]:
    """Resample 1H equity curve to daily (last value per calendar day) and return excess returns."""
    if len(equity_curve) < 2:
        return []

    daily: dict = {}
    for dt, v in equity_curve:
        daily[dt.date()] = v  # last value per day wins

    daily_values = list(daily.values())
    if len(daily_values) < 2:
        return []

    daily_rf = risk_free_rate / 252
    returns = [
        (daily_values[i] - daily_values[i - 1]) / daily_values[i - 1]
        for i in range(1, len(daily_values))
        if daily_values[i - 1] != 0
    ]
    return [r - daily_rf for r in returns]


def _calculate_sharpe(
    equity_curve: list[tuple[datetime, float]],
    risk_free_rate: float = 0.04,
) -> float:
    """Calculate annualized Sharpe ratio using daily returns."""
    excess = _daily_excess_returns(equity_curve, risk_free_rate)
    if not excess or np.std(excess) == 0:
        return 0.0
    return float(np.mean(excess) / np.std(excess) * np.sqrt(252))


def _calculate_sortino(
    equity_curve: list[tuple[datetime, float]],
    risk_free_rate: float = 0.04,
) -> float:
    """Calculate annualized Sortino ratio using daily returns (only penalizes downside vol)."""
    excess = _daily_excess_returns(equity_curve, risk_free_rate)
    if not excess:
        return 0.0

    downside = [r for r in excess if r < 0]
    if not downside:
        return 0.0

    downside_std = np.std(downside)
    if downside_std == 0:
        return 0.0

    return float(np.mean(excess) / downside_std * np.sqrt(252))


def _consecutive_streaks(trades: list[Position]) -> tuple[int, int]:
    """Calculate max consecutive wins and losses."""
    if not trades:
        return 0, 0

    max_wins = max_losses = 0
    current_wins = current_losses = 0

    for t in trades:
        if t.pnl > 0:
            current_wins += 1
            current_losses = 0
            max_wins = max(max_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_losses = max(max_losses, current_losses)

    return max_wins, max_losses
