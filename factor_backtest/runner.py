from __future__ import annotations

from dataclasses import replace
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

from factor_backtest.analytics import (
    compute_daily_group_returns,
    compute_daily_rank_ic,
    compute_future_returns,
    compute_ic_stats,
    compute_long_short_returns,
    compute_performance_metrics,
    compute_quality_metrics,
)
from factor_backtest.config import BacktestConfig
from factor_backtest.filters import compute_tradability_mask
from factor_backtest.io import ensure_dir, read_json, read_table, write_json, write_table
from factor_backtest.market_data import MarketDataBundle
from factor_backtest.pools import resolve_selected_pools
from factor_backtest.sections import DEFAULT_SECTIONS, ReportSection, SectionResult


@dataclass
class BacktestRunResult:
    run_dir: Path
    latest_dir: Path | None
    section_status: dict[str, dict[str, SectionResult]]
    warnings: list[str]


def run_factor_backtest(
    *,
    factor_df: pd.DataFrame,
    market_data: MarketDataBundle,
    config: BacktestConfig | None = None,
    factor_name: str | None = None,
    sections: list[ReportSection] | None = None,
    log_fn=print,
) -> BacktestRunResult:
    cfg = config or BacktestConfig()
    resolved_factor_name = factor_name or cfg.factor_name or "unnamed_factor"
    run_dir, latest_dir = _prepare_run_directories(cfg, resolved_factor_name)
    _log(cfg, log_fn, f"[v1] starting backtest: {resolved_factor_name}")
    _log(cfg, log_fn, f"[v1] output directory: {run_dir}")
    section_list = sections if sections is not None else _resolve_sections(cfg)
    pools = resolve_selected_pools(cfg.selected_pools)
    status: dict[str, dict[str, SectionResult]] = {}
    run_warnings: list[str] = []

    factor = _standardize_factor(factor_df)
    _log(cfg, log_fn, f"[v1] computing future returns: horizons={cfg.horizons}")
    future_returns_all = compute_future_returns(market_data.open_price, cfg.horizons)

    for pool_name, pool_mask in pools.items():
        _log(cfg, log_fn, f"[v1] pool {pool_name}: preparing data")
        pool_dir = ensure_dir(run_dir / "pools" / pool_name)
        artifacts_dir = ensure_dir(pool_dir / "artifacts")
        tables_dir = ensure_dir(pool_dir / "tables")
        plots_dir = ensure_dir(pool_dir / "plots")

        pool_factor, pool_bool = _apply_pool(factor, market_data.open_price, pool_mask)
        run_warnings.extend(_pool_coverage_warnings(pool_name, pool_mask, factor.index))
        base_mask = np.isfinite(pool_factor)

        if cfg.tradability_filter and not _has_tradability_data(market_data):
            missing = _missing_tradability_fields(market_data)
            raise ValueError(
                "tradability_filter=True requires market data fields: "
                + ", ".join(missing)
                + ". Set tradability_filter=False to skip entry-day tradability filtering."
            )

        if cfg.tradability_filter:
            _log(cfg, log_fn, f"[v1] pool {pool_name}: applying tradability filter")
            tradability = compute_tradability_mask(
                open_price=_next_trading_day_frame(market_data.open_price, pool_factor),
                high_price=_next_trading_day_frame(market_data.high_price, pool_factor),
                low_price=_next_trading_day_frame(market_data.low_price, pool_factor),
                limit_up_price=_next_trading_day_frame(market_data.limit_up_price, pool_factor),
                limit_down_price=_next_trading_day_frame(market_data.limit_down_price, pool_factor),
                is_st=_next_trading_day_frame(market_data.is_st, pool_factor),
                is_suspended=_next_trading_day_frame(market_data.is_suspended, pool_factor),
                listed_days=_next_trading_day_frame(market_data.listed_days, pool_factor),
                min_listed_days=cfg.min_listed_days,
            )
            valid_mask = base_mask & tradability.mask
            filter_summary = tradability.summary
        else:
            _log(cfg, log_fn, f"[v1] pool {pool_name}: tradability filter disabled")
            valid_mask = base_mask
            filter_summary = pd.DataFrame(index=pool_factor.index)
            filter_summary["after_filter_count"] = valid_mask.sum(axis=1)

        filtered_factor = pool_factor.where(valid_mask)
        future_returns = {h: r.reindex_like(filtered_factor) for h, r in future_returns_all.items()}
        _log(cfg, log_fn, f"[v1] pool {pool_name}: computing RankIC")
        daily_ic = compute_daily_rank_ic(filtered_factor, future_returns, min_stocks=cfg.min_ic_stocks)
        _log(cfg, log_fn, f"[v1] pool {pool_name}: computing group returns")
        daily_group_returns = compute_daily_group_returns(
            filtered_factor,
            future_returns,
            n_groups=10,
            min_stocks=cfg.min_group_stocks,
        )
        _log(cfg, log_fn, f"[v1] pool {pool_name}: computing long-short returns and data quality")
        daily_long_short = compute_long_short_returns(daily_group_returns)
        data_quality = compute_quality_metrics(pool_factor, pool_bool, valid_mask)

        context = {
            "pool_name": pool_name,
            "factor": filtered_factor,
            "future_returns": future_returns,
            "daily_ic": daily_ic,
            "daily_group_returns": daily_group_returns,
            "daily_long_short_returns": daily_long_short,
            "data_quality": data_quality,
            "filter_summary": filter_summary,
            "plots_dir": plots_dir,
            "plot_index": filtered_factor.index,
            "horizon_colors": cfg.horizon_colors,
            "group_return_windows": cfg.group_return_windows,
        }

        _log(cfg, log_fn, f"[v1] pool {pool_name}: writing artifacts")
        write_table(filtered_factor, artifacts_dir / "aligned_factor.parquet")
        write_table(valid_mask.astype(bool), artifacts_dir / "valid_mask.parquet")
        for horizon, returns in future_returns.items():
            write_table(returns, artifacts_dir / f"future_returns_{horizon}d.parquet")
        write_table(daily_ic, artifacts_dir / "daily_ic.parquet")
        write_table(daily_group_returns, artifacts_dir / "daily_group_returns.parquet")
        write_table(daily_long_short, artifacts_dir / "daily_long_short_returns.parquet")
        write_table(data_quality, artifacts_dir / "data_quality.parquet")
        write_table(filter_summary, artifacts_dir / "filter_summary.parquet")

        status[pool_name] = {}
        for section in section_list:
            _log(cfg, log_fn, f"[v1] pool {pool_name}: running section {section.name}")
            try:
                result = section.compute(context)
                if cfg.render_plots:
                    result = section.render(context, result)
            except Exception as exc:
                result = SectionResult(name=section.name, status="failed", error=str(exc))
            status[pool_name][section.name] = result
            for table_name, table in result.tables.items():
                write_table(table, tables_dir / f"{table_name}.csv")

    run_meta = {
        "framework_version": cfg.framework_version,
        "factor_name": resolved_factor_name,
        "selected_pools": cfg.selected_pools,
        "horizons": cfg.horizons,
        "tradability_filter": cfg.tradability_filter,
        "min_listed_days": cfg.min_listed_days,
        "enabled_sections": cfg.enabled_sections,
        "group_return_windows": cfg.group_return_windows,
        "output_layout": cfg.output_layout,
        "render_plots": cfg.render_plots,
    }
    write_json(run_meta, run_dir / "run_meta.json")
    write_json({"warnings": run_warnings, "sections": _status_to_json(status)}, run_dir / "run_log.json")
    _write_html_report(run_dir, status, meta=run_meta, warnings=run_warnings)
    if latest_dir is not None:
        _sync_latest_dir(run_dir, latest_dir)
    _log(cfg, log_fn, f"[v1] completed: {run_dir}")
    return BacktestRunResult(run_dir=run_dir, latest_dir=latest_dir, section_status=status, warnings=run_warnings)


