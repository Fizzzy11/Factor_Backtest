from __future__ import annotations

import math

import numpy as np
import pandas as pd

from factor_backtest.risk_exposure import RiskExposureData
from factor_backtest.returns import return_label, sort_return_labels

SUPPORTED_IC_METHODS = ("spearman", "pearson")


def compute_future_returns(open_price: pd.DataFrame, horizons: list[int]) -> dict[int, pd.DataFrame]:
    out: dict[int, pd.DataFrame] = {}
    entry_open = open_price.shift(-1)
    for horizon in horizons:
        exit_open = open_price.shift(-(horizon + 1))
        ret = exit_open / entry_open - 1.0
        ret = ret.where((exit_open > 0) & (entry_open > 0))
        out[horizon] = ret
    return out


def validate_ic_methods(methods: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if methods is None:
        methods = ["spearman"]
    if isinstance(methods, str):
        methods = [methods]
    normalized = []
    for method in methods:
        name = str(method).lower()
        if name not in normalized:
            normalized.append(name)
    unsupported = [name for name in normalized if name not in SUPPORTED_IC_METHODS]
    if unsupported:
        raise ValueError(f"Unsupported IC methods: {unsupported}. Supported methods: {list(SUPPORTED_IC_METHODS)}")
    if not normalized:
        raise ValueError("ic_methods must include at least one method")
    return normalized


def compute_daily_ic(
    factor: pd.DataFrame,
    future_returns: dict[int, pd.DataFrame],
    *,
    min_stocks: int,
    methods: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, pd.DataFrame]:
    selected_methods = validate_ic_methods(methods)
    out = {method: pd.DataFrame(index=factor.index) for method in selected_methods}
    for horizon, returns in future_returns.items():
        aligned_returns = returns.reindex_like(factor)
        values_by_method = {method: [] for method in selected_methods}
        for date in factor.index:
            f = factor.loc[date]
            r = aligned_returns.loc[date]
            good = f.notna() & r.notna() & np.isfinite(f) & np.isfinite(r)
            if int(good.sum()) < min_stocks:
                for method in selected_methods:
                    values_by_method[method].append(np.nan)
                continue
            f_good = f.loc[good]
            r_good = r.loc[good]
            for method in selected_methods:
                if method == "spearman":
                    value = _safe_corr(f_good.rank(), r_good.rank())
                else:
                    value = _safe_corr(f_good, r_good)
                values_by_method[method].append(float(value))
        col = f"ic_{return_label(horizon)}"
        for method in selected_methods:
            out[method][col] = values_by_method[method]
    return out


def compute_daily_rank_ic(
    factor: pd.DataFrame,
    future_returns: dict[int, pd.DataFrame],
    *,
    min_stocks: int,
) -> pd.DataFrame:
    return compute_daily_ic(factor, future_returns, min_stocks=min_stocks, methods=["spearman"])["spearman"]


def compute_factor_style_exposure_corr(
    factor: pd.DataFrame,
    risk_exposure: RiskExposureData,
    *,
    min_stocks: int,
    methods: list[str] | tuple[str, ...] | str | None = None,
) -> dict[str, pd.DataFrame]:
    selected_methods = validate_ic_methods(methods)
    out = {
        method: pd.DataFrame(index=factor.index, columns=risk_exposure.style_columns, dtype="float64")
        for method in selected_methods
    }
    for date in factor.index:
        daily_exposure = risk_exposure.slice_date(date, factor.columns)
        daily_factor = factor.loc[date]
        for style in risk_exposure.style_columns:
            exposure = pd.to_numeric(daily_exposure[style], errors="coerce")
            good = daily_factor.notna() & exposure.notna() & np.isfinite(daily_factor) & np.isfinite(exposure)
            if int(good.sum()) < min_stocks:
                continue
            f_good = daily_factor.loc[good]
            e_good = exposure.loc[good]
            for method in selected_methods:
                if method == "spearman":
                    out[method].loc[date, style] = _safe_corr(f_good.rank(), e_good.rank())
                else:
                    out[method].loc[date, style] = _safe_corr(f_good, e_good)
    return out


def compute_exposure_corr_summary(corr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in corr.columns:
        s = pd.to_numeric(corr[col], errors="coerce").dropna()
        mean = float(s.mean()) if not s.empty else np.nan
        std = float(s.std(ddof=1)) if len(s) > 1 else np.nan
        rows.append(
            {
                "style": col,
                "mean": mean,
                "std": std,
                "mean_over_std": mean / std if std and not math.isnan(std) else np.nan,
                "t_stat": mean / (std / math.sqrt(len(s))) if std and len(s) > 1 and not math.isnan(std) else np.nan,
                "positive_ratio": float((s > 0).mean()) if not s.empty else np.nan,
                "negative_ratio": float((s < 0).mean()) if not s.empty else np.nan,
                "valid_days": int(len(s)),
            }
        )
    return pd.DataFrame(rows).set_index("style")


def neutralize_factor_by_exposure(
    factor: pd.DataFrame,
    risk_exposure: RiskExposureData,
    *,
    include_styles: bool,
    include_industries: bool,
    min_stocks: int,
) -> tuple[pd.DataFrame, list[str]]:
    residual = pd.DataFrame(index=factor.index, columns=factor.columns, dtype="float64")
    warnings = []
    exposure_columns = []
    if include_styles:
        exposure_columns.extend(risk_exposure.style_columns)
    if include_industries:
        exposure_columns.extend(risk_exposure.industry_columns)
    if not exposure_columns:
        return factor.copy(), warnings

    total_missing_industry = 0
    for date in factor.index:
        daily_factor = factor.loc[date]
        daily_exposure = risk_exposure.slice_date(date, factor.columns)
        if include_industries:
            industry_sum = daily_exposure.loc[:, risk_exposure.industry_columns].fillna(0).sum(axis=1)
            missing_industry = industry_sum == 0
            total_missing_industry += int(missing_industry.sum())
        else:
            missing_industry = pd.Series(False, index=factor.columns)

        x = daily_exposure.loc[:, exposure_columns].apply(pd.to_numeric, errors="coerce")
        y = pd.to_numeric(daily_factor, errors="coerce")
        good = y.notna() & np.isfinite(y) & ~missing_industry
        good &= x.notna().all(axis=1) & np.isfinite(x).all(axis=1)
        if int(good.sum()) < min_stocks:
            continue
        x_good = x.loc[good]
        if include_industries and len(risk_exposure.industry_columns) > 1:
            drop_col = risk_exposure.industry_columns[-1]
            if drop_col in x_good.columns:
                x_good = x_good.drop(columns=[drop_col])
        design = np.column_stack([np.ones(len(x_good)), x_good.to_numpy(dtype="float64")])
        y_values = y.loc[good].to_numpy(dtype="float64")
        beta, *_ = np.linalg.lstsq(design, y_values, rcond=None)
        fitted = design @ beta
        residual.loc[date, good.index[good]] = y_values - fitted
    if total_missing_industry:
        warnings.append(f"{total_missing_industry} date-symbol rows have missing industry membership and were excluded")
    return residual, warnings


def compute_within_industry_group_returns(
    factor: pd.DataFrame,
    future_returns: dict[int, pd.DataFrame],
    risk_exposure: RiskExposureData,
    *,
    n_groups: int,
    min_industry_stocks: int,
) -> pd.DataFrame:
    records = []
    for date in factor.index:
        daily_factor = factor.loc[date]
        daily_exposure = risk_exposure.slice_date(date, factor.columns)
        industries = daily_exposure.loc[:, risk_exposure.industry_columns].fillna(0)
        for horizon, returns in future_returns.items():
            if date not in returns.index:
                continue
            daily_return = returns.loc[date].reindex(factor.columns)
            for industry in risk_exposure.industry_columns:
                in_industry = pd.to_numeric(industries[industry], errors="coerce").fillna(0) > 0
                good = (
                    in_industry
                    & daily_factor.notna()
                    & daily_return.notna()
                    & np.isfinite(daily_factor)
                    & np.isfinite(daily_return)
                )
                if int(good.sum()) < max(min_industry_stocks, n_groups):
                    continue
                ranked = daily_factor.loc[good].rank(method="first")
                try:
                    labels = pd.qcut(ranked, n_groups, labels=range(1, n_groups + 1))
                except ValueError:
                    continue
                for symbol, group in labels.astype(int).items():
                    records.append(
                        {
                            "trade_date": date,
                            "horizon": horizon,
                            "group": int(group),
                            "symbol": symbol,
                            "industry": industry,
                            "return": float(daily_return.loc[symbol]),
                        }
                    )
    if not records:
        return pd.DataFrame(columns=["group_return"]).rename_axis(["trade_date", "horizon", "group"])
    raw = pd.DataFrame(records)
    grouped = raw.groupby(["trade_date", "horizon", "group"])["return"].mean().to_frame("group_return")
    grouped.index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(grouped.index.get_level_values(0)),
            grouped.index.get_level_values(1),
            grouped.index.get_level_values(2).astype(int),
        ],
        names=["trade_date", "horizon", "group"],
    )
    return grouped.sort_index()


