from factor_backtest.clickhouse_adapter import load_market_data_from_clickhouse
from factor_backtest.config import BacktestConfig, CompanyDiagnosticsConfig, DataSourceConfig, HandoffConfig, PathConfig
from factor_backtest.factor_loader import load_factor_file, resolve_factor_path
from factor_backtest.runner import run_factor_backtest


def main() -> None:
    # ===== 1. Factor file parameters =====
    # 因子文件可以是单个 .parquet 文件、后缀为 .parquet 的分区目录、
    # 原有 H5 文件或 CSV 文件。factor_path=None 时按默认优先级自动发现：
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.parquet
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.h5
    # /data/zhangyuan/factor_dm_20d/factor_dm_20d.csv
    data_root = "/data/zhangyuan"
    factor_name_for_path = "dm_20d"
    factor_display_name = "factor_dm_20d"
    # 也可以显式指定：
    # factor_path = "/data/zhangyuan/factor_dm_20d/factor_dm_20d.parquet"
    # factor_path = "/data/zhangyuan/factor_dm_20d/factor_dm_20d.h5"
    # factor_path = "/data/zhangyuan/factor_dm_20d/factor_dm_20d.csv"
    factor_path = None
    # 只在读取 H5 时生效；CSV 和 parquet 会忽略该参数。
    factor_h5_key = None

    # ===== 2. Market data parameters =====
    # end_date is right-open. Keep at least max(horizons) + 1 future trading
    # opens after the last signal date for next-open entry and open-to-open returns.
    market_start_date = "2020-01-01"
    market_end_date = "2026-05-02"

    # ===== 3. Pool parameters =====
    # Default run pools: full market + HS300 + CSI 1000 + CSI 2000.
    # Other examples: ["all"], ["gz1000_pool", "gz2000_pool"].
    selected_pools = ["all", "hs300_pool", "zz1000_pool", "zz2000_pool"]

    # ===== 4. Backtest core parameters =====
    horizons = [1, 5, 10, 20]
    min_listed_days = 120
    tradability_filter = True
    # IC methods: ["spearman"], ["pearson"], or ["spearman", "pearson"].
    ic_methods = ["spearman", "pearson"]
    min_ic_stocks = 30
    # Used by group_return, group_exposure_diagnostics, and group_turnover.
    min_group_stocks = 10
    analysis_windows = [120, 250, 750]
    group_return_windows = {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}

    # ===== 5. Factor preprocessing parameters =====
    # v1 keeps the raw factor ordering by default.
    winsorize_factor = False
    standardize_factor = False

    # ===== 6. Report section parameters =====
    # "all" runs every built-in section.
    # Example subset: ["data_quality", "ic_overview", "cumulative_ic", "group_turnover"].
    enabled_sections = "all"
    verbose = True

    # ===== 7. Risk exposure and industry data =====
    # Default uses the configured local parquet/csv file. Set to "none" if the file is unavailable.
    # /data/zhangyuan/risk&industry/CNE5_Industry_daily.parquet
    risk_exposure_source = "csv"
    risk_exposure_path = "risk&industry/CNE5_Industry_daily.parquet"
    # Used by within_industry_group_return.
    min_industry_ic_stocks = 10

    # ===== 8. Output parameters =====
    # Recommended script path:
    # /app/workspace/zhangyuan/Factor_Backtest_Result/factor_dm_20d/run_factor_dm_20d.py
    # Output path:
    # /data/zhangyuan/Factor_Backtest_Result/factor_dm_20d/latest/
    # /data/zhangyuan/Factor_Backtest_Result/factor_dm_20d/runs/<run_time>/
    output_root = "/data/zhangyuan/Factor_Backtest_Result"
    output_layout = "latest_runs"
    artifact_level = "none"
    # Set to False for table-only batch runs. You can regenerate PNG/report later
    # with render_factor_backtest_report(result.latest_dir).
    render_plots = True

    # ===== 9. Platform handoff parameters =====
    # Default is off. Set handoff_enabled=True only when you need to export
    # docs/handoffs/factor_backtest_platform/sample_latest/ for platform acceptance.
    # For first-round handoff only, a faster and safer setup is:
    # selected_pools = ["all"]
    # ic_methods = ["spearman"]
    # risk_exposure_source = "none"
    # enabled_sections = [
    #     "data_quality",
    #     "cumulative_ic",
    #     "group_return",
    #     "long_short",
    #     "group_turnover",
    #     "performance_metrics",
    # ]
    handoff_enabled = False
    handoff_factor_direction = "high_is_long"
    # If None, handoff data_asof uses the max date in factor_df.index.
    handoff_data_asof = None

    # ===== 10. Company diagnostics parameters =====
    # Formal company diagnostics are different from the first-round handoff pending files.
    # Default is off. When enabled, computed JSON files are written to:
    # /data/zhangyuan/Factor_Backtest_Result/factor_dm_20d/runs/<run_time>/diagnostics/
    # /data/zhangyuan/Factor_Backtest_Result/factor_dm_20d/latest/diagnostics/
    #
    # Expected book input is a long panel:
    # date/trade_date, symbol/stock_id, factor_id, value/factor_value
    # Regime input is a boolean wide table:
    # date/trade_date, bull, bear, high_vol, low_vol
    company_diagnostics_enabled = False

    company_diagnostics_production_book_source = "file"
    company_diagnostics_peer_book_source = "file"
    company_diagnostics_regime_source = "file"
    production_book_path = None
    peer_book_path = None
    factor_meta_path = None
    factor_ls_pnl_path = None
    regime_path = None

    baseline_suite_id = None
    baseline_book_version = None
    peer_book_version = None
    peer_pool_id = "research+promoted"

    hypothesis_direction = "unknown"  # "high_is_long", "high_is_short", or "unknown"
    idea_id = None
    idea_version = 1
    regime_labels = ["bull", "bear", "high_vol", "low_vol"]
    neutralization_layers = ["production_book"]

    spanning_topk = 20
    spanning_rounds = 3
    top_spanning_factors = 5
    topk_overlap_k = 50
    min_similarity_stocks = 30

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

    if company_diagnostics_enabled:
        diagnostics_config = CompanyDiagnosticsConfig(
            enabled=True,
            production_book_source=company_diagnostics_production_book_source,
            peer_book_source=company_diagnostics_peer_book_source,
            regime_source=company_diagnostics_regime_source,
            production_book_path=production_book_path,
            peer_book_path=peer_book_path,
            factor_meta_path=factor_meta_path,
            factor_ls_pnl_path=factor_ls_pnl_path,
            regime_path=regime_path,
            baseline_suite_id=baseline_suite_id,
            baseline_book_version=baseline_book_version,
            peer_book_version=peer_book_version,
            peer_pool_id=peer_pool_id,
            hypothesis_direction=hypothesis_direction,
            idea_id=idea_id,
            version=idea_version,
            regime_labels=regime_labels,
            neutralization_layers=neutralization_layers,
            spanning_topk=spanning_topk,
            spanning_rounds=spanning_rounds,
            top_spanning_factors=top_spanning_factors,
            topk_overlap_k=topk_overlap_k,
            min_similarity_stocks=min_similarity_stocks,
        )
    else:
        diagnostics_config = CompanyDiagnosticsConfig(enabled=False)

    cfg = BacktestConfig(
        paths=PathConfig(data_root=data_root, risk_exposure_path=risk_exposure_path),
        data_sources=DataSourceConfig(risk_exposure_source=risk_exposure_source),
        factor_name=factor_display_name,
        output_root=output_root,
        selected_pools=selected_pools,
        horizons=horizons,
        min_listed_days=min_listed_days,
        tradability_filter=tradability_filter,
        ic_methods=ic_methods,
        min_ic_stocks=min_ic_stocks,
        min_group_stocks=min_group_stocks,
        min_industry_ic_stocks=min_industry_ic_stocks,
        analysis_windows=analysis_windows,
        group_return_windows=group_return_windows,
        winsorize_factor=winsorize_factor,
        standardize_factor=standardize_factor,
        enabled_sections=enabled_sections,
        output_layout=output_layout,
        artifact_level=artifact_level,
        render_plots=render_plots,
        handoff=HandoffConfig(
            enabled=handoff_enabled,
            factor_direction=handoff_factor_direction,
            data_asof=handoff_data_asof,
        ),
        diagnostics=diagnostics_config,
        verbose=verbose,
    )

    result = run_factor_backtest(
        factor_df=factor_df,
        market_data=market_data,
        config=cfg,
    )
    print(result.run_dir)
    print(result.latest_dir)
    if result.handoff_dir is not None:
        print(result.handoff_dir)


if __name__ == "__main__":
    main()
