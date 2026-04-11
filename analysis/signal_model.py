"""Learned signal quality model — predicts win probability from factor scores.

Replaces/augments the linear weighted confluence scorer with a classifier
trained on historical backtest data (features → win/loss outcome).

Usage:
    python main.py train-model          # generate data + train + evaluate
    python main.py train-model --retrain  # retrain on latest data

Config (optimized_params.json):
    signal_model_enabled: bool   — gate signals through model prediction
    signal_model_min_confidence: float — minimum P(win) to allow signal (0.0–1.0)
"""
from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np

from config.settings import PROJECT_ROOT

logger = logging.getLogger(__name__)

MODEL_DIR = PROJECT_ROOT / "models"
MODEL_PATH = MODEL_DIR / "signal_model.pkl"
META_PATH = MODEL_DIR / "signal_model_meta.json"

# Feature columns in fixed order — must match training and prediction
FEATURE_COLS = [
    "htf_bias", "bos", "wave_position", "liquidity_sweep",
    "sr_reaction", "wave_ending", "confluence_score",
    "bias_strength", "adx", "direction_long",
    "hour_sin", "hour_cos",
]


def _extract_features(signal) -> list[float] | None:
    """Extract feature vector from a Signal object.

    Returns None if required data is missing (pre-upgrade signals).
    """
    scores = signal.rationale.get("scores")
    if not scores:
        return None

    hour = signal.timestamp.hour
    from data.models import Direction
    return [
        scores.get("htf_bias", 0.0),
        scores.get("bos", 0.0),
        scores.get("wave_position", 0.0),
        scores.get("liquidity_sweep", 0.0),
        scores.get("sr_reaction", 0.0),
        scores.get("wave_ending", 0.0),
        signal.confluence_score,
        signal.rationale.get("bias_strength", 0.0),
        signal.rationale.get("adx", 0.0),
        1.0 if signal.direction == Direction.LONG else 0.0,
        np.sin(2 * np.pi * hour / 24),
        np.cos(2 * np.pi * hour / 24),
    ]


def generate_training_data(
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    pair: str = "EURUSD",
) -> tuple[np.ndarray, np.ndarray]:
    """Run a backtest and extract (features, labels) for every closed trade.

    Labels: 1 = win (pnl_pips > 0), 0 = loss.
    Returns (X, y) numpy arrays.
    """
    from backtest.engine import BacktestEngine
    from backtest.config import BASELINE
    from config.params import load_strategy_params

    if start_date is None:
        start_date = datetime(2023, 1, 1)
    if end_date is None:
        end_date = datetime(2026, 4, 1)

    params = load_strategy_params()
    # Disable model filter during training data generation
    params["signal_model_enabled"] = False

    engine = BacktestEngine(
        start_date=start_date,
        end_date=end_date,
        config=BASELINE,
        pair=pair,
        params_override=params,
    )
    engine.run()

    closed = engine.risk_mgr.closed_positions
    logger.info(f"Training data: {len(closed)} closed trades from backtest")

    X_rows = []
    y_rows = []
    skipped = 0

    for pos in closed:
        features = _extract_features(pos.signal)
        if features is None:
            skipped += 1
            continue
        X_rows.append(features)
        y_rows.append(1.0 if pos.pnl_pips > 0 else 0.0)

    if skipped:
        logger.warning(f"Skipped {skipped} trades with missing feature data")

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_rows, dtype=np.float64)
    logger.info(
        f"Dataset: {len(X)} samples, {y.sum():.0f} wins ({y.mean():.1%} WR), "
        f"{len(FEATURE_COLS)} features"
    )
    return X, y


def train_model(
    X: np.ndarray,
    y: np.ndarray,
    n_splits: int = 5,
) -> dict:
    """Train a logistic regression model with walk-forward cross-validation.

    Walk-forward: each fold trains on data *before* the test window,
    never on future data. This prevents lookahead bias.

    Returns metadata dict with CV scores and model path.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import accuracy_score, roc_auc_score, brier_score_loss

    if len(X) < 100:
        raise ValueError(f"Need at least 100 samples, got {len(X)}")

    # Walk-forward splits (time-ordered, no shuffling)
    fold_size = len(X) // (n_splits + 1)
    cv_results = []

    for i in range(n_splits):
        train_end = fold_size * (i + 2)
        test_start = train_end
        test_end = min(train_end + fold_size, len(X))

        if test_end <= test_start:
            break

        X_train, y_train = X[:train_end], y[:train_end]
        X_test, y_test = X[test_start:test_end], y[test_start:test_end]

        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                C=1.0, max_iter=1000, class_weight="balanced",
            )),
        ])
        pipe.fit(X_train, y_train)

        y_pred = pipe.predict(X_test)
        y_prob = pipe.predict_proba(X_test)[:, 1]

        fold_result = {
            "fold": i + 1,
            "train_size": len(X_train),
            "test_size": len(X_test),
            "accuracy": round(accuracy_score(y_test, y_pred), 3),
            "auc": round(roc_auc_score(y_test, y_prob), 3) if len(np.unique(y_test)) > 1 else 0.0,
            "brier": round(brier_score_loss(y_test, y_prob), 4),
            "test_wr": round(y_test.mean(), 3),
            "pred_wr": round(y_pred.mean(), 3),
        }
        cv_results.append(fold_result)
        logger.info(
            f"Fold {fold_result['fold']}: acc={fold_result['accuracy']:.3f} "
            f"auc={fold_result['auc']:.3f} brier={fold_result['brier']:.4f}"
        )

    # Train final model on all data
    final_pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            C=1.0, max_iter=1000, class_weight="balanced",
        )),
    ])
    final_pipe.fit(X, y)

    # Feature importance (logistic regression coefficients)
    coefs = final_pipe.named_steps["clf"].coef_[0]
    feature_importance = {
        name: round(float(coef), 4)
        for name, coef in zip(FEATURE_COLS, coefs)
    }

    # Save model
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(final_pipe, f)

    meta = {
        "trained_at": datetime.utcnow().isoformat(),
        "samples": len(X),
        "win_rate": round(float(y.mean()), 3),
        "features": FEATURE_COLS,
        "feature_importance": feature_importance,
        "cv_results": cv_results,
        "mean_auc": round(np.mean([r["auc"] for r in cv_results]), 3),
        "mean_accuracy": round(np.mean([r["accuracy"] for r in cv_results]), 3),
        "mean_brier": round(np.mean([r["brier"] for r in cv_results]), 4),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    logger.info(
        f"Model saved: {MODEL_PATH} | "
        f"Mean AUC={meta['mean_auc']:.3f}, Accuracy={meta['mean_accuracy']:.3f}"
    )
    return meta


# --- Runtime prediction (loaded once, used per signal) ---

_cached_model = None


def load_model():
    """Load the trained model from disk. Returns None if not found."""
    global _cached_model
    if _cached_model is not None:
        return _cached_model

    if not MODEL_PATH.exists():
        logger.debug("No trained signal model found")
        return None

    with open(MODEL_PATH, "rb") as f:
        _cached_model = pickle.load(f)
    logger.info("Signal model loaded")
    return _cached_model


def predict_win_probability(signal) -> float | None:
    """Predict P(win) for a signal. Returns None if model unavailable or features missing."""
    model = load_model()
    if model is None:
        return None

    features = _extract_features(signal)
    if features is None:
        return None

    X = np.array([features], dtype=np.float64)
    prob = model.predict_proba(X)[0, 1]
    return round(float(prob), 4)


def clear_cache():
    """Clear the cached model (e.g., after retraining)."""
    global _cached_model
    _cached_model = None