def run_factor_backtest_minimal(
    *,
    factor_df: pd.DataFrame,
    market_data: MarketDataBundle,
    config: BacktestConfig | None = None,
) -> pd.DataFrame:
    cfg = config or BacktestConfig()
    factor = _standardize_factor(factor_df)
    future_returns_all = compute_future_returns(market_data.open_price, cfg.horizons)
    rows = []
    for pool_name, pool_mask in resolve_selected_pools(cfg.selected_pools).items():
        pool_factor, pool_bool = _apply_pool(factor, market_data.open_price, pool_mask)
        base_mask = np.isfinite(pool_factor)
        if cfg.tradability_filter:
            if not _has_tradability_data(market_data):
                missing = _missing_tradability_fields(market_data)
                raise ValueError("tradability_filter=True requires market data fields: " + ", ".join(missing))
            tradability = compute_tradability_mask(
                open_price=_next_trading_day_frame(market_data.open_price, pool_factor),
                high_price=_next_trading_day_frame(market_data.high_price, pool_factor),
                low_price=_next_trading_day_frame(market_data.low_price, pool_factor),
                limit_up_price=_next_trading_day_frame(market_data.limit_up_price, pool_factor),
                limit_down_price=_next_trading_day_frame(market_data.limit_down_price, pool_factor),
                is_st=_next_trading_day_frame(market_data.is_st, pool_factor),
                is_suspended=_next_trading_day_frame(market_data.is_suspended, pool_factor),
                listed_days=_next_trading_day_frame(market_data.listed_days, pool_factor),
                min_listed_days=cfg.min_listed_days,
            )
            valid_mask = base_mask & tradability.mask
        else:
            valid_mask = base_mask
        filtered_factor = pool_factor.where(valid_mask)
        future_returns = {h: r.reindex_like(filtered_factor) for h, r in future_returns_all.items()}
        daily_ic = compute_daily_rank_ic(filtered_factor, future_returns, min_stocks=cfg.min_ic_stocks)
        group_returns = compute_daily_group_returns(
            filtered_factor,
            future_returns,
            n_groups=10,
            min_stocks=cfg.min_group_stocks,
        )
        long_short = compute_long_short_returns(group_returns)
        quality = compute_quality_metrics(pool_factor, pool_bool, valid_mask)
        ic_stats = compute_ic_stats(daily_ic)
        perf = compute_performance_metrics(long_short)
        row = {
            "factor_name": cfg.factor_name,
            "pool": pool_name,
            "start_date": filtered_factor.index.min() if len(filtered_factor.index) else pd.NaT,
            "end_date": filtered_factor.index.max() if len(filtered_factor.index) else pd.NaT,
            "coverage_mean": quality["coverage_ratio"].mean() if "coverage_ratio" in quality else np.nan,
            "valid_factor_count_mean": quality["valid_factor_count"].mean() if "valid_factor_count" in quality else np.nan,
        }
        for horizon in cfg.horizons:
            h_key = f"{horizon}d"
            ic_row = ic_stats.loc[h_key] if h_key in ic_stats.index else pd.Series(dtype=float)
            row[f"ic_mean_{h_key}"] = ic_row.get("ic_mean", np.nan)
            row[f"icir_{h_key}"] = ic_row.get("icir", np.nan)
            row[f"ic_t_stat_{h_key}"] = ic_row.get("t_stat", np.nan)
            ls_key = f"long_short_{h_key}"
            ls_row = perf.loc[ls_key] if ls_key in perf.index else pd.Series(dtype=float)
            row[f"long_short_mean_{h_key}"] = ls_row.get("mean", np.nan)
            row[f"long_short_sharpe_{h_key}"] = ls_row.get("sharpe", np.nan)
            row[f"long_short_t_stat_{h_key}"] = ls_row.get("t_stat", np.nan)
        rows.append(row)
    return pd.DataFrame(rows).set_index(["factor_name", "pool"])


