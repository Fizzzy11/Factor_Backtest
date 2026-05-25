import json
import tempfile
from io import StringIO
from pathlib import Path

import pandas as pd

import factor_backtest.sections as sections_module
from factor_backtest.config import BacktestConfig
from factor_backtest.config import POOL_REGISTRY, PoolDefinition
from factor_backtest.io import read_table
from factor_backtest.market_data import MarketDataBundle
from factor_backtest.runner import (
    _write_html_report,
    render_factor_backtest_report,
    run_factor_backtest,
    run_factor_backtest_data,
    run_factor_backtest_minimal,
)
from factor_backtest.sections import GroupReturnSection, LayeredGroupReturnSection, LongShortSection, ReportSection, SectionResult, select_plot_title


class FailingSection(ReportSection):
    name = "failing"
    dependencies = []

    def compute(self, context):
        raise RuntimeError("intentional section failure")


class PassingSection(ReportSection):
    name = "passing"
    dependencies = []

    def compute(self, context):
        return SectionResult(name=self.name, status="success", tables={"ok": pd.DataFrame({"x": [1]})})


def test_runner_writes_pool_artifacts_and_isolates_section_failures():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(
            [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
        market = MarketDataBundle(open_price=open_price)
        cfg = BacktestConfig(
            output_root=tmp_path,
            selected_pools=["all"],
            horizons=[1],
            factor_name="factor_dm_20d",
            tradability_filter=False,
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=market,
            config=cfg,
            sections=[FailingSection(), PassingSection()],
        )

        assert result.run_dir.parent.name == "runs"
        assert result.run_dir.parent.parent.name == "factor_dm_20d"
        assert result.latest_dir == tmp_path / "factor_dm_20d" / "latest"
        assert (result.latest_dir / "report.html").exists()
        pool_dir = result.run_dir / "pools" / "all"
        assert _artifact_exists(pool_dir, "daily_ic.parquet")
        assert _artifact_exists(pool_dir, "daily_group_returns.parquet")
        meta = json.loads((result.run_dir / "run_meta.json").read_text(encoding="utf-8"))
        assert meta["framework_version"] == "v1"
        assert result.section_status["all"]["failing"].status == "failed"
        assert result.section_status["all"]["passing"].status == "success"


def _artifact_exists(pool_dir: Path, name: str) -> bool:
    path = pool_dir / "artifacts" / name
    return path.exists() or path.with_suffix(path.suffix + ".pkl").exists()


def test_runner_honors_enabled_sections_and_writes_chinese_report():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(
            [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
        cfg = BacktestConfig(
            output_root=tmp_path,
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["data_quality"],
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
        )

        assert list(result.section_status["all"]) == ["data_quality"]
        tables = result.section_status["all"]["data_quality"].tables
        assert list(tables["data_quality_counts"].columns) == ["pool_stock_count", "valid_factor_count"]
        assert set(tables["data_quality_ratios"].columns) == {
            "coverage_ratio",
            "zero_ratio",
            "nan_ratio",
            "inf_ratio",
        }
        report = (result.run_dir / "report.html").read_text(encoding="utf-8")
        assert "\u56e0\u5b50\u56de\u6d4b\u62a5\u544a" in report
        assert "\u6a21\u5757" in report
        assert "\u72b6\u6001" in report


def test_runner_verbose_prints_progress_and_quiet_mode_suppresses_it():
    dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
    symbols = [f"S{i}" for i in range(40)]
    factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
    open_price = pd.DataFrame(
        [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
        index=dates,
        columns=symbols,
        dtype=float,
    )

    with tempfile.TemporaryDirectory() as tmp:
        cfg = BacktestConfig(
            output_root=Path(tmp),
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["data_quality"],
            verbose=True,
        )
        out = StringIO()
        run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
            log_fn=lambda message: out.write(message + "\n"),
        )
        text = out.getvalue()
        assert "[v1] starting backtest: factor_dm_20d" in text
        assert "[v1] pool all: computing RankIC" in text
        assert "[v1] completed:" in text

    with tempfile.TemporaryDirectory() as tmp:
        cfg = BacktestConfig(
            output_root=Path(tmp),
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["data_quality"],
            verbose=False,
        )
        out = StringIO()
        run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
            log_fn=lambda message: out.write(message + "\n"),
        )
        assert out.getvalue() == ""


def test_runner_requires_tradability_inputs_when_filter_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        dates = pd.to_datetime(["2026-05-15", "2026-05-18"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame([[10 + i for i in range(40)], [11 + i for i in range(40)]], index=dates, columns=symbols)
        cfg = BacktestConfig(output_root=Path(tmp), selected_pools=["all"], horizons=[1], tradability_filter=True)

        try:
            run_factor_backtest(
                factor_df=factor,
                market_data=MarketDataBundle(open_price=open_price),
                config=cfg,
            )
        except ValueError as exc:
            assert "tradability_filter=True" in str(exc)
        else:
            raise AssertionError("Expected missing tradability data to raise ValueError")


def test_runner_applies_tradability_filter_on_next_trading_day():
    with tempfile.TemporaryDirectory() as tmp:
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(10.0, index=dates, columns=symbols)
        high_price = pd.DataFrame(10.0, index=dates, columns=symbols)
        low_price = pd.DataFrame(9.0, index=dates, columns=symbols)
        limit_up = pd.DataFrame(11.0, index=dates, columns=symbols)
        limit_down = pd.DataFrame(8.0, index=dates, columns=symbols)
        high_price.loc[dates[1], "S0"] = 11.0
        is_st = pd.DataFrame(0, index=dates, columns=symbols)
        is_suspended = pd.DataFrame(0, index=dates, columns=symbols)
        listed_days = pd.DataFrame(121, index=dates, columns=symbols)
        cfg = BacktestConfig(
            output_root=Path(tmp),
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=True,
            enabled_sections=[],
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                limit_up_price=limit_up,
                limit_down_price=limit_down,
                is_st=is_st,
                is_suspended=is_suspended,
                listed_days=listed_days,
            ),
            config=cfg,
        )

        valid_mask_path = result.run_dir / "pools" / "all" / "artifacts" / "valid_mask.parquet"
        valid_mask = read_table(valid_mask_path if valid_mask_path.exists() else valid_mask_path.with_suffix(".parquet.pkl"))
        assert bool(valid_mask.loc[dates[0], "S0"]) is False
        assert bool(valid_mask.loc[dates[1], "S0"]) is True


def test_plot_title_falls_back_to_english_without_cjk_font():
    chinese_title = "\u4e2d\u6587\u6807\u9898"
    assert select_plot_title(chinese_title, "English Title", has_cjk_font=False) == "English Title"
    assert select_plot_title(chinese_title, "English Title", has_cjk_font=True) == "English Title"


def test_long_short_section_plots_cumulative_series():
    dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
    daily = pd.DataFrame({"long_short_1d": [0.01, -0.02, 0.03]}, index=dates)
    section = LongShortSection()

    result = section.compute({"daily_long_short_returns": daily})

    assert result.tables["daily_long_short_returns"].equals(daily)
    expected = pd.DataFrame({"long_short_1d": [0.01, -0.01, 0.02]}, index=dates)
    pd.testing.assert_frame_equal(result.tables["cumulative_long_short_returns"], expected)


def test_layered_group_return_section_summarizes_windows():
    dates = pd.to_datetime(["2026-05-15", "2026-05-16", "2026-05-17"])
    records = []
    for date_idx, date in enumerate(dates):
        for group in [1, 2]:
            records.append(
                {
                    "trade_date": date,
                    "horizon": 1,
                    "group": group,
                    "group_return": float(date_idx + group),
                }
            )
    daily = pd.DataFrame(records).set_index(["trade_date", "horizon", "group"])
    section = LayeredGroupReturnSection()

    result = section.compute({"daily_group_returns": daily, "group_return_windows": {"last2": 2}})

    summary = result.tables["layered_group_return_summary"]
    assert summary.loc[("last2", 1, 1), "group_return"] == 2.5
    assert summary.loc[("last2", 1, 2), "group_return"] == 3.5


def test_layered_group_return_preserves_config_window_order():
    dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
    records = []
    for date_idx, date in enumerate(dates):
        for group in [1, 2]:
            records.append(
                {
                    "trade_date": date,
                    "horizon": 1,
                    "group": group,
                    "group_return": float(date_idx + group),
                }
            )
    daily = pd.DataFrame(records).set_index(["trade_date", "horizon", "group"])
    section = LayeredGroupReturnSection()

    result = section.compute(
        {"daily_group_returns": daily, "group_return_windows": {"6m": 2, "1y": 2, "3y": 2, "5y": 2}}
    )

    windows = result.tables["layered_group_return_summary"].index.get_level_values("window").unique().tolist()
    assert windows == ["6m", "1y", "3y", "5y"]


def test_group_return_renders_combined_bar_and_horizon_cumulative_line_charts():
    with tempfile.TemporaryDirectory() as tmp:
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        daily = pd.DataFrame(
            {"group_return": [0.01, 0.02, 0.03, 0.04]},
            index=pd.MultiIndex.from_tuples(
                [
                    (dates[0], 1, 1),
                    (dates[0], 1, 2),
                    (dates[0], 5, 1),
                    (dates[0], 5, 2),
                ],
                names=["trade_date", "horizon", "group"],
            ),
        )
        section = GroupReturnSection()
        result = section.compute({"daily_group_returns": daily})
        original_plot_bars = sections_module._plot_bars
        original_plot_lines = sections_module._plot_lines
        try:
            sections_module._plot_bars = lambda df, path, title, result, **kwargs: result.plots.update({Path(path).name: str(path)})
            sections_module._plot_lines = lambda df, path, title, result, **kwargs: result.plots.update({Path(path).name: str(path)})
            result = section.render(
                {
                    "plots_dir": Path(tmp),
                    "horizon_colors": {1: "#111111", 5: "#222222"},
                    "plot_index": dates,
                },
                result,
            )
        finally:
            sections_module._plot_bars = original_plot_bars
            sections_module._plot_lines = original_plot_lines

    assert "group_return_bar.png" in result.plots
    assert "group_cumulative_return_1d.png" in result.plots
    assert "group_cumulative_return_5d.png" in result.plots
    assert "group_return_horizon_1d.png" not in result.plots
    assert "group_cumulative_returns_1d" in result.tables
    assert "group_cumulative_returns_5d" in result.tables
    assert result.tables["group_cumulative_returns_1d"].loc[pd.Timestamp("2026-05-15"), "G1"] == 1.01
    assert list(result.tables["group_cumulative_returns_5d"].index) == list(dates)
    expected_5d_first = (1.03) ** (1 / 5)
    assert result.tables["group_cumulative_returns_5d"].loc[dates[0], "G1"] == expected_5d_first
    assert result.tables["group_cumulative_returns_5d"].loc[dates[1], "G1"] == expected_5d_first
    assert result.tables["group_cumulative_returns_5d"].loc[dates[2], "G1"] == expected_5d_first


def test_group_colors_use_ordered_red_to_blue_palette():
    colors = sections_module._group_colors(10)

    assert colors == [
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


def test_layered_group_return_renders_one_chart_per_window():
    with tempfile.TemporaryDirectory() as tmp:
        summary = pd.DataFrame(
            {
                "window_size": [2, 2, 2, 2],
                "end_date": pd.to_datetime(["2026-05-17"] * 4),
                "group_return": [0.01, 0.02, 0.03, 0.04],
            },
            index=pd.MultiIndex.from_tuples(
                [
                    ("last2", 1, 1),
                    ("last2", 1, 2),
                    ("last2", 5, 1),
                    ("last2", 5, 2),
                ],
                names=["window", "horizon", "group"],
            ),
        )
        section = LayeredGroupReturnSection()
        result = SectionResult(
            name="layered_group_return",
            status="success",
            tables={"layered_group_return_summary": summary},
        )

        original_plot_bars = sections_module._plot_bars
        try:
            sections_module._plot_bars = lambda df, path, title, result, **kwargs: result.plots.update({Path(path).name: str(path)})
            result = section.render({"plots_dir": Path(tmp), "horizon_colors": {1: "#111111", 5: "#222222"}}, result)
        finally:
            sections_module._plot_bars = original_plot_bars

    assert "group_return_bar_last2.png" in result.plots
    assert not any(name.startswith("layered_group_return_last2_") for name in result.plots)


def test_minimal_backtest_returns_one_summary_row_per_pool():
    dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
    symbols = [f"S{i}" for i in range(40)]
    factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
    open_price = pd.DataFrame(
        [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
        index=dates,
        columns=symbols,
        dtype=float,
    )
    cfg = BacktestConfig(
        factor_name="factor_dm_20d",
        selected_pools=["all"],
        horizons=[1],
        tradability_filter=False,
    )

    summary = run_factor_backtest_minimal(
        factor_df=factor,
        market_data=MarketDataBundle(open_price=open_price),
        config=cfg,
    )

    assert ("factor_dm_20d", "all") in summary.index
    assert "ic_mean_1d" in summary.columns
    assert "coverage_mean" in summary.columns


def test_html_report_embeds_key_tables_and_plot_links():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        plot_dir = run_dir / "pools" / "all" / "plots"
        plot_dir.mkdir(parents=True)
        plot_path = plot_dir / "cumulative_ic.png"
        plot_path.write_bytes(b"fake image")

        status = {
            "all": {
                "cumulative_ic": SectionResult(
                    name="cumulative_ic",
                    status="success",
                    tables={"ic_stats": pd.DataFrame({"ic_mean": [0.03]}, index=["1d"])},
                    plots={"cumulative_ic.png": str(plot_path)},
                ),
                "group_return": SectionResult(
                    name="group_return",
                    status="success",
                    tables={"group_return_summary": pd.DataFrame({1: [0.01]}, index=[1])},
                    plots={"group_cumulative_return_1d.png": str(plot_dir / "group_cumulative_return_1d.png")},
                ),
                "performance_metrics": SectionResult(
                    name="performance_metrics",
                    status="success",
                    tables={"performance_metrics": pd.DataFrame({"sharpe": [1.2]}, index=["long_short_1d"])},
                ),
            }
        }

        _write_html_report(
            run_dir,
            status,
            meta={"factor_name": "factor_dm_20d", "horizons": [1]},
            warnings=["sample warning"],
        )

        html = (run_dir / "report.html").read_text(encoding="utf-8")
        assert "关键图表" in html
        assert "pools/all/plots/cumulative_ic.png" in html
        assert "pools/all/plots/group_cumulative_return_1d.png" in html
        assert "IC Statistics" in html
        assert "Performance Metrics" in html
        assert "sample warning" in html


def test_html_report_orders_key_plots_by_analysis_flow():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        plot_dir = run_dir / "pools" / "all" / "plots"
        plot_dir.mkdir(parents=True)
        plot_specs = [
            ("ic_overview", "ic_overview.png"),
            ("cumulative_ic", "cumulative_ic.png"),
            ("group_return", "group_cumulative_return_1d.png"),
            ("group_return", "group_cumulative_return_5d.png"),
            ("group_return", "group_cumulative_return_10d.png"),
            ("group_return", "group_cumulative_return_20d.png"),
            ("group_return", "group_return_bar.png"),
            ("layered_group_return", "group_return_bar_6m.png"),
            ("layered_group_return", "group_return_bar_1y.png"),
            ("layered_group_return", "group_return_bar_3y.png"),
            ("layered_group_return", "group_return_bar_5y.png"),
            ("long_short", "long_short_curve.png"),
            ("data_quality", "data_quality_counts.png"),
            ("data_quality", "data_quality_ratios.png"),
        ]
        status = {"all": {}}
        for section_name, plot_name in plot_specs:
            path = plot_dir / plot_name
            path.write_bytes(b"fake image")
            status["all"].setdefault(
                section_name,
                SectionResult(name=section_name, status="success", plots={}),
            ).plots[plot_name] = str(path)

        _write_html_report(run_dir, status, meta={"factor_name": "factor_dm_20d"}, warnings=[])

        html = (run_dir / "report.html").read_text(encoding="utf-8")
        expected_titles = [
            "Cumulative RankIC",
            "20-Day Moving Average RankIC",
            "10-Group Cumulative Return 1D",
            "10-Group Cumulative Return 5D",
            "10-Group Cumulative Return 10D",
            "10-Group Cumulative Return 20D",
            "10-Group Forward Returns",
            "10-Group Forward Returns 6M",
            "10-Group Forward Returns 1Y",
            "10-Group Forward Returns 3Y",
            "10-Group Forward Returns 5Y",
            "Cumulative Long-Short Return",
            "Factor Coverage Counts",
            "Factor Coverage and Invalid Value Ratios",
        ]
        positions = [html.index(title) for title in expected_titles]
        assert positions == sorted(positions)


def test_plot_helpers_use_larger_figures():
    assert sections_module.LINE_FIGSIZE == (14, 6)
    assert sections_module.BAR_FIGSIZE == (14, 6)
    assert sections_module.BAR_WIDTH == 0.85


def test_html_report_does_not_truncate_layered_group_return_summary():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        records = []
        for window in ["6m", "1y", "3y", "5y"]:
            for horizon in [1, 5, 10, 20]:
                for group in range(1, 11):
                    records.append(
                        {
                            "window": window,
                            "horizon": horizon,
                            "group": group,
                            "window_size": 120,
                            "end_date": "2026-05-21",
                            "group_return": horizon + group / 100,
                        }
                    )
        layered = pd.DataFrame(records).set_index(["window", "horizon", "group"]).sort_index()
        status = {
            "all": {
                "layered_group_return": SectionResult(
                    name="layered_group_return",
                    status="success",
                    tables={"layered_group_return_summary": layered},
                )
            }
        }

        _write_html_report(run_dir, status, meta={"factor_name": "factor_dm_20d"}, warnings=[])

        html = (run_dir / "report.html").read_text(encoding="utf-8")
        assert "Layered Group Return Summary" in html
        assert "<td>...</td>" not in html
        assert html.count("<tr>") >= len(layered) + 1


def test_render_report_from_existing_result_files():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(
            [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
        cfg = BacktestConfig(
            output_root=tmp_path,
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["cumulative_ic", "performance_metrics"],
        )
        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
        )

        report_path = render_factor_backtest_report(result.run_dir)

        assert report_path == result.run_dir / "report.html"
        html = report_path.read_text(encoding="utf-8")
        assert "IC Statistics" in html
        assert "Performance Metrics" in html


def test_data_only_run_can_be_rendered_later():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(
            [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
        cfg = BacktestConfig(
            output_root=tmp_path,
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections=["cumulative_ic", "group_return", "layered_group_return", "long_short"],
        )

        result = run_factor_backtest_data(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
        )

        assert result.section_status["all"]["cumulative_ic"].plots == {}
        report_path = render_factor_backtest_report(result.run_dir)
        assert report_path.exists()
        plot_names = {path.name for path in (result.run_dir / "pools" / "all" / "plots").glob("*.png")}
        assert "group_cumulative_return_1d.png" in plot_names
        assert not any(name.startswith("group_return_horizon_") for name in plot_names)
        html = report_path.read_text(encoding="utf-8")
        assert "Cumulative RankIC" in html
        assert "group_cumulative_return_1d.png" in html
        assert "group_return_horizon_" not in html
        assert "Layered Group Return Summary" in html


def test_latest_report_uses_latest_pool_all_relative_plot_paths():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.bdate_range("2026-05-01", periods=30)
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40) for _ in dates], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(
            [[10 + i + date_idx * 0.1 for i in range(40)] for date_idx in range(len(dates))],
            index=dates,
            columns=symbols,
            dtype=float,
        )
        cfg = BacktestConfig(
            output_root=tmp_path,
            factor_name="factor_dm_20d",
            selected_pools=["all"],
            horizons=[1, 5, 10, 20],
            tradability_filter=False,
            enabled_sections=["group_return"],
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
        )

        latest_report = result.latest_dir / "report.html"
        html = latest_report.read_text(encoding="utf-8")
        expected = "pools/all/plots/group_cumulative_return_20d.png"
        assert expected in html
        assert "runs/" not in html
        assert (result.latest_dir / expected).exists()


def test_runner_warns_when_dynamic_pool_dates_do_not_cover_factor_window():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pool_path = tmp_path / "tmp_pool.csv"
        pool_path.write_text(
            "trade_date,symbol\n"
            "2026-05-18,S0\n"
            "2026-05-18,S1\n",
            encoding="utf-8",
        )
        POOL_REGISTRY["tmp_pool"] = PoolDefinition(path=pool_path, display_name="tmp pool")
        try:
            dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
            symbols = [f"S{i}" for i in range(40)]
            factor = pd.DataFrame([range(40), range(40), range(40)], index=dates, columns=symbols, dtype=float)
            open_price = pd.DataFrame(
                [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
                index=dates,
                columns=symbols,
                dtype=float,
            )
            cfg = BacktestConfig(
                output_root=tmp_path,
                selected_pools=["tmp_pool"],
                horizons=[1],
                tradability_filter=False,
            )

            result = run_factor_backtest(
                factor_df=factor,
                market_data=MarketDataBundle(open_price=open_price),
                config=cfg,
            )

            log = json.loads((result.run_dir / "run_log.json").read_text(encoding="utf-8"))
            assert any("tmp_pool" in warning and "2026-05-18" in warning for warning in log["warnings"])
        finally:
            POOL_REGISTRY.pop("tmp_pool", None)
