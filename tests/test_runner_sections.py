import json
import tempfile
import warnings
from html.parser import HTMLParser
from io import StringIO
from pathlib import Path

import pandas as pd

import factor_backtest.sections as sections_module
from factor_backtest.config import BacktestConfig, DataSourceConfig, PathConfig
from factor_backtest.config import POOL_REGISTRY, PoolDefinition
from factor_backtest.risk_exposure import DEFAULT_STYLE_COLUMNS
from factor_backtest.io import read_table
from factor_backtest.market_data import MarketDataBundle
from factor_backtest.runner import (
    _write_html_report,
    render_factor_backtest_report,
    run_factor_backtest,
    run_factor_backtest_data,
    run_factor_backtest_minimal,
)
from factor_backtest.sections import (
    GroupExposureDiagnosticsSection,
    GroupReturnSection,
    GroupTurnoverSection,
    LayeredGroupReturnSection,
    LongShortSection,
    ReportSection,
    SectionResult,
    select_plot_title,
)


class _ImgSrcParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            attrs_by_name = dict(attrs)
            if attrs_by_name.get("src"):
                self.sources.append(attrs_by_name["src"])


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


def test_runner_supports_multiple_ic_methods_with_compatibility_outputs():
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
            selected_pools=["all"],
            horizons=[1],
            factor_name="factor_dm_20d",
            tradability_filter=False,
            ic_methods=["spearman", "pearson"],
            enabled_sections=["cumulative_ic", "ic_overview"],
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
        )

        pool_dir = result.run_dir / "pools" / "all"
        assert _artifact_exists(pool_dir, "daily_ic.parquet")
        assert _artifact_exists(pool_dir, "daily_ic_spearman.parquet")
        assert _artifact_exists(pool_dir, "daily_ic_pearson.parquet")
        assert _table_exists(pool_dir, "ic_stats.csv")
        assert _table_exists(pool_dir, "ic_stats_spearman.csv")
        assert _table_exists(pool_dir, "ic_stats_pearson.csv")
        assert (pool_dir / "plots" / "cumulative_ic_spearman.png").exists()
        assert (pool_dir / "plots" / "cumulative_ic_pearson.png").exists()
        assert (pool_dir / "plots" / "ic_overview_spearman.png").exists()
        assert (pool_dir / "plots" / "ic_overview_pearson.png").exists()
        meta = json.loads((result.run_dir / "run_meta.json").read_text(encoding="utf-8"))
        assert meta["ic_methods"] == ["spearman", "pearson"]
        expected_ic_plots = {
            "pools/all/plots/cumulative_ic_spearman.png",
            "pools/all/plots/cumulative_ic_pearson.png",
            "pools/all/plots/ic_overview_spearman.png",
            "pools/all/plots/ic_overview_pearson.png",
        }
        for report_path in (result.run_dir / "report.html", result.latest_dir / "report.html"):
            html = report_path.read_text(encoding="utf-8")
            assert "Cumulative Spearman RankIC" in html
            assert "Cumulative Pearson IC" in html
            assert "20-Day Moving Average Spearman RankIC" in html
            assert "20-Day Moving Average Pearson IC" in html
            image_sources = _html_image_sources(html)
            assert expected_ic_plots.issubset(set(image_sources))
            assert all(not Path(src).is_absolute() for src in image_sources)
            assert all((report_path.parent / src).exists() for src in image_sources)


