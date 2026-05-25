import numpy as np
import pandas as pd

from factor_backtest.analytics import (
    compute_daily_group_returns,
    compute_daily_rank_ic,
    compute_future_returns,
    compute_ic_stats,
    compute_long_short_returns,
    compute_quality_metrics,
)
from factor_backtest.filters import compute_tradability_mask


def _dates():
    return pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19", "2026-05-20"])


def test_compute_future_returns_uses_next_open_entry_after_signal_date():
    open_price = pd.DataFrame(
        {"A": [10.0, 11.0, 12.0, 15.0], "B": [20.0, 18.0, 22.0, 24.0]},
        index=_dates(),
    )

    returns = compute_future_returns(open_price, horizons=[1, 2])

    # Signal date 2026-05-15 is evaluated by entering at the next trading day's open.
    assert np.isclose(returns[1].loc[pd.Timestamp("2026-05-15"), "A"], 12.0 / 11.0 - 1.0)
    assert np.isclose(returns[2].loc[pd.Timestamp("2026-05-15"), "A"], 15.0 / 11.0 - 1.0)
    assert pd.isna(returns[1].loc[pd.Timestamp("2026-05-19"), "A"])


def test_compute_tradability_mask_applies_entry_day_filters_only():
    dates = pd.to_datetime(["2026-05-15"])
    cols = ["A", "B", "C", "D", "E"]
    high = pd.DataFrame([[10, 10, 10, 10, 10]], index=dates, columns=cols)
    low = pd.DataFrame([[9, 9, 9, 9, 9]], index=dates, columns=cols)
    limit_up = pd.DataFrame([[11, 10, 11, 11, 11]], index=dates, columns=cols)
    limit_down = pd.DataFrame([[8, 8, 9, 8, 8]], index=dates, columns=cols)
    is_st = pd.DataFrame([[0, 0, 0, 1, 0]], index=dates, columns=cols)
    is_suspended = pd.DataFrame([[0, 0, 0, 0, 1]], index=dates, columns=cols)
    listed_days = pd.DataFrame([[121, 121, 121, 121, 121]], index=dates, columns=cols)
    open_price = pd.DataFrame([[10, 10, 10, 10, 10]], index=dates, columns=cols)

    result = compute_tradability_mask(
        open_price=open_price,
        high_price=high,
        low_price=low,
        limit_up_price=limit_up,
        limit_down_price=limit_down,
        is_st=is_st,
        is_suspended=is_suspended,
        listed_days=listed_days,
        min_listed_days=120,
    )

    assert bool(result.mask.loc[dates[0], "A"]) is True
    assert bool(result.mask.loc[dates[0], "B"]) is False
    assert bool(result.mask.loc[dates[0], "C"]) is False
    assert bool(result.mask.loc[dates[0], "D"]) is False
    assert bool(result.mask.loc[dates[0], "E"]) is False
    assert result.summary.loc[dates[0], "limit_up_touch_count"] == 1
    assert result.summary.loc[dates[0], "limit_down_touch_count"] == 1
    assert result.summary.loc[dates[0], "st_count"] == 1
    assert result.summary.loc[dates[0], "suspended_count"] == 1


def test_daily_rank_ic_group_returns_and_long_short_are_pool_local():
    dates = pd.to_datetime(["2026-05-15", "2026-05-18"])
    symbols = [f"S{i}" for i in range(40)]
    factor = pd.DataFrame([range(40), range(40)], index=dates, columns=symbols, dtype=float)
    future = pd.DataFrame([range(40), range(40)], index=dates, columns=symbols, dtype=float)

    ic = compute_daily_rank_ic(factor, {1: future}, min_stocks=30)
    groups = compute_daily_group_returns(factor, {1: future}, n_groups=10, min_stocks=10)
    long_short = compute_long_short_returns(groups)

    assert ic.loc[dates[0], "ic_1d"] == 1.0
    assert groups.loc[(dates[0], 1, 1), "group_return"] < groups.loc[(dates[0], 1, 10), "group_return"]
    assert long_short.loc[dates[0], "long_short_1d"] > 0


def test_quality_metrics_and_ic_stats():
    factor = pd.DataFrame(
        {"A": [0.0, np.nan], "B": [np.inf, 2.0], "C": [3.0, 0.0]},
        index=pd.to_datetime(["2026-05-15", "2026-05-18"]),
    )
    pool_mask = pd.DataFrame(True, index=factor.index, columns=factor.columns)
    valid_mask = np.isfinite(factor)

    quality = compute_quality_metrics(factor, pool_mask, valid_mask)

    assert quality.loc[pd.Timestamp("2026-05-15"), "zero_ratio"] == 1 / 3
    assert quality.loc[pd.Timestamp("2026-05-15"), "inf_ratio"] == 1 / 3
    assert quality.loc[pd.Timestamp("2026-05-18"), "nan_ratio"] == 1 / 3

    ic = pd.DataFrame({"ic_1d": [0.1, 0.2, -0.1, 0.0]})
    stats = compute_ic_stats(ic)

    assert np.isclose(stats.loc["1d", "ic_mean"], 0.05)
    assert stats.loc["1d", "ic_positive_ratio"] == 0.5
