"""Feature importance tracking — empirical factor analysis from closed trades.

Computes per-factor importance from historical trade outcomes:
  - Point-biserial correlation: each factor score vs win/loss
  - Mean factor score for wins vs losses (effect size)
  - Optional SHAP values when a trained signal model exists

Results stored in strategic memory (test_history.json) per asset.
Monthly consolidation hook + CLI: `python main.py feature-importance`
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Factor columns stored in SignalRecord (must match database.py)
FACTOR_COLS = [
    "score_htf_bias", "score_bos", "score_wave_position",
    "score_liquidity_sweep", "score_sr_reaction", "score_wave_ending",
    "score_catalyst",
]

# Human-readable names (match confluence weight keys)
FACTOR_NAMES = {
    "score_htf_bias": "htf_bias",
    "score_bos": "bos",
    "score_wave_position": "wave_position",
    "score_liquidity_sweep": "liquidity_sweep",
    "score_sr_reaction": "sr_reaction",
    "score_wave_ending": "wave_ending",
    "score_catalyst": "catalyst",
}

# Extra context features (not confluence weights, but useful for insight)
CONTEXT_COLS = ["adx", "atr"]

MIN_TRADES = 30  # Minimum closed trades to compute meaningful importance


def _point_biserial(x: np.ndarray, y_binary: np.ndarray) -> float:
    """Point-biserial correlation between continuous x and binary y.

    Equivalent to Pearson r when one variable is dichotomous.
    Returns 0.0 on degenerate inputs (no variance, too few samples).
    """
    if len(x) < 3 or np.std(x) == 0 or np.std(y_binary) == 0:
        return 0.0
    n = len(x)
    n1 = y_binary.sum()
    n0 = n - n1
    if n0 == 0 or n1 == 0:
        return 0.0
    m1 = x[y_binary == 1].mean()
    m0 = x[y_binary == 0].mean()
    s = np.std(x, ddof=1)
    rpb = (m1 - m0) / s * math.sqrt(n0 * n1 / (n * n))
    return round(float(rpb), 4)


def compute_feature_importance(
    pair: str | None = None,
    lookback_days: int = 180,
) -> dict[str, Any]:
    """Compute feature importance from closed trades in the database.

    Joins PositionRecord (outcome) with SignalRecord (feature vector)
    via signal_id. Computes:
      - point_biserial: correlation of each factor with win/loss
      - mean_win / mean_loss: average factor score for wins vs losses
      - effect_size: mean_win - mean_loss (positive = factor helps wins)
      - trade_count: number of trades with non-null factor data

    Returns dict keyed by factor name (e.g. "htf_bias"), plus metadata.
    """
    from storage.database import PositionRecord, SignalRecord, get_session

    session = get_session()
    try:
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)

        query = (
            session.query(PositionRecord, SignalRecord)
            .join(SignalRecord, PositionRecord.signal_id == SignalRecord.id)
            .filter(PositionRecord.status == "closed")
            .filter(PositionRecord.closed_at >= cutoff)
        )
        if pair:
            query = query.filter(PositionRecord.pair == pair)

        rows = query.all()
    finally:
        session.close()

    if len(rows) < MIN_TRADES:
        logger.info(
            f"Feature importance: only {len(rows)} trades "
            f"(need {MIN_TRADES}) for {pair or 'all pairs'} — skipping"
        )
        return {"status": "insufficient_data", "trade_count": len(rows)}

    # Build arrays
    outcomes = np.array([1.0 if pos.pnl_pips > 0 else 0.0 for pos, _ in rows])

    all_cols = FACTOR_COLS + CONTEXT_COLS
    importance: dict[str, dict] = {}

    for col in all_cols:
        values = []
        valid_outcomes = []
        for pos, sig in rows:
            val = getattr(sig, col, None)
            if val is not None:
                values.append(val)
                valid_outcomes.append(1.0 if pos.pnl_pips > 0 else 0.0)

        if len(values) < MIN_TRADES:
            continue

        x = np.array(values)
        y = np.array(valid_outcomes)

        wins_mask = y == 1
        losses_mask = y == 0
        mean_win = float(x[wins_mask].mean()) if wins_mask.any() else 0.0
        mean_loss = float(x[losses_mask].mean()) if losses_mask.any() else 0.0

        name = FACTOR_NAMES.get(col, col)
        importance[name] = {
            "point_biserial": _point_biserial(x, y),
            "mean_win": round(mean_win, 4),
            "mean_loss": round(mean_loss, 4),
            "effect_size": round(mean_win - mean_loss, 4),
            "trade_count": len(values),
        }

    # Optional: SHAP values from trained model
    shap_importance = _compute_shap_importance(rows)
    if shap_importance:
        for name, shap_val in shap_importance.items():
            if name in importance:
                importance[name]["shap_mean_abs"] = shap_val

    # Rank factors by absolute point-biserial correlation
    ranked = sorted(
        importance.items(),
        key=lambda kv: abs(kv[1].get("point_biserial", 0)),
        reverse=True,
    )

    return {
        "status": "ok",
        "pair": pair or "all",
        "lookback_days": lookback_days,
        "trade_count": len(rows),
        "win_rate": round(float(outcomes.mean()), 4),
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "factors": dict(ranked),
    }


def _compute_shap_importance(rows: list) -> dict[str, float] | None:
    """Compute mean |SHAP| values if a trained signal model exists.

    Uses the model's feature set (12 features from signal_model.py),
    not just the 7 confluence factors. Returns None if model unavailable.
    """
    try:
        from analysis.signal_model import MODEL_PATH, FEATURE_COLS, load_model
        if not MODEL_PATH.exists():
            return None

        model = load_model()
        if model is None:
            return None

        # Build feature matrix from signal rationale (same extraction as signal_model)
        X_rows = []
        for pos, sig in rows:
            try:
                rationale = {}
                if sig.rationale:
                    import json
                    rationale = json.loads(sig.rationale) if isinstance(sig.rationale, str) else sig.rationale
                scores = rationale.get("scores", {})
                if not scores:
                    continue

                hour = sig.timestamp.hour
                features = [
                    scores.get("htf_bias", 0.0),
                    scores.get("bos", 0.0),
                    scores.get("wave_position", 0.0),
                    scores.get("liquidity_sweep", 0.0),
                    scores.get("sr_reaction", 0.0),
                    scores.get("wave_ending", 0.0),
                    pos.confluence_score or 0.0,
                    rationale.get("bias_strength", 0.0),
                    rationale.get("adx", 0.0),
                    1.0 if pos.direction == "LONG" else 0.0,
                    np.sin(2 * np.pi * hour / 24),
                    np.cos(2 * np.pi * hour / 24),
                ]
                X_rows.append(features)
            except Exception:
                continue

        if len(X_rows) < MIN_TRADES:
            return None

        X = np.array(X_rows, dtype=np.float64)

        # Use model coefficients as proxy for SHAP (logistic regression is linear)
        # For linear models, SHAP ≈ coef * (x - mean(x)), so mean |SHAP| ≈ |coef| * std(x)
        scaler = model.named_steps["scaler"]
        clf = model.named_steps["clf"]
        coefs = clf.coef_[0]

        X_scaled = scaler.transform(X)
        # mean |SHAP| per feature = mean(|coef_i * x_scaled_i|)
        shap_approx = np.mean(np.abs(X_scaled * coefs), axis=0)

        return {
            name: round(float(val), 4)
            for name, val in zip(FEATURE_COLS, shap_approx)
        }

    except Exception as e:
        logger.debug(f"SHAP computation skipped: {e}")
        return None


def compute_all_pairs(lookback_days: int = 180) -> dict[str, dict]:
    """Compute feature importance for each active asset individually + combined."""
    from config.assets import ACTIVE_ASSETS

    results = {}

    # Combined (all pairs)
    combined = compute_feature_importance(pair=None, lookback_days=lookback_days)
    if combined.get("status") == "ok":
        results["_combined"] = combined

    # Per pair
    for pair_name in ACTIVE_ASSETS:
        result = compute_feature_importance(pair=pair_name, lookback_days=lookback_days)
        if result.get("status") == "ok":
            results[pair_name] = result

    return results
