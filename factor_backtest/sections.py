from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import pandas as pd

from factor_backtest.config import DEFAULT_HORIZON_COLORS

from factor_backtest.analytics import compute_ic_stats, compute_performance_metrics

LINE_FIGSIZE = (14, 6)
BAR_FIGSIZE = (14, 6)
BAR_WIDTH = 0.85


@dataclass
class SectionResult:
    name: str
    status: str
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    plots: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class ReportSection:
    name = "base"
    dependencies: list[str] = []

    def compute(self, context) -> SectionResult:
        raise NotImplementedError

    def render(self, context, result: SectionResult) -> SectionResult:
        return result


class DataQualitySection(ReportSection):
    name = "data_quality"

    def compute(self, context) -> SectionResult:
        quality = context["data_quality"]
        count_cols = [col for col in ("pool_stock_count", "valid_factor_count") if col in quality.columns]
        ratio_cols = [col for col in quality.columns if col.endswith("_ratio")]
        tables = {"data_quality": quality}
        if count_cols:
            tables["data_quality_counts"] = quality[count_cols]
        if ratio_cols:
            tables["data_quality_ratios"] = quality[ratio_cols]
        return SectionResult(name=self.name, status="success", tables=tables)

    def render(self, context, result: SectionResult) -> SectionResult:
        if "data_quality_counts" in result.tables:
            _plot_lines(
                result.tables["data_quality_counts"],
                context["plots_dir"] / "data_quality_counts.png",
                "Factor Coverage Counts",
                result,
            )
        if "data_quality_ratios" in result.tables:
            _plot_lines(
                result.tables["data_quality_ratios"],
                context["plots_dir"] / "data_quality_ratios.png",
                "Factor Coverage and Invalid Value Ratios",
                result,
                ylim=(0, 1),
            )
        return result


class CumulativeICSection(ReportSection):
    name = "cumulative_ic"

    def compute(self, context) -> SectionResult:
        ic = context["daily_ic"]
        tables = {"daily_ic": ic, "cumulative_ic": ic.cumsum(), "ic_stats": compute_ic_stats(ic)}
        return SectionResult(name=self.name, status="success", tables=tables)

    def render(self, context, result: SectionResult) -> SectionResult:
        _plot_lines(
            result.tables["cumulative_ic"],
            context["plots_dir"] / "cumulative_ic.png",
            "Cumulative RankIC",
            result,
            horizon_colors=context.get("horizon_colors"),
        )
        return result


class ICOverviewSection(ReportSection):
    name = "ic_overview"

    def compute(self, context) -> SectionResult:
        ic = context["daily_ic"]
        col = "ic_20d" if "ic_20d" in ic.columns else ic.columns[-1]
        overview = ic[[col]].rolling(20, min_periods=1).mean()
        return SectionResult(name=self.name, status="success", tables={"ic_overview": overview})

    def render(self, context, result: SectionResult) -> SectionResult:
        _plot_lines(
            result.tables["ic_overview"],
            context["plots_dir"] / "ic_overview.png",
            "20-Day Moving Average RankIC",
            result,
            horizon_colors=context.get("horizon_colors"),
        )
        return result


class GroupReturnSection(ReportSection):
    name = "group_return"

    def compute(self, context) -> SectionResult:
        return SectionResult(name=self.name, status="success", tables={"daily_group_returns": context["daily_group_returns"]})

    def render(self, context, result: SectionResult) -> SectionResult:
        daily = result.tables["daily_group_returns"]
        if not daily.empty:
            summary = daily.groupby(["horizon", "group"])["group_return"].mean().unstack("horizon")
            result.tables["group_return_summary"] = summary
            _plot_bars(
                summary,
                context["plots_dir"] / "group_return_bar.png",
                "10-Group Forward Returns",
                result,
                horizon_colors=context.get("horizon_colors"),
            )
            for horizon in sorted(daily.index.get_level_values("horizon").unique()):
                cumulative = _group_cumulative_return_table(daily, int(horizon), plot_index=context.get("plot_index"))
                table_name = f"group_cumulative_returns_{int(horizon)}d"
                result.tables[table_name] = cumulative
                _plot_lines(
                    cumulative,
                    context["plots_dir"] / f"group_cumulative_return_{int(horizon)}d.png",
                    f"10-Group Cumulative Return {int(horizon)}D",
                    result,
                    colors=_group_colors(len(cumulative.columns)),
                    linewidth=1.8,
                    zero_line=False,
                )
        return result


