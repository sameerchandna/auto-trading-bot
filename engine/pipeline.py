"""Main trading pipeline - orchestrates the full agent loop."""
import logging
import time
from datetime import datetime

from agents.market_analyzer import MarketAnalyzerAgent
from agents.signal_generator import SignalGeneratorAgent
from agents.risk_manager import RiskManagerAgent
from agents.executor import ExecutorAgent
from agents.learner import LearnerAgent
from data.ingestion import fetch_candles, load_candles, save_candles, get_live_price
from data.models import Candle, CycleContext, CycleSignalRecord, Direction
from engine.event_bus import bus
from config.settings import TIMEFRAMES, TIMEFRAME_MINUTES, NO_OVERLAP_ENTRIES, BLOCK_HOURS_UTC
from config.assets import ACTIVE_ASSETS, get_asset, resolve_pair_name
from storage.database import log_audit

logger = logging.getLogger(__name__)


class TradingPipeline:
    """Main orchestration loop for the trading system."""

    def __init__(self, pairs: list[str] | None = None):
        self.pairs = pairs or list(ACTIVE_ASSETS)
        self.analyzer = MarketAnalyzerAgent()
        self.signal_gen = SignalGeneratorAgent()
        self.risk_mgr = RiskManagerAgent()
        self.executor = ExecutorAgent()
        self.learner = LearnerAgent()

        self._running = False
        self._last_fetch: dict[str, datetime] = {}
        self._last_prices: dict[str, float] = {}   # cached live prices for equity snapshot
        self._peak_equity: float = 0.0
        self._using_default_params: bool = False

        # Wire up events
        bus.subscribe("position_closed", self._on_position_closed)

    def setup(self):
        """Initialize all agents and load optimized parameters."""
        for agent in [self.analyzer, self.signal_gen, self.risk_mgr,
                      self.executor, self.learner]:
            agent.setup()
        self.learner.load_stats_from_db()

        # Load optimized parameters
        try:
            from backtest.optimizer import apply_optimized_params
            from config.params import load_strategy_params
            if apply_optimized_params():
                # Update signal generator weights + regime config
                from config.settings import CONFLUENCE_WEIGHTS
                self.signal_gen.update_weights(CONFLUENCE_WEIGHTS)
                params = load_strategy_params()
                self.signal_gen._regime_filter_enabled = params.get(
                    "regime_filter_enabled", False
                )
                self.signal_gen._signal_model_enabled = params.get(
                    "signal_model_enabled", False
                )
                self.signal_gen._signal_model_min_confidence = params.get(
                    "signal_model_min_confidence", 0.5
                )
                self.analyzer.adx_threshold = params.get(
                    "regime_adx_threshold", 25.0
                )
                self.analyzer.atr_volatility_threshold = params.get(
                    "atr_volatility_threshold", 80.0
                )
                self.signal_gen._regime_params_enabled = params.get(
                    "regime_params_enabled", False
                )
                self.signal_gen._regime_params = params.get(
                    "regime_params", {}
                )
                self.signal_gen._news_filter_enabled = params.get(
                    "news_filter_enabled", False
                )
                self.signal_gen._news_block_before_mins = params.get(
                    "news_block_before_mins", 30
                )
                self.signal_gen._news_block_after_mins = params.get(
                    "news_block_after_mins", 15
                )
                # Learner config
                learner_enabled = params.get("learner_enabled", False)
                self.learner.frozen = not learner_enabled
                self.learner._auto_promote = params.get(
                    "auto_promote_enabled", False
                )
                logger.info(
                    f"Loaded optimized parameters "
                    f"(regime_filter={'ON' if self.signal_gen._regime_filter_enabled else 'OFF'}, "
                    f"regime_params={'ON' if self.signal_gen._regime_params_enabled else 'OFF'}, "
                    f"signal_model={'ON' if self.signal_gen._signal_model_enabled else 'OFF'}, "
                    f"news_filter={'ON' if self.signal_gen._news_filter_enabled else 'OFF'}, "
                    f"learner={'ON' if learner_enabled else 'OFF'})"
                )
            else:
                self._using_default_params = True
                logger.warning(
                    "No optimized params found — running on defaults. "
                    "Run 'python main.py backtest' to generate them."
                )
        except Exception as e:
            self._using_default_params = True
            logger.warning(f"Could not load optimized params: {e} — running on defaults")

        # Sync capital with OANDA account
        try:
            from data.oanda import get_account_summary
            acct = get_account_summary()
            self.risk_mgr.capital = float(acct["balance"])
            logger.info(f"OANDA balance: £{self.risk_mgr.capital:,.2f}")
        except Exception as e:
            logger.warning(f"Could not sync OANDA balance: {e}")

        # Reconcile open positions with OANDA so restarts don't orphan state
        try:
            self._reconcile_open_positions()
        except Exception as e:
            logger.warning(f"Position reconciliation failed: {e}")

        logger.info("Trading pipeline initialized")

    def _reconcile_open_positions(self):
        """On startup, rebuild in-memory open positions from OANDA + DB.

        - For every OANDA open trade, find its matching DB PositionRecord via
          oanda_trade_id, reconstruct a Position, and register it with the
          risk manager + executor.
        - DB rows marked open but missing on OANDA are assumed externally
          closed and are marked closed (no fill price available — logged).
        - OANDA trades missing from the DB are logged as orphans; they are
          not added to internal state.
        """
        import os
        if not os.getenv("OANDA_API_KEY"):
            return

        from data.oanda import get_open_trades
        from storage.database import PositionRecord, SignalRecord, get_session
        from data.models import Signal, Position, Direction, SignalType, TradeStatus

        oanda_trades = {str(t.get("id")): t for t in get_open_trades()}

        session = get_session()
        try:
            open_rows = session.query(PositionRecord).filter_by(status="open").all()

            reconciled = 0
            for rec in open_rows:
                if not rec.oanda_trade_id or rec.oanda_trade_id not in oanda_trades:
                    logger.warning(
                        f"DB position #{rec.id} ({rec.pair}) marked open but not "
                        f"found on OANDA — marking closed"
                    )
                    rec.status = "closed"
                    continue

                sig_rec = (
                    session.query(SignalRecord).filter_by(id=rec.signal_id).first()
                    if rec.signal_id else None
                )
                try:
                    signal = Signal(
                        timestamp=sig_rec.timestamp if sig_rec else rec.opened_at,
                        pair=rec.pair,
                        direction=Direction(rec.direction),
                        signal_type=SignalType(rec.signal_type or "bos_continuation"),
                        entry_price=rec.entry_price,
                        stop_loss=rec.stop_loss or rec.entry_price,
                        take_profit=rec.take_profit or rec.entry_price,
                        confluence_score=rec.confluence_score or 0.0,
                        entry_timeframe=sig_rec.entry_timeframe if sig_rec else "15m",
                        trigger_timeframe=sig_rec.trigger_timeframe if sig_rec else "4h",
                    )
                except Exception as e:
                    logger.warning(f"Could not rebuild Signal for position #{rec.id}: {e}")
                    continue

                position = Position(
                    id=rec.id,
                    signal=signal,
                    status=TradeStatus.OPEN,
                    entry_price=rec.entry_price,
                    size=rec.size,
                    risk_amount=rec.risk_amount,
                    opened_at=rec.opened_at,
                )
                self.risk_mgr.open_positions.append(position)
                self.executor.oanda_trade_map[rec.id] = rec.oanda_trade_id
                reconciled += 1

            session.commit()

            db_trade_ids = {
                r.oanda_trade_id for r in open_rows if r.oanda_trade_id
            }
            orphans = [tid for tid in oanda_trades if tid not in db_trade_ids]
            for tid in orphans:
                logger.warning(
                    f"OANDA trade {tid} open but not tracked in DB — leaving untouched"
                )

            logger.info(
                f"Reconciled {reconciled} open position(s) from OANDA "
                f"({len(orphans)} orphan OANDA trade(s))"
            )
        finally:
            session.close()

    def run_once(self) -> dict:
        """Run a single cycle of the pipeline for all active pairs.

        Returns summary of what happened.
        """
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "signals": 0,
            "positions_opened": 0,
            "positions_closed": 0,
        }

        for pair_name in self.pairs:
            asset = get_asset(pair_name)
            pair_summary = self._run_pair(asset)
            summary["signals"] += pair_summary["signals"]
            summary["positions_opened"] += pair_summary["positions_opened"]

        self._write_equity_snapshot()
        self._maybe_run_consolidation()

        return summary

    def _run_pair(self, asset) -> dict:
        """Run analysis and trading for a single pair."""
        pair_summary = {"signals": 0, "positions_opened": 0}
        pair = asset.yahoo_ticker

        # Create CycleContext for this pair
        pair_positions = [
            p for p in self.risk_mgr.open_positions
            if p.signal.pair == pair
        ]
        cycle = CycleContext(
            pair=pair,
            equity=self.risk_mgr.capital,
            daily_pnl=getattr(self.risk_mgr, "daily_pnl", 0.0),
            open_positions_count=len(pair_positions),
            open_position_ids=[p.id for p in pair_positions if p.id],
        )

        # 1. Fetch/load data
        candles = self._get_candles(pair)
        if not candles:
            logger.warning(f"No candle data available for {asset.name}")
            return pair_summary

        # 2. Analyze
        analysis_result = self.analyzer.process({"candles": candles, "cycle": cycle})
        context = analysis_result.get("context")
        if not context:
            return pair_summary

        # Populate cycle from analysis
        cycle.regime = context.regime
        cycle.overall_bias = context.overall_bias
        cycle.bias_strength = context.bias_strength
        # ADX as regime confidence (prefer 4H, fallback to any available TF)
        for tf_key in ["4h", "1d", "1h"]:
            tf_analysis = context.analyses.get(tf_key)
            if tf_analysis and tf_analysis.adx > 0:
                cycle.regime_confidence = tf_analysis.adx
                break

        # 3. Check existing positions against current live price
        live = get_live_price(pair=pair)
        current_price = live.get("mid", 0) if live else 0

        if current_price == 0:
            for tf in ["15m", "1h", "4h", "1d"]:
                if tf in context.analyses:
                    current_price = context.analyses[tf].current_price
                    break

        if current_price > 0:
            self.risk_mgr.update_positions(current_price, pair=pair)
            self._last_prices[pair] = current_price

        # 4. Generate signals
        signal_result = self.signal_gen.process({"context": context, "cycle": cycle})
        signals = signal_result.get("signals", [])

        # Track generated signals in cycle
        for s in signals:
            cycle.signals_generated.append(CycleSignalRecord(signal=s))

        # 4b. Apply trading rules
        signals = self._filter_signals(signals, current_price, cycle)
        pair_summary["signals"] = len(signals)

        # 5. Risk check and size
        if signals:
            risk_result = self.risk_mgr.process({"signals": signals, "cycle": cycle})
            positions = risk_result.get("positions", [])
            pair_summary["positions_opened"] = len(positions)

            # Track executed signals
            for p in positions:
                cycle.signals_executed.append(
                    CycleSignalRecord(signal=p.signal, executed=True)
                )

            # 6. Execute
            if positions:
                self.executor.process({"positions": positions, "cycle": cycle})

        # Audit: log cycle summary
        log_audit("pipeline", "cycle_complete", pair=pair, details={
            "signals_generated": len(cycle.signals_generated),
            "signals_filtered": len(cycle.signals_filtered),
            "signals_executed": len(cycle.signals_executed),
            "regime": cycle.regime.value if cycle.regime else None,
            "bias": cycle.overall_bias.value if cycle.overall_bias else None,
            "equity": cycle.equity,
        })

        return pair_summary

    def run_loop(self, interval_seconds: int = 60):
        """Run the pipeline in a loop."""
        self._running = True
        logger.info(f"Starting trading loop (interval={interval_seconds}s)")
        logger.info(f"Capital: £{self.risk_mgr.capital:.2f}")

        try:
            while self._running:
                try:
                    summary = self.run_once()
                    logger.info(
                        f"Cycle complete: {summary['signals']} signals, "
                        f"{summary['positions_opened']} opened | "
                        f"Capital: £{self.risk_mgr.capital:.2f}"
                    )
                except Exception as e:
                    logger.error(f"Pipeline error: {e}", exc_info=True)

                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Pipeline stopped by user")
        finally:
            self._running = False
            self.teardown()

    def _filter_signals(self, signals: list, current_price: float,
                        cycle: CycleContext | None = None) -> list:
        """Apply evidence-based trading rules before risk processing."""
        if not signals:
            return signals

        now = datetime.utcnow()

        # Block signals during low-quality hours
        if BLOCK_HOURS_UTC and now.hour in BLOCK_HOURS_UTC:
            logger.info(f"Skipping {len(signals)} signal(s) — blocked hour {now.hour:02d}:00 UTC")
            if cycle:
                for s in signals:
                    cycle.signals_filtered.append(
                        CycleSignalRecord(signal=s, filtered_reason="blocked_hour")
                    )
            return []

        filtered = []
        for signal in signals:
            # No-overlap: skip if a position in the same direction is already open
            if NO_OVERLAP_ENTRIES:
                same_dir_open = any(
                    p.signal.direction == signal.direction
                    and p.signal.pair == signal.pair
                    for p in self.risk_mgr.open_positions
                )
                if same_dir_open:
                    logger.debug(f"Skipping {signal.direction.value} signal — position already open in same direction")
                    if cycle:
                        cycle.signals_filtered.append(
                            CycleSignalRecord(signal=signal, filtered_reason="no_overlap")
                        )
                    continue
            filtered.append(signal)

        skipped = len(signals) - len(filtered)
        if skipped:
            logger.info(f"Trading rules filtered {skipped}/{len(signals)} signal(s)")
        return filtered

    def _write_equity_snapshot(self):
        """Write end-of-cycle equity snapshot to main DB."""
        from storage.database import EquitySnapshotRecord, get_session

        unrealized = 0.0
        for pos in self.risk_mgr.open_positions:
            price = self._last_prices.get(pos.signal.pair, pos.entry_price)
            if pos.signal.direction == Direction.LONG:
                unrealized += (price - pos.entry_price) * pos.size
            else:
                unrealized += (pos.entry_price - price) * pos.size

        equity = self.risk_mgr.capital + unrealized
        self._peak_equity = max(equity, self._peak_equity)
        dd_pct = ((self._peak_equity - equity) / self._peak_equity
                  if self._peak_equity > 0 else 0.0)

        session = get_session()
        try:
            session.add(EquitySnapshotRecord(
                timestamp=datetime.utcnow(),
                equity=round(equity, 2),
                cash=round(self.risk_mgr.capital, 2),
                unrealized_pnl=round(unrealized, 2),
                daily_return=round(getattr(self.risk_mgr, "daily_pnl", 0.0), 2),
                drawdown_pct=round(dd_pct, 4),
                open_positions=len(self.risk_mgr.open_positions),
            ))
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning(f"Failed to write equity snapshot: {e}")
        finally:
            session.close()

    def _maybe_run_consolidation(self):
        """Run periodic consolidation tasks. Called at end of run_once()."""
        now = datetime.utcnow()

        # Weekly (Monday midnight UTC): update rolling 90-day summary in strategic memory
        if now.weekday() == 0 and now.hour == 0:
            self._run_weekly_consolidation()

        # Monthly (1st of month, midnight UTC): feature importance update
        if now.day == 1 and now.hour == 0:
            self._run_monthly_feature_importance()

    def _run_weekly_consolidation(self):
        """Compute rolling 90-day performance and store in strategic memory."""
        try:
            from storage.database import query_recent_closed_positions
            from research import history

            positions = query_recent_closed_positions(n=500)
            if not positions:
                return

            # Filter to last 90 days
            cutoff = datetime.utcnow().replace(tzinfo=None)
            cutoff = cutoff.replace(day=max(1, cutoff.day), month=cutoff.month)
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=90)
            recent = [p for p in positions
                      if p.closed_at and p.closed_at >= cutoff]
            if not recent:
                return

            total = len(recent)
            wins = sum(1 for p in recent if p.pnl and p.pnl > 0)
            win_pips = sum(p.pnl_pips for p in recent if p.pnl and p.pnl > 0)
            loss_pips = sum(abs(p.pnl_pips) for p in recent if p.pnl and p.pnl <= 0)

            history_data = history.load()
            for pair in set(p.pair for p in recent if p.pair):
                pair_positions = [p for p in recent if p.pair == pair]
                p_total = len(pair_positions)
                p_wins = sum(1 for p in pair_positions if p.pnl and p.pnl > 0)
                p_win_pips = sum(p.pnl_pips for p in pair_positions if p.pnl and p.pnl > 0)
                p_loss_pips = sum(abs(p.pnl_pips) for p in pair_positions
                                  if p.pnl and p.pnl <= 0)

                history.update_rolling_90d_summary(history_data, pair, {
                    "total_trades": p_total,
                    "win_rate": p_wins / p_total if p_total else 0,
                    "profit_factor": p_win_pips / p_loss_pips if p_loss_pips > 0 else 0,
                    "expectancy_pips": round(
                        (p_win_pips - p_loss_pips) / p_total if p_total else 0, 1
                    ),
                })

            history.save(history_data)
            logger.info(f"Weekly consolidation: updated 90-day summary ({total} trades)")
        except Exception as e:
            logger.warning(f"Weekly consolidation failed: {e}")

    def _run_monthly_feature_importance(self):
        """Compute feature importance from trade history and store in strategic memory."""
        try:
            from analysis.feature_importance import compute_all_pairs
            from research import history

            results = compute_all_pairs(lookback_days=180)
            if not results:
                logger.info("Monthly feature importance: no data to compute")
                return

            history_data = history.load()
            for pair_key, importance in results.items():
                if pair_key == "_combined":
                    continue
                history.update_feature_importance(history_data, pair_key, importance)

            history.save(history_data)
            pairs_updated = [k for k in results if k != "_combined"]
            logger.info(
                f"Monthly feature importance: updated {len(pairs_updated)} pair(s) "
                f"({', '.join(pairs_updated)})"
            )
        except Exception as e:
            logger.warning(f"Monthly feature importance failed: {e}")

    def _get_candles(self, pair: str) -> dict[str, list[Candle]]:
        """Get candles for all timeframes for a specific pair."""
        all_candles = {}
        now = datetime.utcnow()

        for tf in TIMEFRAMES:
            # Check if we need to re-fetch (keyed by pair+tf)
            fetch_key = f"{pair}:{tf}"
            last = self._last_fetch.get(fetch_key)
            interval_min = TIMEFRAME_MINUTES.get(tf, 60)

            needs_fetch = (
                last is None or
                (now - last).total_seconds() > interval_min * 60
            )

            if needs_fetch:
                try:
                    candles = fetch_candles(pair=pair, timeframe=tf)
                    if candles:
                        save_candles(candles, pair)
                        all_candles[tf] = candles
                        self._last_fetch[fetch_key] = now
                    else:
                        all_candles[tf] = load_candles(pair=pair, timeframe=tf)
                except Exception as e:
                    logger.warning(f"Fetch failed for {pair} {tf}: {e}, using cache")
                    all_candles[tf] = load_candles(pair=pair, timeframe=tf)
            else:
                all_candles[tf] = load_candles(pair=pair, timeframe=tf)

        return all_candles

    def _on_position_closed(self, position):
        """Handle closed position - feed to learner."""
        self.learner.process({"closed_position": position})
        self.executor.record_close(position)

    def teardown(self):
        """Shutdown all agents."""
        for agent in [self.analyzer, self.signal_gen, self.risk_mgr,
                      self.executor, self.learner]:
            agent.teardown()
        logger.info("Trading pipeline shut down")

    def get_status(self) -> dict:
        """Get current pipeline status."""
        return {
            "running": self._running,
            "using_default_params": self._using_default_params,
            "risk": self.risk_mgr.get_stats(),
            "learning": self.learner.get_learning_summary(),
            "last_context": (
                self.analyzer.last_context.model_dump()
                if self.analyzer.last_context else None
            ),
            "active_signals": [
                s.model_dump() for s in self.signal_gen.active_signals
            ],
        }
