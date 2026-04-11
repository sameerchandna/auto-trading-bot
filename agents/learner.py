"""Learner Agent - tracks performance and proposes weight adjustments."""
import json
import logging
from datetime import datetime, timezone

from agents.base import BaseAgent
from data.models import Position, SetupStats, ParameterSet, TradeStatus
from storage.database import (
    PositionRecord, ParameterRecord, get_session, query_recent_closed_positions,
)
from config.settings import (
    MIN_TRADES_FOR_STATS, OPTIMIZATION_INTERVAL, PARAM_BOUNDS,
    CONFLUENCE_WEIGHTS, LEARNER_MAX_WEIGHT_DELTA,
    LEARNER_DD_KILL_PCT, LEARNER_MIN_WR_FOR_BOOST, LEARNER_MIN_PF_FOR_BOOST,
)
from config.params import load_strategy_params
from engine.event_bus import bus
from storage.database import log_audit

logger = logging.getLogger(__name__)

# Map signal types to the confluence weight they most depend on.
# Used to decide which weight to nudge based on setup-type performance.
SETUP_WEIGHT_MAP: dict[str, str] = {
    "bos_continuation": "bos",
    "liquidity_sweep": "liquidity_sweep",
    "wave_entry": "wave_position",
    "sr_bounce": "sr_reaction",
    "wave_ending": "wave_ending",
}

WEIGHT_KEYS = [
    "htf_bias", "bos", "wave_position",
    "liquidity_sweep", "sr_reaction", "wave_ending", "catalyst",
]