def compute_group_exposure_diagnostics(
    factor: pd.DataFrame,
    risk_exposure: RiskExposureData,
    *,
    n_groups: int,
    min_stocks: int,
    low_group: int = 1,
    high_group: int = 10,
) -> dict[str, pd.DataFrame]:
    style_records = []
    industry_records = []
    for date in factor.index:
        daily_factor = factor.loc[date]
        labels = _assign_factor_groups(daily_factor, n_groups=n_groups, min_stocks=min_stocks)
        if labels.empty:
            continue
        daily_exposure = risk_exposure.slice_date(date, factor.columns)
        style_values = daily_exposure.loc[:, risk_exposure.style_columns].apply(pd.to_numeric, errors="coerce")
        industry_values = daily_exposure.loc[:, risk_exposure.industry_columns].apply(pd.to_numeric, errors="coerce").fillna(0.0)

        group_symbols = {
            "pool": labels.index,
            f"G{low_group}": labels.index[labels == low_group],
            f"G{high_group}": labels.index[labels == high_group],
        }
        style_by_leg = {
            leg: _mean_exposure(style_values, symbols)
            for leg, symbols in group_symbols.items()
            if len(symbols) > 0
        }
        industry_valid = industry_values.sum(axis=1) > 0
        industry_by_leg = {
            leg: _mean_exposure(industry_values.loc[industry_valid], symbols)
            for leg, symbols in group_symbols.items()
            if len(symbols) > 0
        }
        _add_edge_differences(style_by_leg, low_group=low_group, high_group=high_group)
        _add_edge_differences(industry_by_leg, low_group=low_group, high_group=high_group)

        for leg, values in style_by_leg.items():
            for exposure, value in values.items():
                style_records.append(
                    {"trade_date": date, "leg": leg, "exposure": exposure, "value": float(value)}
                )
        for leg, values in industry_by_leg.items():
            for industry, value in values.items():
                industry_records.append(
                    {"trade_date": date, "leg": leg, "industry": industry, "value": float(value)}
                )

    style_daily = _exposure_records_to_frame(style_records, index_names=["trade_date", "leg", "exposure"])
    industry_daily = _exposure_records_to_frame(industry_records, index_names=["trade_date", "leg", "industry"])
    return {
        "style_daily": style_daily,
        "style_summary": compute_exposure_value_summary(style_daily, exposure_level="exposure"),
        "industry_daily": industry_daily,
        "industry_summary": compute_exposure_value_summary(industry_daily, exposure_level="industry"),
    }


