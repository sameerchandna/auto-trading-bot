from backtest.folds.aggregator import aggregate, MIN_TRADES_PER_FOLD


def _fold(fid, trades, pnl, partial=False, curve_end=None):
    curve = [("2023-01-01T00:00:00", 10_000.0), ("2023-03-31T00:00:00", curve_end or (10_000.0 + pnl))]
    return {
        "fold_id": fid,
        "label": f"OOS:{fid}",
        "partial": partial,
        "oos_start": "2023-01-01",
        "oos_end": "2023-03-31",
        "metrics": {
            "total_trades": trades,
            "win_rate": 0.5,
            "profit_factor": 1.5,
            "sharpe_ratio": 1.0,
            "max_drawdown_pct": 0.1,
            "total_pnl": pnl,
        },
        "equity_curve": curve,
        "trades": [{"pnl": pnl / max(trades, 1)} for _ in range(trades)],
    }


def test_summary_excludes_insufficient_and_partial():
    results = [
        _fold("A", 25, 500.0),
        _fold("B", 25, -200.0),
        _fold("C", 5, 100.0),                  # insufficient
        _fold("D", 25, 300.0, partial=True),   # partial excluded
    ]
    agg = aggregate(results, 10_000)
    assert agg["num_counted"] == 2
    assert agg["pct_profitable_folds"] == 0.5
    assert agg["summary"]["total_pnl"]["mean"] == 150.0  # (500 + -200)/2
    statuses = {f["fold_id"]: f["status"] for f in agg["per_fold"]}
    assert statuses["C"] == "insufficient_data"
    assert statuses["D"] == "partial"
    assert statuses["A"] == "ok"


def test_combined_curve_continuity():
    results = [_fold("A", 25, 500.0), _fold("B", 25, 300.0)]
    agg = aggregate(results, 10_000)
    curve = agg["combined_equity_curve"]
    assert curve[0][1] == 10_000.0
    # After fold A ending at 10_500, fold B is rebased to start at 10_500
    # and ends at 10_500 + 300 = 10_800
    assert abs(curve[-1][1] - 10_800.0) < 1e-6
    assert agg["combined_metrics"]["total_trades"] == 50
