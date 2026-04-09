from datetime import date

from backtest.folds.splits import walk_forward_quarterly, kfold_shuffled_yearly


def test_full_years_no_partial():
    folds = walk_forward_quarterly(date(2023, 1, 1), date(2025, 12, 31))
    assert len(folds) == 12
    assert all(not f.partial for f in folds)
    q4_2023 = next(f for f in folds if f.fold_id == "2023Q4")
    assert q4_2023.oos_start == date(2023, 10, 1)
    assert q4_2023.oos_end == date(2023, 12, 31)


def test_partial_final_quarter():
    folds = walk_forward_quarterly(date(2023, 1, 1), date(2026, 4, 9))
    assert len(folds) == 14  # 2023Q1..2026Q2
    complete = [f for f in folds if not f.partial]
    partial = [f for f in folds if f.partial]
    assert len(complete) == 13
    assert len(partial) == 1
    assert partial[0].fold_id == "2026Q2"
    assert partial[0].oos_end == date(2026, 4, 9)


def test_start_midquarter_clamped():
    folds = walk_forward_quarterly(date(2023, 2, 15), date(2023, 6, 30))
    assert folds[0].fold_id == "2023Q1"
    assert folds[0].oos_start == date(2023, 2, 15)
    assert folds[0].oos_end == date(2023, 3, 31)
    assert folds[-1].fold_id == "2023Q2"


def test_kfold_shuffled_yearly():
    folds = kfold_shuffled_yearly(date(2023, 1, 1), date(2025, 12, 31))
    assert [f.fold_id for f in folds] == ["2023", "2024", "2025"]
    assert folds[0].oos_start == date(2023, 1, 1)
    assert folds[0].oos_end == date(2023, 12, 31)