def compute_exposure_value_summary(daily: pd.DataFrame, *, exposure_level: str) -> pd.DataFrame:
    if daily.empty:
        return pd.DataFrame(
            columns=["mean", "std", "mean_over_std", "t_stat", "positive_ratio", "negative_ratio", "valid_days"]
        ).rename_axis(["leg", exposure_level])
    rows = []
    for keys, group in daily.groupby(level=["leg", exposure_level]):
        leg, exposure = keys
        s = pd.to_numeric(group["value"], errors="coerce").dropna()
        mean = float(s.mean()) if not s.empty else np.nan
        std = float(s.std(ddof=1)) if len(s) > 1 else np.nan
        rows.append(
            {
                "leg": leg,
                exposure_level: exposure,
                "mean": mean,
                "std": std,
                "mean_over_std": mean / std if std and not math.isnan(std) else np.nan,
                "t_stat": mean / (std / math.sqrt(len(s))) if std and len(s) > 1 and not math.isnan(std) else np.nan,
                "positive_ratio": float((s > 0).mean()) if not s.empty else np.nan,
                "negative_ratio": float((s < 0).mean()) if not s.empty else np.nan,
                "valid_days": int(len(s)),
            }
        )
    return pd.DataFrame(rows).set_index(["leg", exposure_level]).sort_index()