class LearnerAgent(BaseAgent):
    """Tracks what works and proposes conservative weight adjustments."""

    def __init__(self, frozen: bool = True):
        super().__init__("learner")
        # When frozen=True, learner is stats-only: no param mutation, no snapshots.
        # Set frozen=False via learner_enabled config to activate proposals.
        self.frozen = frozen
        self.trade_count = 0
        self.setup_stats: dict[str, SetupStats] = {}
        self.current_params = ParameterSet(
            confluence_weights=CONFLUENCE_WEIGHTS.copy()
        )
        self._auto_promote: bool = False  # set by pipeline from config

    def process(self, data: dict) -> dict:
        """Process closed trades and potentially propose weight adjustments.

        Input: {"closed_position": Position}
        Output: {"stats": dict, "params_updated": bool}
        """
        position = data.get("closed_position")
        if not position:
            return {"stats": {}, "params_updated": False}

        self.trade_count += 1
        self._update_stats(position)

        params_updated = False
        if self.frozen:
            self.logger.debug("Learner frozen — skipping parameter optimization")
        elif self.trade_count % OPTIMIZATION_INTERVAL == 0:
            params_updated = self._optimize_parameters()

        return {
            "stats": {k: v.model_dump() for k, v in self.setup_stats.items()},
            "params_updated": params_updated,
        }

    # ------------------------------------------------------------------
    # Stats tracking (always active, even when frozen)
    # ------------------------------------------------------------------

    def _update_stats(self, position: Position):
        """Update win/loss stats per setup type."""
        setup_type = position.signal.signal_type.value

        if setup_type not in self.setup_stats:
            self.setup_stats[setup_type] = SetupStats(setup_type=setup_type)

        stats = self.setup_stats[setup_type]
        stats.total_trades += 1

        if position.pnl > 0:
            stats.wins += 1
            stats.avg_win_pips = (
                (stats.avg_win_pips * (stats.wins - 1) + position.pnl_pips) / stats.wins
            )
        else:
            stats.losses += 1
            stats.avg_loss_pips = (
                (stats.avg_loss_pips * (stats.losses - 1) + abs(position.pnl_pips)) / stats.losses
            )

        if stats.total_trades > 0:
            stats.win_rate = stats.wins / stats.total_trades

        if stats.avg_loss_pips > 0:
            stats.profit_factor = (
                (stats.avg_win_pips * stats.wins) /
                (stats.avg_loss_pips * stats.losses) if stats.losses > 0 else float("inf")
            )

        stats.expectancy = (
            stats.win_rate * stats.avg_win_pips -
            (1 - stats.win_rate) * stats.avg_loss_pips
        )

        self.logger.info(
            f"Stats [{setup_type}]: {stats.total_trades} trades | "
            f"WR={stats.win_rate:.1%} | E={stats.expectancy:+.1f} pips"
        )

    # ------------------------------------------------------------------
    # Guardrailed optimization (only when unfrozen)
    # ------------------------------------------------------------------

    def _optimize_parameters(self) -> bool:
        """Analyse rolling trades and propose a small weight adjustment."""
        positions = query_recent_closed_positions(n=OPTIMIZATION_INTERVAL)
        if len(positions) < MIN_TRADES_FOR_STATS:
            self.logger.info(
                f"Not enough recent trades for optimization "
                f"({len(positions)}/{MIN_TRADES_FOR_STATS})"
            )
            return False

        metrics = self._compute_rolling_metrics(positions)

        # Kill switch — freeze if drawdown too high
        if self._check_kill_switch(metrics):
            return False

        # Log performance snapshot
        self.logger.info(f"Performance snapshot at trade #{self.trade_count}:")
        self.logger.info(
            f"  Rolling {metrics['total_trades']} trades: "
            f"WR={metrics['win_rate']:.1%} PF={metrics['profit_factor']:.2f} "
            f"DD={metrics['max_dd_pct']:.1%}"
        )
        for st, sm in metrics["per_setup"].items():
            self.logger.info(
                f"  {st}: {sm['trades']} trades WR={sm['win_rate']:.1%} "
                f"PF={sm['profit_factor']:.2f}"
            )

        # Propose weight changes
        proposal = self._propose_weight_adjustment(metrics)
        if proposal is None:
            self.logger.info("No meaningful weight changes to propose")
            return False

        # Record in strategic memory
        self._record_proposal(proposal, metrics)

        # Route through approval / auto-apply
        if self._auto_promote:
            self._auto_apply(proposal)
        else:
            from research.promotion import push_learner_proposal
            push_learner_proposal(proposal)
            self.logger.info(
                f"Learner proposal {proposal['proposal_id']} pushed to approval queue"
            )

        log_audit("learner", "weight_proposal", None, {
            "proposal_id": proposal["proposal_id"],
            "trade_count": self.trade_count,
            "weight_deltas": proposal["weight_deltas"],
            "auto_applied": self._auto_promote,
        })

        return True

    def _compute_rolling_metrics(self, positions: list[PositionRecord]) -> dict:
        """Compute aggregate and per-setup metrics from recent closed positions."""
        total = len(positions)
        wins = sum(1 for p in positions if p.pnl and p.pnl > 0)
        losses = total - wins

        total_win_pips = sum(p.pnl_pips for p in positions if p.pnl and p.pnl > 0)
        total_loss_pips = sum(abs(p.pnl_pips) for p in positions if p.pnl and p.pnl <= 0)

        win_rate = wins / total if total > 0 else 0.0
        profit_factor = (total_win_pips / total_loss_pips) if total_loss_pips > 0 else float("inf")

        avg_win = total_win_pips / wins if wins > 0 else 0.0
        avg_loss = total_loss_pips / losses if losses > 0 else 0.0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        # Approximate rolling max drawdown from cumulative PnL
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in sorted(positions, key=lambda x: x.closed_at or datetime.min):
            cum_pnl += (p.pnl_pips or 0.0)
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd:
                max_dd = dd
        max_dd_pct = max_dd / peak if peak > 0 else 0.0

        # Per-setup breakdown
        per_setup: dict[str, dict] = {}
        for p in positions:
            st = p.signal_type or "unknown"
            if st not in per_setup:
                per_setup[st] = {"trades": 0, "wins": 0, "win_pips": 0.0, "loss_pips": 0.0}
            per_setup[st]["trades"] += 1
            if p.pnl and p.pnl > 0:
                per_setup[st]["wins"] += 1
                per_setup[st]["win_pips"] += p.pnl_pips or 0.0
            else:
                per_setup[st]["loss_pips"] += abs(p.pnl_pips or 0.0)

        for st, sm in per_setup.items():
            sm["win_rate"] = sm["wins"] / sm["trades"] if sm["trades"] > 0 else 0.0
            sm["profit_factor"] = (
                sm["win_pips"] / sm["loss_pips"]
                if sm["loss_pips"] > 0 else float("inf")
            )

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "expectancy": expectancy,
            "max_dd_pct": max_dd_pct,
            "per_setup": per_setup,
        }

    def _check_kill_switch(self, metrics: dict) -> bool:
        """Freeze the learner if rolling drawdown exceeds the kill threshold."""
        if metrics["max_dd_pct"] > LEARNER_DD_KILL_PCT:
            self.frozen = True
            self.logger.warning(
                f"KILL SWITCH: rolling DD {metrics['max_dd_pct']:.1%} > "
                f"{LEARNER_DD_KILL_PCT:.0%} — learner frozen"
            )
            log_audit("learner", "kill_switch", None, {
                "max_dd_pct": metrics["max_dd_pct"],
                "threshold": LEARNER_DD_KILL_PCT,
                "trade_count": self.trade_count,
            })
            return True
        return False

    def _propose_weight_adjustment(self, metrics: dict) -> dict | None:
        """Propose small weight changes based on per-setup performance.

        Returns a proposal dict or None if no meaningful change.
        """
        current_params = load_strategy_params()
        current_weights = current_params["weights"].copy()
        overall_wr = metrics["win_rate"]

        deltas: dict[str, float] = {k: 0.0 for k in WEIGHT_KEYS}

        for setup_type, sm in metrics["per_setup"].items():
            weight_key = SETUP_WEIGHT_MAP.get(setup_type)
            if not weight_key or sm["trades"] < 5:
                continue

            if (sm["win_rate"] >= LEARNER_MIN_WR_FOR_BOOST
                    and sm["profit_factor"] >= LEARNER_MIN_PF_FOR_BOOST):
                # Outperforming — nudge up
                deltas[weight_key] += LEARNER_MAX_WEIGHT_DELTA
            elif sm["win_rate"] < overall_wr and sm["profit_factor"] < 1.0:
                # Underperforming — nudge down
                deltas[weight_key] -= LEARNER_MAX_WEIGHT_DELTA

        # Clamp deltas
        for k in WEIGHT_KEYS:
            deltas[k] = max(-LEARNER_MAX_WEIGHT_DELTA,
                            min(LEARNER_MAX_WEIGHT_DELTA, deltas[k]))

        # Apply deltas and clamp to PARAM_BOUNDS
        new_weights = {}
        for k in WEIGHT_KEYS:
            lo, hi = PARAM_BOUNDS.get(f"{k}_weight", (0.0, 1.0))
            new_weights[k] = max(lo, min(hi, current_weights.get(k, 0.0) + deltas[k]))

        # Normalize to sum ≈ 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        # Check if anything actually changed
        meaningful = any(
            abs(new_weights.get(k, 0) - current_weights.get(k, 0)) > 1e-4
            for k in WEIGHT_KEYS
        )
        if not meaningful:
            return None

        # Build the full params dict for saving
        new_params = dict(current_params)
        new_params["weights"] = new_weights

        from research.history import hash_params
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        proposal_id = f"L-{now_iso[:10]}-{self.trade_count}"

        return {
            "proposal_id": proposal_id,
            "trade_count": self.trade_count,
            "before_weights": current_weights,
            "after_weights": new_weights,
            "weight_deltas": {k: round(new_weights[k] - current_weights.get(k, 0), 4)
                              for k in WEIGHT_KEYS},
            "metrics": metrics,
            "params": new_params,
            "params_hash": hash_params(new_params),
        }

    def _auto_apply(self, proposal: dict) -> None:
        """Auto-apply a learner proposal (when auto_promote_enabled)."""
        from research.promotion import auto_apply_promotion
        from research import history

        history_data = history.load()
        ok = auto_apply_promotion(proposal, history_data)
        if ok:
            history.save(history_data)
            self.logger.info(
                f"Auto-applied learner proposal {proposal['proposal_id']}"
            )
        else:
            self.logger.warning(
                f"Failed to auto-apply learner proposal {proposal['proposal_id']}"
            )

    def _record_proposal(self, proposal: dict, metrics: dict) -> None:
        """Record the proposal in strategic memory (test_history.json)."""
        from research import history

        history_data = history.load()
        history.record_learner_proposal(history_data, {
            "proposal_id": proposal["proposal_id"],
            "trade_count": self.trade_count,
            "before_weights": proposal["before_weights"],
            "after_weights": proposal["after_weights"],
            "weight_deltas": proposal["weight_deltas"],
            "rolling_wr": metrics["win_rate"],
            "rolling_pf": metrics["profit_factor"],
            "rolling_dd": metrics["max_dd_pct"],
            "auto_applied": self._auto_promote,
        })
        history.save(history_data)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _save_params(self):
        """Save current parameters to database."""
        session = get_session()
        try:
            self.current_params.version += 1
            self.current_params.timestamp = datetime.utcnow()

            rec = ParameterRecord(
                version=self.current_params.version,
                timestamp=self.current_params.timestamp,
                params_json=self.current_params.model_dump_json(),
                performance_score=self.current_params.performance_score,
            )
            session.add(rec)
            session.commit()
            self.logger.info(f"Saved parameter set v{self.current_params.version}")
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to save params: {e}")
        finally:
            session.close()

    def load_stats_from_db(self):
        """Load historical trade stats from database."""
        session = get_session()
        try:
            positions = session.query(PositionRecord).filter_by(
                status="closed"
            ).all()

            for rec in positions:
                setup_type = rec.signal_type or "unknown"
                if setup_type not in self.setup_stats:
                    self.setup_stats[setup_type] = SetupStats(setup_type=setup_type)

                stats = self.setup_stats[setup_type]
                stats.total_trades += 1

                if rec.pnl and rec.pnl > 0:
                    stats.wins += 1

            # Recalculate rates
            for stats in self.setup_stats.values():
                stats.losses = stats.total_trades - stats.wins
                if stats.total_trades > 0:
                    stats.win_rate = stats.wins / stats.total_trades

            self.trade_count = sum(s.total_trades for s in self.setup_stats.values())
            self.logger.info(f"Loaded {self.trade_count} historical trades")
        finally:
            session.close()

    def get_learning_summary(self) -> dict:
        """Get summary of learning progress."""
        return {
            "total_trades_analyzed": self.trade_count,
            "frozen": self.frozen,
            "setup_stats": {
                k: v.model_dump() for k, v in self.setup_stats.items()
            },
            "current_params_version": self.current_params.version,
            "next_optimization_at": (
                (self.trade_count // OPTIMIZATION_INTERVAL + 1) * OPTIMIZATION_INTERVAL
            ),
        }