class LayeredGroupReturnSection(ReportSection):
    name = "layered_group_return"

    def compute(self, context) -> SectionResult:
        daily = context["daily_group_returns"]
        windows = context.get("group_return_windows", {})
        if daily.empty:
            return SectionResult(name=self.name, status="success", tables={"layered_group_return_summary": pd.DataFrame()})
        records = []
        dates = daily.index.get_level_values("trade_date")
        max_date = dates.max()
        for window_name, window_size in windows.items():
            window_dates = sorted(pd.unique(dates))[-int(window_size) :]
            window_data = daily.loc[daily.index.get_level_values("trade_date").isin(window_dates)]
            summary = window_data.groupby(["horizon", "group"])["group_return"].mean()
            for (horizon, group), value in summary.items():
                records.append(
                    {
                        "window": window_name,
                        "window_size": int(window_size),
                        "end_date": max_date,
                        "horizon": int(horizon),
                        "group": int(group),
                        "group_return": float(value),
                    }
                )
        table = (
            pd.DataFrame(records).set_index(["window", "horizon", "group"])
            if records
            else pd.DataFrame(columns=["window_size", "end_date", "group_return"])
        )
        return SectionResult(name=self.name, status="success", tables={"layered_group_return_summary": table})

    def render(self, context, result: SectionResult) -> SectionResult:
        summary = result.tables["layered_group_return_summary"]
        if summary.empty:
            return result
        for window in summary.index.get_level_values("window").unique():
            window_data = summary[summary.index.get_level_values("window") == window]
            window_summary = window_data["group_return"].unstack("horizon")
            window_summary = window_summary.sort_index().sort_index(axis=1)
            _plot_bars(
                window_summary,
                context["plots_dir"] / f"group_return_bar_{window}.png",
                f"10-Group Forward Returns {window}",
                result,
                horizon_colors=context.get("horizon_colors"),
            )
        return result


class LongShortSection(ReportSection):
    name = "long_short"

    def compute(self, context) -> SectionResult:
        daily = context["daily_long_short_returns"]
        cumulative = daily.cumsum() if not daily.empty else daily
        return SectionResult(
            name=self.name,
            status="success",
            tables={
                "daily_long_short_returns": daily,
                "cumulative_long_short_returns": cumulative,
            },
        )

    def render(self, context, result: SectionResult) -> SectionResult:
        _plot_lines(
            result.tables["cumulative_long_short_returns"],
            context["plots_dir"] / "long_short_curve.png",
            "Cumulative Long-Short Return",
            result,
            horizon_colors=context.get("horizon_colors"),
        )
        return result


class PerformanceMetricsSection(ReportSection):
    name = "performance_metrics"

    def compute(self, context) -> SectionResult:
        metrics = compute_performance_metrics(context["daily_long_short_returns"])
        return SectionResult(name=self.name, status="success", tables={"performance_metrics": metrics})


DEFAULT_SECTIONS: list[ReportSection] = [
    DataQualitySection(),
    ICOverviewSection(),
    CumulativeICSection(),
    GroupReturnSection(),
    LayeredGroupReturnSection(),
    LongShortSection(),
    PerformanceMetricsSection(),
]


def select_plot_title(chinese_title: str, english_title: str, has_cjk_font: bool | None = None) -> str:
    return english_title