def run_factor_backtest_data(
    *,
    factor_df: pd.DataFrame,
    market_data: MarketDataBundle,
    config: BacktestConfig | None = None,
    factor_name: str | None = None,
    sections: list[ReportSection] | None = None,
    log_fn=print,
) -> BacktestRunResult:
    cfg = replace(config or BacktestConfig(), render_plots=False)
    return run_factor_backtest(
        factor_df=factor_df,
        market_data=market_data,
        config=cfg,
        factor_name=factor_name,
        sections=sections,
        log_fn=log_fn,
    )


def render_factor_backtest_report(run_dir: str | Path) -> Path:
    run_path = Path(run_dir)
    meta = read_json(run_path / "run_meta.json")
    log = read_json(run_path / "run_log.json")
    status: dict[str, dict[str, SectionResult]] = {}
    for pool_dir in sorted((run_path / "pools").iterdir()):
        if not pool_dir.is_dir():
            continue
        status[pool_dir.name] = _render_loaded_section_results(pool_dir, _load_section_results_from_disk(pool_dir))
    _write_html_report(run_path, status, meta=meta, warnings=log.get("warnings", []))
    return run_path / "report.html"


def _standardize_factor(factor_df: pd.DataFrame) -> pd.DataFrame:
    out = factor_df.copy()
    out.index = pd.to_datetime(out.index)
    out.index.name = "trade_date"
    out.columns = [str(c) for c in out.columns]
    return out.sort_index().sort_index(axis=1).apply(pd.to_numeric, errors="coerce")