def test_runner_renders_risk_exposure_sections_when_csv_source_is_configured():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.bdate_range("2026-01-02", periods=14)
        symbols = [f"S{i:03d}" for i in range(24)]
        factor = pd.DataFrame(
            [[float(i + day) for i, _ in enumerate(symbols)] for day, _ in enumerate(dates)],
            index=dates,
            columns=symbols,
        )
        open_price = pd.DataFrame(
            [[10.0 + i * 0.1 + day * 0.02 for i, _ in enumerate(symbols)] for day, _ in enumerate(dates)],
            index=dates,
            columns=symbols,
        )
        risk_rows = []
        for day, date in enumerate(dates):
            for i, symbol in enumerate(symbols):
                row = {"date": date, "symbol": symbol}
                for style in DEFAULT_STYLE_COLUMNS:
                    row[style] = float(i + day)
                row["comovement"] = 1.0
                row["银行"] = 1 if i < 12 else 0
                row["计算机"] = 1 if i >= 12 else 0
                risk_rows.append(row)
        risk_path = tmp_path / "risk&industry" / "CNE5&Industry.csv"
        risk_path.parent.mkdir()
        pd.DataFrame(risk_rows).to_csv(risk_path, index=False)
        cfg = BacktestConfig(
            paths=PathConfig(data_root=tmp_path, pool_dir=tmp_path / "pool"),
            data_sources=DataSourceConfig(risk_exposure_source="csv"),
            output_root=tmp_path / "out",
            selected_pools=["all"],
            horizons=[1],
            factor_name="factor_dm_20d",
            tradability_filter=False,
            ic_methods=["spearman", "pearson"],
            min_industry_ic_stocks=4,
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
            log_fn=lambda *_: None,
        )

        pool_dir = result.run_dir / "pools" / "all"
        assert _table_exists(pool_dir, "factor_style_exposure_corr_spearman.csv")
        assert _table_exists(pool_dir, "factor_style_exposure_corr_summary_spearman.csv")
        assert _table_exists(pool_dir, "style_neutralized_ic_stats_spearman.csv")
        assert _table_exists(pool_dir, "style_industry_neutralized_ic_stats_spearman.csv")
        assert _table_exists(pool_dir, "within_industry_group_return_summary.csv")
        assert (pool_dir / "plots" / "factor_style_exposure_corr_spearman.png").exists()
        assert (pool_dir / "plots" / "cumulative_style_neutralized_ic_spearman.png").exists()
        assert (pool_dir / "plots" / "cumulative_style_industry_neutralized_ic_spearman.png").exists()
        assert (pool_dir / "plots" / "within_industry_group_return_bar.png").exists()
        html = (result.latest_dir / "report.html").read_text(encoding="utf-8")
        assert "Factor Style Exposure Correlation Spearman" in html
        assert "Pearson Style Exposure Correlation Summary" in html
        assert "Cumulative Style Neutralized Spearman RankIC" in html
        assert "Style Neutralized Pearson IC Statistics" in html
        assert "Cumulative Style + Industry Neutralized Spearman RankIC" in html
        assert "Style + Industry Neutralized Pearson IC Statistics" in html
        assert "Within-Industry 10-Group Forward Returns" in html
        rerendered = render_factor_backtest_report(result.latest_dir)
        rerendered_html = rerendered.read_text(encoding="utf-8")
        assert "Factor Style Exposure Correlation Spearman" in rerendered_html
        assert "Cumulative Style Neutralized Spearman RankIC" in rerendered_html
        assert "Within-Industry 10-Group Forward Returns" in rerendered_html
        assert "Group Style Exposure G1" in rerendered_html
        assert "Group Turnover Edge Summary" in rerendered_html


