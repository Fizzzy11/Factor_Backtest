from factor_backtest.clickhouse_adapter import load_market_data_from_clickhouse
from factor_backtest.config import BacktestConfig
from factor_backtest.factor_loader import load_factor_file, resolve_factor_path
from factor_backtest.runner import run_factor_backtest


def main() -> None:
    # ===== 1. Factor file parameters =====
    # Existing factor file:
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.h5
    data_root = "/data/zhangyuan"
    factor_name_for_path = "dm_20d"
    factor_display_name = "factor_dm_20d"
    factor_path = None
    factor_h5_key = None

    # ===== 2. Market data parameters =====
    # end_date is right-open. Keep at least max(horizons) + 1 future trading
    # opens after the last signal date for next-open entry and open-to-open returns.
    market_start_date = "2020-01-01"
    market_end_date = "2026-05-02"

    # ===== 3. Pool parameters =====
    # Use ["all"] for full market.
    # Example index pools: ["hs300_pool", "gz1000_pool", "gz2000_pool"]
    selected_pools = ["all"]

    # ===== 4. Backtest core parameters =====
    horizons = [1, 5, 10, 20]
    min_listed_days = 120
    tradability_filter = True
    min_ic_stocks = 30
    min_group_stocks = 10
    analysis_windows = [120, 250, 750]
    group_return_windows = {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}

    # ===== 5. Factor preprocessing parameters =====
    # v1 keeps the raw factor ordering by default.
    winsorize_factor = False
    standardize_factor = False

    # ===== 6. Report section parameters =====
    # "all" runs every built-in section.
    # Example subset: ["data_quality", "ic_overview", "cumulative_ic"]
    enabled_sections = "all"
    verbose = True

    # ===== 7. Output parameters =====
    # Recommended script path:
    # /app/workspace/zhangyuan/Factor_Backtest_Result/factor_dm_20d/run_factor_dm_20d.py
    # Output path:
    # /data/zhangyuan/Factor_Backtest_Result/factor_dm_20d/latest/
    # /data/zhangyuan/Factor_Backtest_Result/factor_dm_20d/runs/<run_time>/
    output_root = "/data/zhangyuan/Factor_Backtest_Result"
    output_layout = "latest_runs"
    render_plots = True

    resolved_factor_path = factor_path or resolve_factor_path(
        data_root=data_root,
        factor_name=factor_name_for_path,
    )
    if verbose:
        print(f"[v1] loading factor: {resolved_factor_path}")
    factor_df = load_factor_file(
        resolved_factor_path,
        h5_key=factor_h5_key,
    )
    if verbose:
        print(f"[v1] factor loaded: dates={len(factor_df.index):,}, symbols={len(factor_df.columns):,}")

    market_data = load_market_data_from_clickhouse(
        start_date=market_start_date,
        end_date=market_end_date,
        verbose=verbose,
    )

    cfg = BacktestConfig(
        factor_name=factor_display_name,
        output_root=output_root,
        selected_pools=selected_pools,
        horizons=horizons,
        min_listed_days=min_listed_days,
        tradability_filter=tradability_filter,
        min_ic_stocks=min_ic_stocks,
        min_group_stocks=min_group_stocks,
        analysis_windows=analysis_windows,
        group_return_windows=group_return_windows,
        winsorize_factor=winsorize_factor,
        standardize_factor=standardize_factor,
        enabled_sections=enabled_sections,
        output_layout=output_layout,
        render_plots=render_plots,
        verbose=verbose,
    )

    result = run_factor_backtest(
        factor_df=factor_df,
        market_data=market_data,
        config=cfg,
    )
    print(result.run_dir)
    print(result.latest_dir)


if __name__ == "__main__":
    main()