def compute_group_turnover(
    factor: pd.DataFrame,
    *,
    n_groups: int,
    min_stocks: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    columns = [f"G{group}" for group in range(1, n_groups + 1)]
    rows = []
    previous: dict[int, set] | None = None
    for date in factor.index:
        labels = _assign_factor_groups(factor.loc[date], n_groups=n_groups, min_stocks=min_stocks)
        if labels.empty:
            rows.append(pd.Series(index=columns, dtype="float64", name=date))
            previous = None
            continue
        current = {group: set(labels.index[labels == group]) for group in range(1, n_groups + 1)}
        values = {}
        for group in range(1, n_groups + 1):
            col = f"G{group}"
            current_members = current[group]
            if previous is None or not current_members:
                values[col] = np.nan
                continue
            values[col] = 1.0 - len(previous.get(group, set()) & current_members) / len(current_members)
        rows.append(pd.Series(values, name=date, dtype="float64"))
        previous = current
    daily = pd.DataFrame(rows, columns=columns)
    daily.index = pd.to_datetime(daily.index)
    summary = compute_turnover_summary(daily)
    edge = summary.reindex(["G1", f"G{n_groups}"]).copy()
    if {"G1", f"G{n_groups}"}.issubset(daily.columns):
        edge_avg = daily[["G1", f"G{n_groups}"]].mean(axis=1)
        edge_avg_summary = compute_turnover_summary(edge_avg.to_frame("edge_avg"))
        edge = pd.concat([edge, edge_avg_summary], axis=0)
    return daily, summary, edge


def compute_turnover_summary(turnover: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in turnover.columns:
        s = pd.to_numeric(turnover[col], errors="coerce").dropna()
        mean = float(s.mean()) if not s.empty else np.nan
        std = float(s.std(ddof=1)) if len(s) > 1 else np.nan
        rows.append(
            {
                "group": col,
                "mean": mean,
                "std": std,
                "mean_over_std": mean / std if std and not math.isnan(std) else np.nan,
                "min": float(s.min()) if not s.empty else np.nan,
                "max": float(s.max()) if not s.empty else np.nan,
                "valid_days": int(len(s)),
            }
        )
    return pd.DataFrame(rows).set_index("group") if rows else pd.DataFrame()


def _assign_factor_groups(daily_factor: pd.Series, *, n_groups: int, min_stocks: int) -> pd.Series:
    f = pd.to_numeric(daily_factor, errors="coerce")
    good = f.notna() & np.isfinite(f)
    if int(good.sum()) < max(min_stocks, n_groups):
        return pd.Series(dtype="int64")
    ranked = f.loc[good].rank(method="first")
    try:
        return pd.qcut(ranked, n_groups, labels=range(1, n_groups + 1)).astype(int)
    except ValueError:
        return pd.Series(dtype="int64")


def _mean_exposure(values: pd.DataFrame, symbols: pd.Index) -> pd.Series:
    aligned = values.reindex(symbols)
    return aligned.mean(axis=0, skipna=True)


def _add_edge_differences(values_by_leg: dict[str, pd.Series], *, low_group: int, high_group: int) -> None:
    low = f"G{low_group}"
    high = f"G{high_group}"
    if low in values_by_leg and high in values_by_leg:
        values_by_leg[f"{high}_minus_{low}"] = values_by_leg[high] - values_by_leg[low]
    if "pool" in values_by_leg and low in values_by_leg:
        values_by_leg[f"{low}_minus_pool"] = values_by_leg[low] - values_by_leg["pool"]
    if "pool" in values_by_leg and high in values_by_leg:
        values_by_leg[f"{high}_minus_pool"] = values_by_leg[high] - values_by_leg["pool"]


def _exposure_records_to_frame(records: list[dict], *, index_names: list[str]) -> pd.DataFrame:
    if not records:
        empty_index = pd.MultiIndex.from_arrays([[] for _ in index_names], names=index_names)
        return pd.DataFrame({"value": []}, index=empty_index)
    out = pd.DataFrame(records).set_index(index_names).sort_index()
    out.index = pd.MultiIndex.from_arrays(
        [
            pd.to_datetime(out.index.get_level_values(0)),
            *[out.index.get_level_values(idx) for idx in range(1, len(index_names))],
        ],
        names=index_names,
    )
    return out[["value"]]


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    if left.nunique(dropna=True) <= 1 or right.nunique(dropna=True) <= 1:
        return np.nan
    return left.corr(right)


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
                        "horizon": horizon,
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
    for horizon in sort_return_labels({h for h, _ in wide.columns}):
        if (horizon, high_group) in wide.columns and (horizon, low_group) in wide.columns:
            out[f"long_short_{return_label(horizon)}"] = wide[(horizon, high_group)] - wide[(horizon, low_group)]
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
