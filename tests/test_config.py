from pathlib import Path

from factor_backtest.config import BacktestConfig, DEFAULT_HORIZON_COLORS, POOL_REGISTRY, ClickHouseConfig, ClickHouseTableConfig, PathConfig


def test_backtest_config_defaults_match_design():
    cfg = BacktestConfig()

    assert cfg.paths.project_dir == Path("/app/workspace/zhangyuan/Factor_Backtest")
    assert cfg.paths.data_root == Path("/data/zhangyuan")
    assert cfg.paths.pool_dir == Path("/data/zhangyuan/pool")
    assert cfg.paths.risk_exposure_path == Path("risk&industry/CNE5_Industry_daily.parquet")
    assert cfg.output_root == Path("/data/zhangyuan/Factor_Backtest_Result")
    assert cfg.selected_pools == ["all"]
    assert cfg.framework_version == "v1"
    assert cfg.horizons == [1, 5, 10, 20]
    assert cfg.min_listed_days == 120
    assert cfg.min_ic_stocks == 30
    assert cfg.min_group_stocks == 10
    assert cfg.min_industry_ic_stocks == 10
    assert cfg.enabled_sections == "all"
    assert cfg.tradability_filter is True
    assert cfg.output_layout == "latest_runs"
    assert cfg.artifact_level == "none"
    assert cfg.render_plots is True
    assert cfg.write_neutralized_factors is False
    assert cfg.handoff.enabled is False
    assert cfg.handoff.output_dir == Path("docs/handoffs/factor_backtest_platform")
    assert cfg.group_return_windows == {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}
    assert cfg.verbose is True
    assert cfg.data_sources.market_data_source == "clickhouse"
    assert cfg.data_sources.pool_source == "csv"
    assert cfg.data_sources.factor_source == "file"
    assert cfg.data_sources.risk_exposure_source == "csv"
    assert cfg.data_sources.clickhouse == ClickHouseConfig()
    assert cfg.data_sources.clickhouse_tables == ClickHouseTableConfig()


def test_path_config_normalizes_string_paths():
    cfg = BacktestConfig(paths=PathConfig(data_root="/tmp/data", risk_exposure_path="risk&industry/CNE5_Industry_daily.parquet"))

    assert cfg.paths.data_root == Path("/tmp/data")
    assert cfg.paths.risk_exposure_path == Path("risk&industry/CNE5_Industry_daily.parquet")
    assert cfg.output_root == Path("/tmp/data") / "Factor_Backtest_Result"


def test_pool_registry_contains_virtual_all_and_named_pools():
    assert POOL_REGISTRY["all"].is_virtual is True
    assert POOL_REGISTRY["all"].path is None
    assert POOL_REGISTRY["hs300_pool"].display_name == "沪深300"
    assert POOL_REGISTRY["zz1000_pool"].path == Path("zz1000_pool.csv")
    assert POOL_REGISTRY["zz2000_pool"].path == Path("zz2000_pool.csv")
    assert POOL_REGISTRY["zz2000_pool"].display_name == "中证2000"
    assert POOL_REGISTRY["gz1000_pool"].display_name == "国证1000"
    assert POOL_REGISTRY["gz2000_pool"].display_name == "国证2000"
    assert POOL_REGISTRY["miMicrocap_pool"].path == Path("miMicrocap_pool.csv")


def test_horizon_color_defaults_are_stable():
    assert DEFAULT_HORIZON_COLORS == {
        1: "#4C78A8",
        5: "#F58518",
        10: "#54A24B",
        20: "#E45756",
    }