def test_group_exposure_and_turnover_sections_render_edge_group_outputs():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.bdate_range("2026-01-02", periods=14)
        symbols = [f"S{i:03d}" for i in range(40)]
        factor = pd.DataFrame(
            [[float((i + day) % 40) for i, _ in enumerate(symbols)] for day, _ in enumerate(dates)],
            index=dates,
            columns=symbols,
        )
        open_price = pd.DataFrame(
            [[10.0 + i * 0.1 + day * 0.02 for i, _ in enumerate(symbols)] for day, _ in enumerate(dates)],
            index=dates,
            columns=symbols,
        )
        risk_rows = []
        for day, date in enumerate(dates):
            for i, symbol in enumerate(symbols):
                row = {"date": date, "symbol": symbol}
                for style in DEFAULT_STYLE_COLUMNS:
                    row[style] = float(i + day)
                row["bank"] = 1 if i < 20 else 0
                row["tech"] = 1 if i >= 20 else 0
                risk_rows.append(row)
        risk_path = tmp_path / "risk&industry" / "CNE5&Industry.csv"
        risk_path.parent.mkdir()
        pd.DataFrame(risk_rows).to_csv(risk_path, index=False)
        cfg = BacktestConfig(
            paths=PathConfig(data_root=tmp_path, pool_dir=tmp_path / "pool"),
            data_sources=DataSourceConfig(risk_exposure_source="csv"),
            output_root=tmp_path / "out",
            selected_pools=["all"],
            horizons=[1],
            factor_name="factor_dm_20d",
            tradability_filter=False,
            enabled_sections=["group_exposure_diagnostics", "group_turnover"],
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
            log_fn=lambda *_: None,
        )

        pool_dir = result.run_dir / "pools" / "all"
        assert _table_exists(pool_dir, "group_style_exposure_daily.csv")
        assert _table_exists(pool_dir, "group_industry_exposure_daily.csv")
        assert _table_exists(pool_dir, "daily_group_turnover.csv")
        assert _table_exists(pool_dir, "group_turnover_edge_summary.csv")
        assert (pool_dir / "plots" / "group_style_exposure_g1.png").exists()
        assert (pool_dir / "plots" / "group_style_exposure_g10.png").exists()
        assert (pool_dir / "plots" / "group_industry_exposure_g1.png").exists()
        assert (pool_dir / "plots" / "group_industry_exposure_g10.png").exists()
        assert (pool_dir / "plots" / "group_turnover_edges.png").exists()
        style_daily = result.section_status["all"]["group_exposure_diagnostics"].tables["group_style_exposure_daily"]
        assert {"G1", "G10", "G10_minus_G1"}.issubset(set(style_daily.index.get_level_values("leg")))
        turnover_edge = result.section_status["all"]["group_turnover"].tables["group_turnover_edge_summary"]
        assert {"G1", "G10", "edge_avg"}.issubset(set(turnover_edge.index))
        html = (result.latest_dir / "report.html").read_text(encoding="utf-8")
        assert "Group Style Exposure G1" in html
        assert "Group Style Exposure G10" in html
        assert "Group Turnover Edge Summary" in html


