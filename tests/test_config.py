from pathlib import Path

from factor_backtest.config import BacktestConfig, DEFAULT_HORIZON_COLORS, POOL_REGISTRY


def test_backtest_config_defaults_match_design():
    cfg = BacktestConfig()

    assert cfg.paths.project_dir == Path("/app/workspace/zhangyuan/Factor_Backtest")
    assert cfg.paths.data_root == Path("/data/zhangyuan")
    assert cfg.paths.pool_dir == Path("/data/zhangyuan/pool")
    assert cfg.output_root == Path("/data/zhangyuan/Factor_Backtest_Result")
    assert cfg.selected_pools == ["all"]
    assert cfg.framework_version == "v1"
    assert cfg.horizons == [1, 5, 10, 20]
    assert cfg.min_listed_days == 120
    assert cfg.min_ic_stocks == 30
    assert cfg.min_group_stocks == 10
    assert cfg.enabled_sections == "all"
    assert cfg.tradability_filter is True
    assert cfg.output_layout == "latest_runs"
    assert cfg.render_plots is True
    assert cfg.group_return_windows == {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}
    assert cfg.verbose is True


def test_pool_registry_contains_virtual_all_and_named_pools():
    assert POOL_REGISTRY["all"].is_virtual is True
    assert POOL_REGISTRY["all"].path is None
    assert POOL_REGISTRY["hs300_pool"].display_name == "沪深300"
    assert POOL_REGISTRY["gz1000_pool"].display_name == "国证1000"
    assert POOL_REGISTRY["gz2000_pool"].display_name == "国证2000"
    assert POOL_REGISTRY["miMicrocap_pool"].path == Path("/data/zhangyuan/pool/miMicrocap_pool.csv")


def test_horizon_color_defaults_are_stable():
    assert DEFAULT_HORIZON_COLORS == {
        1: "#4C78A8",
        5: "#F58518",
        10: "#54A24B",
        20: "#E45756",
    }