def _plot_lines(
    df: pd.DataFrame,
    path,
    title: str,
    result: SectionResult,
    *,
    horizon_colors: dict[int, str] | None = None,
    colors: list[str] | None = None,
    ylim: tuple[float, float] | None = None,
    linewidth: float | None = None,
    zero_line: bool = True,
) -> None:
    if df.empty:
        result.warnings.append(f"{title} has no plottable data")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        path.parent.mkdir(parents=True, exist_ok=True)
        plot_colors = colors if colors is not None else _colors_for_columns(df.columns, horizon_colors)
        plot_kwargs = {"figsize": LINE_FIGSIZE, "title": title, "color": plot_colors}
        if linewidth is not None:
            plot_kwargs["linewidth"] = linewidth
        ax = df.plot(**plot_kwargs)
        if zero_line:
            ax.axhline(0, color="#333333", linewidth=0.8)
        if ylim is not None:
            ax.set_ylim(*ylim)
        ax.figure.tight_layout()
        ax.figure.savefig(path, dpi=150)
        plt.close(ax.figure)
        result.plots[path.name] = str(path)
    except Exception as exc:
        result.warnings.append(f"{title} plotting failed: {exc}")


def _plot_bars(
    df: pd.DataFrame,
    path,
    title: str,
    result: SectionResult,
    *,
    horizon_colors: dict[int, str] | None = None,
) -> None:
    if df.empty:
        result.warnings.append(f"{title} has no plottable data")
        return
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        path.parent.mkdir(parents=True, exist_ok=True)
        colors = _colors_for_columns(df.columns, horizon_colors)
        ax = df.plot(kind="bar", figsize=BAR_FIGSIZE, title=title, color=colors, width=BAR_WIDTH)
        ax.axhline(0, color="#333333", linewidth=0.8)
        ax.figure.tight_layout()
        ax.figure.savefig(path, dpi=150)
        plt.close(ax.figure)
        result.plots[path.name] = str(path)
    except Exception as exc:
        result.warnings.append(f"{title} plotting failed: {exc}")


def _colors_for_columns(columns, horizon_colors: dict[int, str] | None = None) -> list[str]:
    colors = horizon_colors or DEFAULT_HORIZON_COLORS
    fallback = ["#4C78A8", "#72B7B2", "#F58518", "#54A24B", "#B279A2", "#E45756", "#9D755D", "#BAB0AC"]
    out = []
    for idx, col in enumerate(columns):
        horizon = _extract_horizon(col)
        out.append(colors.get(horizon, fallback[idx % len(fallback)]))
    return out


def _group_cumulative_return_table(
    daily_group_returns: pd.DataFrame,
    horizon: int,
    plot_index: pd.Index | None = None,
) -> pd.DataFrame:
    horizon_data = daily_group_returns.xs(horizon, level="horizon")["group_return"]
    wide = horizon_data.unstack("group").sort_index().sort_index(axis=1)
    wide = wide.rename(columns={group: f"G{int(group)}" for group in wide.columns})
    if plot_index is not None:
        wide = wide.reindex(pd.DatetimeIndex(pd.to_datetime(plot_index)))
    daily_equivalent = _horizon_return_to_daily_equivalent(wide, horizon)
    return (1.0 + daily_equivalent.fillna(0.0)).cumprod()


def _horizon_return_to_daily_equivalent(returns: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if horizon <= 1:
        return returns
    valid = returns.where(returns > -1.0)
    return (1.0 + valid) ** (1.0 / horizon) - 1.0


def _group_colors(n: int) -> list[str]:
    palette = [
        "#9E0142",
        "#D53E4F",
        "#F46D43",
        "#FDAE61",
        "#FEE08B",
        "#E0F3F8",
        "#ABD9E9",
        "#74ADD1",
        "#4575B4",
        "#313695",
    ]
    return [palette[idx % len(palette)] for idx in range(n)]


def _extract_horizon(value) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    match = re.search(r"(\d+)d\b", str(value))
    return int(match.group(1)) if match else None