def test_group_exposure_industry_plots_use_ascii_labels_for_cjk_columns():
    with tempfile.TemporaryDirectory() as tmp:
        date = pd.Timestamp("2026-01-02")
        industry_daily = pd.DataFrame(
            {"value": [0.3, 0.7]},
            index=pd.MultiIndex.from_tuples(
                [(date, "G1", "银行"), (date, "G1", "计算机")],
                names=["trade_date", "leg", "industry"],
            ),
        )
        result = SectionResult(
            name="group_exposure_diagnostics",
            status="success",
            tables={
                "group_style_exposure_daily": pd.DataFrame(),
                "group_industry_exposure_daily": industry_daily,
            },
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = GroupExposureDiagnosticsSection().render({"plots_dir": Path(tmp)}, result)

    assert not any("Glyph" in str(warning.message) for warning in caught)
    assert "group_industry_exposure_plot_label_map" in result.tables
    label_map = result.tables["group_industry_exposure_plot_label_map"]
    assert set(label_map["industry"]) == {"银行", "计算机"}


def _artifact_exists(pool_dir: Path, name: str) -> bool:
    path = pool_dir / "artifacts" / name
    return path.exists() or path.with_suffix(path.suffix + ".pkl").exists()


def _table_exists(pool_dir: Path, name: str) -> bool:
    path = pool_dir / "tables" / name
    return path.exists() or path.with_suffix(path.suffix + ".pkl").exists()


def _html_image_sources(html: str) -> list[str]:
    parser = _ImgSrcParser()
    parser.feed(html)
    return parser.sources


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


def test_enabled_sections_all_keeps_group_turnover_without_risk_exposure():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dates = pd.to_datetime(["2026-05-15", "2026-05-18", "2026-05-19"])
        symbols = [f"S{i}" for i in range(40)]
        factor = pd.DataFrame([range(40), range(40), range(39, -1, -1)], index=dates, columns=symbols, dtype=float)
        open_price = pd.DataFrame(
            [[10 + i for i in range(40)], [11 + i for i in range(40)], [12 + i for i in range(40)]],
            index=dates,
            columns=symbols,
            dtype=float,
        )
        cfg = BacktestConfig(
            data_sources=DataSourceConfig(risk_exposure_source="none"),
            output_root=tmp_path,
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=False,
            enabled_sections="all",
        )

        result = run_factor_backtest(
            factor_df=factor,
            market_data=MarketDataBundle(open_price=open_price),
            config=cfg,
            log_fn=lambda *_: None,
        )

        sections = result.section_status["all"]
        assert "group_turnover" in sections
        assert "group_exposure_diagnostics" not in sections
        assert "factor_style_exposure" not in sections
        assert _table_exists(result.run_dir / "pools" / "all", "group_turnover_edge_summary.csv")


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
        assert "[v1] pool all: computing IC: methods=['spearman']" in text
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
        cfg = BacktestConfig(
            data_sources=DataSourceConfig(risk_exposure_source="none"),
            output_root=Path(tmp),
            selected_pools=["all"],
            horizons=[1],
            tradability_filter=True,
        )

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


def test_ic_overview_keeps_20d_and_external_return_columns():
    ic = pd.DataFrame(
        {
            "ic_1d": [0.01, 0.02],
            "ic_20d": [0.03, 0.04],
            "ic_external_alpha": [0.05, 0.07],
        },
        index=pd.to_datetime(["2026-05-15", "2026-05-18"]),
    )
    section = sections_module.ICOverviewSection()

    result = section.compute({"daily_ic": ic})

    assert list(result.tables["ic_overview"].columns) == ["ic_20d", "ic_external_alpha"]


def test_ic_sections_emit_method_specific_tables_and_plots():
    with tempfile.TemporaryDirectory() as tmp:
        dates = pd.to_datetime(["2026-05-15", "2026-05-18"])
        daily_ic_by_method = {
            "spearman": pd.DataFrame({"ic_1d": [0.1, 0.2]}, index=dates),
            "pearson": pd.DataFrame({"ic_1d": [0.3, 0.4]}, index=dates),
        }
        context = {
            "daily_ic_by_method": daily_ic_by_method,
            "daily_ic": daily_ic_by_method["spearman"],
            "plots_dir": Path(tmp),
            "horizon_colors": {1: "#111111"},
        }
        original_plot_lines = sections_module._plot_lines
        try:
            sections_module._plot_lines = lambda df, path, title, result, **kwargs: result.plots.update({Path(path).name: title})
            cumulative = sections_module.CumulativeICSection().render(
                context,
                sections_module.CumulativeICSection().compute(context),
            )
            overview = sections_module.ICOverviewSection().render(
                context,
                sections_module.ICOverviewSection().compute(context),
            )
        finally:
            sections_module._plot_lines = original_plot_lines

    assert "daily_ic_spearman" in cumulative.tables
    assert "daily_ic_pearson" in cumulative.tables
    assert "daily_ic" in cumulative.tables
    assert "ic_stats_spearman" in cumulative.tables
    assert "ic_stats_pearson" in cumulative.tables
    assert "ic_stats" in cumulative.tables
    assert "cumulative_ic_spearman.png" in cumulative.plots
    assert "Cumulative Spearman RankIC" == cumulative.plots["cumulative_ic_spearman.png"]
    assert "cumulative_ic_pearson.png" in cumulative.plots
    assert "Cumulative Pearson IC" == cumulative.plots["cumulative_ic_pearson.png"]
    assert "ic_overview_spearman" in overview.tables
    assert "ic_overview_pearson" in overview.tables
    assert "ic_overview_spearman.png" in overview.plots
    assert "ic_overview_pearson.png" in overview.plots


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
        assert "Cumulative Spearman RankIC" in html
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
                data_sources=DataSourceConfig(risk_exposure_source="none"),
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
