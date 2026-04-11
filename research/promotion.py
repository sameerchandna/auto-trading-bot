"""Apply approved/rejected research decisions back into the system.

Approval handler (notifications/approval_handler.py) only moves entries
between buckets in approvals.json. The actual *application* of an approval
is the responsibility of the originating agent — that's this module.

apply_decisions() is called at the start of every research_job run:
  - For each approved research entry not yet processed:
      * Write its params to config/optimized_params.json
      * Mark it processed=true in approvals.json
      * Sync rolling_baseline in test_history.json
  - For each rejected research entry not yet processed:
      * Add params hash to test_history.json blacklist (30-day cooldown)
      * Mark it processed=true in approvals.json
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from config.params import save_strategy_params
from research import history

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent
APPROVALS_FILE = REPO_ROOT / "reports" / "approvals.json"


def _load_approvals() -> dict:
    if not APPROVALS_FILE.exists():
        return {"pending": [], "approved": [], "rejected": []}
    return json.loads(APPROVALS_FILE.read_text())


def _save_approvals(data: dict) -> None:
    APPROVALS_FILE.write_text(json.dumps(data, indent=2))


def _strip_for_save(params: dict) -> dict:
    """Extract the fields that save_strategy_params() persists.

    sl_method lives on BacktestConfig and is not stored in
    optimized_params.json — drop it before save.
    """
    return {
        "weights": params["weights"],
        "threshold": params["threshold"],
        "sl_multiplier": params["sl_multiplier"],
        "tp_risk_reward": params["tp_risk_reward"],
        "swing_lookback": params["swing_lookback"],
        "regime_filter_enabled": params.get("regime_filter_enabled", False),
        "regime_adx_threshold": params.get("regime_adx_threshold", 25.0),
        "signal_model_enabled": params.get("signal_model_enabled", False),
        "signal_model_min_confidence": params.get("signal_model_min_confidence", 0.5),
        "news_filter_enabled": params.get("news_filter_enabled", False),
        "news_block_before_mins": params.get("news_block_before_mins", 30),
        "news_block_after_mins": params.get("news_block_after_mins", 15),
        "regime_params_enabled": params.get("regime_params_enabled", False),
        "atr_volatility_threshold": params.get("atr_volatility_threshold", 80.0),
        "regime_params": params.get("regime_params", {"trending": {}, "ranging": {}, "volatile": {}}),
    }


def apply_decisions(history_data: dict | None = None) -> dict:
    """Process any pending approve/reject decisions for research entries.

    Returns a summary dict with counts.
    """
    if history_data is None:
        history_data = history.load()

    approvals = _load_approvals()
    summary = {"applied": 0, "blacklisted": 0, "skipped": 0}

    for entry in approvals.get("approved", []):
        if entry.get("kind") not in ("research", "learner") or entry.get("processed"):
            continue
        params = entry.get("params")
        if not params:
            logger.warning(f"approved research entry {entry.get('id')} missing params, skipping")
            summary["skipped"] += 1
            continue
        try:
            save_strategy_params(_strip_for_save(params))
            entry["processed"] = True
            entry["applied_at"] = history._utcnow_iso()
            summary["applied"] += 1
            logger.info(f"Applied approved research entry {entry.get('id')} to optimized_params.json")
        except Exception as exc:
            logger.error(f"Failed to apply {entry.get('id')}: {exc}")
            summary["skipped"] += 1

    for entry in approvals.get("rejected", []):
        if entry.get("kind") not in ("research", "learner") or entry.get("processed"):
            continue
        h = entry.get("params_hash")
        if not h:
            entry["processed"] = True
            continue
        history.add_to_blacklist(history_data, h, f"user rejected {entry.get('id')}")
        entry["processed"] = True
        entry["blacklisted_at"] = history._utcnow_iso()
        summary["blacklisted"] += 1

    _save_approvals(approvals)
    if summary["applied"] > 0:
        # Refresh rolling baseline from the new optimized_params.json
        history.sync_rolling_baseline(history_data)
        # Auto-sync the PineScript so BT and TV stay in lockstep
        try:
            from tradingview.generate_pine import generate as generate_pine
            generate_pine()
            logger.info("PineScript regenerated after promotion")
        except Exception as exc:
            logger.warning(f"Pine sync failed after promotion: {exc}")

    return summary


def auto_apply_promotion(entry: dict, history_data: dict) -> bool:
    """Directly apply an AUTO_PROMOTED candidate, bypassing the approval queue.

    Stores the previous params for rollback, applies the new params, and
    syncs the rolling baseline.  Returns True on success.
    """
    params = entry.get("params")
    if not params:
        logger.warning(f"auto_apply: entry {entry.get('id')} missing params")
        return False

    # Store previous params for rollback
    _store_rollback_snapshot(history_data)

    try:
        save_strategy_params(_strip_for_save(params))
        logger.info(
            f"AUTO-PROMOTED {entry.get('id')} ({entry.get('mutation', '?')}) "
            f"applied to optimized_params.json"
        )
    except Exception as exc:
        logger.error(f"auto_apply failed for {entry.get('id')}: {exc}")
        return False

    # Refresh rolling baseline
    history.sync_rolling_baseline(history_data)

    # Regenerate PineScript
    try:
        from tradingview.generate_pine import generate as generate_pine
        generate_pine()
        logger.info("PineScript regenerated after auto-promotion")
    except Exception as exc:
        logger.warning(f"Pine sync failed after auto-promotion: {exc}")

    return True


def _store_rollback_snapshot(history_data: dict) -> None:
    """Save current optimized_params.json as a rollback point."""
    from config.params import load_strategy_params
    current = load_strategy_params()
    history_data.setdefault("auto_promote_rollback", {})
    history_data["auto_promote_rollback"] = {
        "params": current,
        "params_hash": history.hash_params(current),
        "saved_at": history._utcnow_iso(),
    }


def rollback_last_auto_promotion(history_data: dict | None = None) -> bool:
    """Revert to the params saved before the last auto-promotion.

    Returns True if rollback was applied, False if no snapshot exists.
    """
    if history_data is None:
        history_data = history.load()

    snapshot = history_data.get("auto_promote_rollback")
    if not snapshot or not snapshot.get("params"):
        logger.warning("No rollback snapshot found — nothing to revert")
        return False

    try:
        save_strategy_params(_strip_for_save(snapshot["params"]))
        logger.info(
            f"Rolled back to params from {snapshot.get('saved_at')} "
            f"(hash {snapshot.get('params_hash')})"
        )
        history_data["auto_promote_rollback"]["rolled_back_at"] = history._utcnow_iso()
        history.sync_rolling_baseline(history_data)
        history.save(history_data)
        return True
    except Exception as exc:
        logger.error(f"Rollback failed: {exc}")
        return False


def push_promotion(entry: dict, approval_id: str) -> None:
    """Add a PROMOTED_CANDIDATE history entry to approvals.json `pending`.

    The entry shape matches what notifications/report_builder expects:
        {id, kind, title, details, params, params_hash}
    """
    approvals = _load_approvals()
    agg = entry.get("aggregate") or {}
    oos = entry.get("oos") or {}
    title = f"Promote {entry.get('mutation', 'param change')}?"
    lines = [
        f"  Hash: {entry['params_hash']}",
        f"  Walk-forward median: PF={agg.get('median_profit_factor', 0):.2f} "
        f"WR={agg.get('median_win_rate', 0):.1%} "
        f"DD={agg.get('median_max_drawdown_pct', 0):.1%}",
    ]
    if oos:
        lines.append(
            f"  OOS: trades={oos.get('trades', 0)} "
            f"PF={oos.get('profit_factor', 0):.2f} "
            f"WR={oos.get('win_rate', 0):.1%}"
        )
    delta = entry.get("delta_vs_anchor") or {}
    if delta:
        lines.append(
            f"  vs anchor: PF {delta.get('profit_factor_pct', 0):+.1%}, "
            f"WR {delta.get('win_rate_pp', 0):+.3f}"
        )

    approvals.setdefault("pending", []).append({
        "id": approval_id,
        "kind": "research",
        "title": title,
        "details": "\n".join(lines),
        "params": entry["params"],
        "params_hash": entry["params_hash"],
        "test_id": entry["id"],
    })
    _save_approvals(approvals)


def push_learner_proposal(proposal: dict) -> None:
    """Add a learner weight-adjustment proposal to approvals.json `pending`.

    proposal keys: proposal_id, params, params_hash, before_weights, after_weights,
                   metrics, weight_deltas
    """
    approvals = _load_approvals()
    metrics = proposal.get("metrics", {})
    deltas = proposal.get("weight_deltas", {})

    delta_lines = [f"    {k}: {v:+.4f}" for k, v in deltas.items() if abs(v) > 1e-6]
    lines = [
        f"  Rolling {metrics.get('total_trades', 0)} trades: "
        f"WR={metrics.get('win_rate', 0):.1%} PF={metrics.get('profit_factor', 0):.2f} "
        f"DD={metrics.get('max_dd_pct', 0):.1%}",
        "  Weight deltas:",
        *delta_lines,
    ]

    approvals.setdefault("pending", []).append({
        "id": proposal["proposal_id"],
        "kind": "learner",
        "title": f"Learner weight adjustment (trade #{proposal.get('trade_count', '?')})",
        "details": "\n".join(lines),
        "params": proposal["params"],
        "params_hash": proposal["params_hash"],
    })
    _save_approvals(approvals)
