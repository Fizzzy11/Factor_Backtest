from __future__ import annotations

import math

import numpy as np
import pandas as pd


def compute_future_returns(open_price: pd.DataFrame, horizons: list[int]) -> dict[int, pd.DataFrame]:
    out: dict[int, pd.DataFrame] = {}
    entry_open = open_price.shift(-1)
    for horizon in horizons:
        exit_open = open_price.shift(-(horizon + 1))
        ret = exit_open / entry_open - 1.0
        ret = ret.where((exit_open > 0) & (entry_open > 0))
        out[horizon] = ret
    return out


def compute_daily_rank_ic(
    factor: pd.DataFrame,
    future_returns: dict[int, pd.DataFrame],
    *,
    min_stocks: int,
) -> pd.DataFrame:
    rows = pd.DataFrame(index=factor.index)
    for horizon, returns in future_returns.items():
        values = []
        aligned_returns = returns.reindex_like(factor)
        for date in factor.index:
            f = factor.loc[date]
            r = aligned_returns.loc[date]
            good = f.notna() & r.notna() & np.isfinite(f) & np.isfinite(r)
            if int(good.sum()) < min_stocks:
                values.append(np.nan)
            else:
                values.append(float(f.loc[good].rank().corr(r.loc[good].rank())))
        rows[f"ic_{horizon}d"] = values
    return rows


def compute_ic_stats(ic: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in ic.columns:
        horizon = col.replace("ic_", "")
        s = pd.to_numeric(ic[col], errors="coerce").dropna()
        mean = float(s.mean()) if not s.empty else np.nan
        std = float(s.std(ddof=1)) if len(s) > 1 else np.nan
        rows.append(
            {
                "horizon": horizon,
                "ic_mean": mean,
                "ic_std": std,
                "icir": mean / std if std and not math.isnan(std) else np.nan,
                "ic_positive_ratio": float((s > 0).mean()) if not s.empty else np.nan,
                "t_stat": mean / (std / math.sqrt(len(s))) if std and len(s) > 1 and not math.isnan(std) else np.nan,
            }
        )
    return pd.DataFrame(rows).set_index("horizon")


def compute_daily_group_returns(
    factor: pd.DataFrame,
    future_returns: dict[int, pd.DataFrame],
    *,
    n_groups: int,
    min_stocks: int,
) -> pd.DataFrame:
    records = []
    for date in factor.index:
        f = factor.loc[date]
        for horizon, returns in future_returns.items():
            if date not in returns.index:
                continue
            r = returns.loc[date].reindex(f.index)
            good = f.notna() & r.notna() & np.isfinite(f) & np.isfinite(r)
            if int(good.sum()) < max(min_stocks, n_groups):
                continue
            ranked = f.loc[good].rank(method="first")
            try:
                labels = pd.qcut(ranked, n_groups, labels=range(1, n_groups + 1))
            except ValueError:
                continue
            tmp = pd.DataFrame({"group": labels.astype(int), "return": r.loc[good]})
            grouped = tmp.groupby("group")["return"].mean()
            for group, value in grouped.items():
                records.append(
                    {
                        "trade_date": date,
                        "horizon": int(horizon),
                        "group": int(group),
                        "group_return": float(value),
                    }
                )
    if not records:
        return pd.DataFrame(columns=["group_return"]).set_index(
            [pd.Index([], name="trade_date"), pd.Index([], name="horizon"), pd.Index([], name="group")]
        )
    return pd.DataFrame(records).set_index(["trade_date", "horizon", "group"]).sort_index()


def compute_long_short_returns(group_returns: pd.DataFrame, low_group: int = 1, high_group: int = 10) -> pd.DataFrame:
    if group_returns.empty:
        return pd.DataFrame()
    wide = group_returns["group_return"].unstack(["horizon", "group"])
    out = pd.DataFrame(index=wide.index)
    for horizon in sorted({h for h, _ in wide.columns}):
        if (horizon, high_group) in wide.columns and (horizon, low_group) in wide.columns:
            out[f"long_short_{horizon}d"] = wide[(horizon, high_group)] - wide[(horizon, low_group)]
    return out


def compute_quality_metrics(
    factor: pd.DataFrame,
    pool_mask: pd.DataFrame,
    valid_mask: pd.DataFrame,
) -> pd.DataFrame:
    pool = pool_mask.reindex_like(factor).fillna(False).astype(bool)
    valid = valid_mask.reindex_like(factor).fillna(False).astype(bool)
    in_pool_factor = factor.where(pool)
    pool_count = pool.sum(axis=1).replace(0, np.nan)
    out = pd.DataFrame(index=factor.index)
    out["pool_stock_count"] = pool.sum(axis=1)
    out["valid_factor_count"] = (pool & valid).sum(axis=1)
    out["coverage_ratio"] = out["valid_factor_count"] / pool_count
    out["zero_ratio"] = ((in_pool_factor == 0).sum(axis=1)) / pool_count
    out["nan_ratio"] = factor.isna().where(pool, False).sum(axis=1) / pool_count
    out["inf_ratio"] = np.isinf(factor).where(pool, False).sum(axis=1) / pool_count
    return out


def compute_performance_metrics(long_short: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in long_short.columns:
        s = pd.to_numeric(long_short[col], errors="coerce").dropna()
        mean = float(s.mean()) if not s.empty else np.nan
        std = float(s.std(ddof=1)) if len(s) > 1 else np.nan
        nav = (1 + s).cumprod() if not s.empty else pd.Series(dtype=float)
        max_dd = float((nav / nav.cummax() - 1).min()) if not nav.empty else np.nan
        rows.append(
            {
                "series": col,
                "mean": mean,
                "std": std,
                "sharpe": mean / std if std and not math.isnan(std) else np.nan,
                "max_drawdown": max_dd,
                "win_rate": float((s > 0).mean()) if not s.empty else np.nan,
                "t_stat": mean / (std / math.sqrt(len(s))) if std and len(s) > 1 and not math.isnan(std) else np.nan,
            }
        )
    return pd.DataFrame(rows).set_index("series") if rows else pd.DataFrame()