def _prepare_run_directories(cfg: BacktestConfig, factor_name: str) -> tuple[Path, Path | None]:
    factor_root = Path(cfg.output_root) / factor_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if cfg.output_layout == "latest_runs":
        run_dir = ensure_dir(factor_root / "runs" / timestamp)
        latest_dir = factor_root / "latest"
        return run_dir, latest_dir
    if cfg.output_layout == "timestamp":
        return ensure_dir(factor_root / timestamp), None
    raise ValueError("output_layout must be 'latest_runs' or 'timestamp'")


def _sync_latest_dir(run_dir: Path, latest_dir: Path) -> None:
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(run_dir, latest_dir)


def _apply_pool(
    factor: pd.DataFrame,
    open_price: pd.DataFrame,
    pool_mask: pd.DataFrame | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    common_dates = factor.index.intersection(open_price.index)
    common_symbols = factor.columns.intersection(open_price.columns)
    out = factor.loc[common_dates, common_symbols]
    if pool_mask is None:
        pool_bool = pd.DataFrame(True, index=out.index, columns=out.columns)
        return out, pool_bool
    pool_bool = pool_mask.reindex(index=out.index, columns=out.columns).fillna(False).astype(bool)
    return out.where(pool_bool), pool_bool


def _next_trading_day_frame(source: pd.DataFrame, like: pd.DataFrame) -> pd.DataFrame:
    return source.reindex(index=like.index, columns=like.columns).shift(-1)


def _pool_coverage_warnings(pool_name: str, pool_mask: pd.DataFrame | None, factor_dates: pd.Index) -> list[str]:
    if pool_mask is None or pool_mask.empty or len(factor_dates) == 0:
        return []
    factor_idx = pd.DatetimeIndex(pd.to_datetime(factor_dates)).sort_values()
    pool_idx = pd.DatetimeIndex(pd.to_datetime(pool_mask.index)).sort_values()
    warnings: list[str] = []
    if factor_idx.min() < pool_idx.min():
        warnings.append(
            f"股票池 {pool_name} 的成分数据起始日为 {pool_idx.min().date()}，"
            f"早于该日期的因子日期不会进入该股票池有效样本。"
        )
    if factor_idx.max() > pool_idx.max():
        warnings.append(
            f"股票池 {pool_name} 的成分数据结束日为 {pool_idx.max().date()}，"
            f"晚于该日期的因子日期不会进入该股票池有效样本。"
        )
    return warnings


def _has_tradability_data(market_data: MarketDataBundle) -> bool:
    return not _missing_tradability_fields(market_data)


def _missing_tradability_fields(market_data: MarketDataBundle) -> list[str]:
    required = ("high_price", "low_price", "limit_up_price", "limit_down_price", "is_st", "is_suspended", "listed_days")
    return [name for name in required if getattr(market_data, name) is None]


def _resolve_sections(cfg: BacktestConfig) -> list[ReportSection]:
    if cfg.enabled_sections == "all":
        return DEFAULT_SECTIONS
    if not isinstance(cfg.enabled_sections, list):
        raise ValueError("enabled_sections must be 'all' or a list of section names")
    sections_by_name = {section.name: section for section in DEFAULT_SECTIONS}
    unknown = [name for name in cfg.enabled_sections if name not in sections_by_name]
    if unknown:
        raise KeyError(f"Unknown report sections: {unknown}")
    return [sections_by_name[name] for name in cfg.enabled_sections]


def _status_to_json(status: dict[str, dict[str, SectionResult]]) -> dict:
    return {
        pool: {
            name: {"status": result.status, "error": result.error, "warnings": result.warnings}
            for name, result in sections.items()
        }
        for pool, sections in status.items()
    }


def _load_section_results_from_disk(pool_dir: Path) -> dict[str, SectionResult]:
    tables_dir = pool_dir / "tables"
    plots_dir = pool_dir / "plots"
    group_return_tables = ["daily_group_returns", "group_return_summary"]
    if tables_dir.exists():
        group_return_tables.extend(sorted(path.stem for path in tables_dir.glob("group_cumulative_returns_*d.csv")))
    table_map = {
        "data_quality": ["data_quality", "data_quality_counts", "data_quality_ratios"],
        "ic_overview": ["ic_overview"],
        "cumulative_ic": ["daily_ic", "cumulative_ic", "ic_stats"],
        "group_return": group_return_tables,
        "layered_group_return": ["layered_group_return_summary"],
        "long_short": ["daily_long_short_returns", "cumulative_long_short_returns"],
        "performance_metrics": ["performance_metrics"],
    }
    plot_map = {
        "data_quality": ["data_quality_counts.png", "data_quality_ratios.png"],
        "ic_overview": ["ic_overview.png"],
        "cumulative_ic": ["cumulative_ic.png"],
        "group_return": sorted(p.name for p in plots_dir.glob("group_cumulative_return_*.png")) + ["group_return_bar.png"]
        if plots_dir.exists()
        else ["group_return_bar.png"],
        "layered_group_return": sorted(p.name for p in plots_dir.glob("group_return_bar_*.png")) if plots_dir.exists() else [],
        "long_short": ["long_short_curve.png"],
    }
    out: dict[str, SectionResult] = {}
    for section_name, table_names in table_map.items():
        tables = {}
        for table_name in table_names:
            path = tables_dir / f"{table_name}.csv"
            if path.exists():
                tables[table_name] = _read_section_table(path, table_name)
        plots = {}
        for plot_name in plot_map.get(section_name, []):
            path = plots_dir / plot_name
            if path.exists():
                plots[plot_name] = str(path)
        if tables or plots:
            out[section_name] = SectionResult(name=section_name, status="success", tables=tables, plots=plots)
    return out


def _read_section_table(path: Path, table_name: str) -> pd.DataFrame:
    if table_name == "daily_group_returns":
        df = pd.read_csv(path, index_col=[0, 1, 2])
        df.index = pd.MultiIndex.from_arrays(
            [
                pd.to_datetime(df.index.get_level_values(0)),
                df.index.get_level_values(1).astype(int),
                df.index.get_level_values(2).astype(int),
            ],
            names=["trade_date", "horizon", "group"],
        )
        return df
    if table_name == "layered_group_return_summary":
        df = pd.read_csv(path, index_col=[0, 1, 2])
        df.index = pd.MultiIndex.from_arrays(
            [
                df.index.get_level_values(0),
                df.index.get_level_values(1).astype(int),
                df.index.get_level_values(2).astype(int),
            ],
            names=["window", "horizon", "group"],
        )
        return df
    return read_table(path)


def _render_loaded_section_results(pool_dir: Path, sections: dict[str, SectionResult]) -> dict[str, SectionResult]:
    section_classes = {section.name: section for section in DEFAULT_SECTIONS}
    aligned_factor_path = pool_dir / "artifacts" / "aligned_factor.parquet"
    fallback_factor_path = aligned_factor_path.with_suffix(aligned_factor_path.suffix + ".pkl")
    if aligned_factor_path.exists():
        plot_index = read_table(aligned_factor_path).index
    elif fallback_factor_path.exists():
        plot_index = read_table(fallback_factor_path).index
    else:
        plot_index = None
    context = {
        "plots_dir": ensure_dir(pool_dir / "plots"),
        "plot_index": plot_index,
        "horizon_colors": BacktestConfig().horizon_colors,
    }
    rendered = {}
    for name, result in sections.items():
        section = section_classes.get(name)
        if section is None:
            rendered[name] = result
            continue
        try:
            rendered[name] = section.render(context, result)
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            rendered[name] = result
    return rendered


def _write_html_report(
    run_dir: Path,
    status: dict[str, dict[str, SectionResult]],
    *,
    meta: dict,
    warnings: list[str],
) -> None:
    rows = [_render_report_header(meta, warnings)]
    for pool, sections in status.items():
        rows.append(f"<section><h2>{escape(pool)}</h2>")
        rows.append(_render_pool_links(pool))
        rows.append(_render_key_plots(run_dir, sections))
        rows.append(_render_key_tables(sections))
        rows.append(_render_status_table(sections))
        rows.append("</section>")
    body = "\n".join(rows)
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>因子回测报告</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; color: #222; line-height: 1.45; }
    h1 { margin-bottom: 8px; }
    h2 { border-bottom: 2px solid #333; padding-bottom: 6px; margin-top: 32px; }
    h3 { margin-top: 22px; }
    table { border-collapse: collapse; width: 100%; margin: 12px 0 24px; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 7px 8px; text-align: left; vertical-align: top; }
    th { background: #f2f2f2; }
    img { max-width: 100%; border: 1px solid #ddd; margin: 8px 0 20px; }
    .meta, .links, .warning { background: #f7f7f7; padding: 12px 14px; margin: 12px 0; }
    .plot-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 18px; }
    .plot-card h4 { margin: 0 0 6px; }
    .muted { color: #666; }
    a { color: #245b9d; text-decoration: none; }
  </style>
</head>
<body>
  <h1>因子回测报告</h1>
  __BODY__
</body>
</html>
""".replace("__BODY__", body)
    (run_dir / "report.html").write_text(html, encoding="utf-8")


def _render_report_header(meta: dict, warnings: list[str]) -> str:
    meta_rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(str(value))}</td></tr>"
        for key, value in meta.items()
    )
    parts = [
        "<p class=\"muted\">本报告汇总单次回测中各股票池的关键图表、核心统计表和模块运行状态。"
        "完整中间结果仍保存在各 pool 目录下的 artifacts、tables 和 plots。</p>",
        f"<div class=\"meta\"><table>{meta_rows}</table></div>",
    ]
    if warnings:
        warning_items = "".join(f"<li>{escape(w)}</li>" for w in warnings)
        parts.append(f"<div class=\"warning\"><strong>Warnings</strong><ul>{warning_items}</ul></div>")
    return "\n".join(parts)


def _render_pool_links(pool: str) -> str:
    safe_pool = escape(pool)
    base = f"pools/{safe_pool}"
    return (
        "<div class=\"links\">"
        f"<a href=\"{base}/plots/\">plots</a> | "
        f"<a href=\"{base}/tables/\">tables</a> | "
        f"<a href=\"{base}/artifacts/\">artifacts</a>"
        "</div>"
    )


def _render_key_plots(run_dir: Path, sections: dict[str, SectionResult]) -> str:
    plot_order = [
        ("cumulative_ic", "cumulative_ic.png", "Cumulative RankIC"),
        ("ic_overview", "ic_overview.png", "20-Day Moving Average RankIC"),
        ("group_return", "group_cumulative_return_1d.png", "10-Group Cumulative Return 1D"),
        ("group_return", "group_cumulative_return_5d.png", "10-Group Cumulative Return 5D"),
        ("group_return", "group_cumulative_return_10d.png", "10-Group Cumulative Return 10D"),
        ("group_return", "group_cumulative_return_20d.png", "10-Group Cumulative Return 20D"),
        ("group_return", "group_return_bar.png", "10-Group Forward Returns"),
        ("layered_group_return", "group_return_bar_6m.png", "10-Group Forward Returns 6M"),
        ("layered_group_return", "group_return_bar_1y.png", "10-Group Forward Returns 1Y"),
        ("layered_group_return", "group_return_bar_3y.png", "10-Group Forward Returns 3Y"),
        ("layered_group_return", "group_return_bar_5y.png", "10-Group Forward Returns 5Y"),
        ("long_short", "long_short_curve.png", "Cumulative Long-Short Return"),
        ("data_quality", "data_quality_counts.png", "Factor Coverage Counts"),
        ("data_quality", "data_quality_ratios.png", "Factor Coverage and Invalid Value Ratios"),
    ]
    cards = []
    for section_name, plot_name, title in plot_order:
        result = sections.get(section_name)
        if result is None or plot_name not in result.plots:
            continue
        rel = _relative_report_path(run_dir, Path(result.plots[plot_name]))
        cards.append(
            f"<div class=\"plot-card\"><h4>{escape(title)}</h4>"
            f"<a href=\"{rel}\"><img src=\"{rel}\" alt=\"{escape(title)}\"></a></div>"
        )
    if not cards:
        return "<h3>关键图表</h3><p class=\"muted\">本次运行没有可嵌入图表，请检查 plots 目录或模块 warning。</p>"
    return "<h3>关键图表</h3><div class=\"plot-grid\">" + "\n".join(cards) + "</div>"


def _render_key_tables(sections: dict[str, SectionResult]) -> str:
    specs = [
        ("cumulative_ic", "ic_stats", "IC Statistics"),
        ("group_return", "group_return_summary", "Group Return Summary"),
        ("layered_group_return", "layered_group_return_summary", "Layered Group Return Summary"),
        ("long_short", "cumulative_long_short_returns", "Cumulative Long-Short Return Tail"),
        ("performance_metrics", "performance_metrics", "Performance Metrics"),
    ]
    parts = ["<h3>关键统计表</h3>"]
    rendered = 0
    for section_name, table_name, title in specs:
        result = sections.get(section_name)
        if result is None or table_name not in result.tables:
            continue
        table = result.tables[table_name]
        if table.empty:
            continue
        view = table.tail(10) if table_name == "cumulative_long_short_returns" else table
        parts.append(f"<h4>{escape(title)}</h4>")
        parts.append(_dataframe_to_html(view, max_rows=None if table_name != "cumulative_long_short_returns" else 20))
        rendered += 1
    if rendered == 0:
        parts.append("<p class=\"muted\">本次运行没有可展示的关键统计表，请检查 tables 目录或模块 warning。</p>")
    return "\n".join(parts)


def _render_status_table(sections: dict[str, SectionResult]) -> str:
    rows = ["<h3>模块状态</h3><table><thead><tr><th>模块</th><th>状态</th><th>错误</th><th>Warning</th></tr></thead><tbody>"]
    for name, result in sections.items():
        section_warnings = "<br>".join(escape(w) for w in result.warnings)
        rows.append(
            f"<tr><td>{escape(name)}</td><td>{escape(result.status)}</td>"
            f"<td>{escape(result.error or '')}</td><td>{section_warnings}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _dataframe_to_html(df: pd.DataFrame, *, max_rows: int | None = 20, max_cols: int | None = 12) -> str:
    return df.to_html(max_rows=max_rows, max_cols=max_cols, border=0, escape=True, float_format="{:.6g}".format)


def _relative_report_path(run_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _log(cfg: BacktestConfig, log_fn, message: str) -> None:
    if cfg.verbose:
        log_fn(message)
