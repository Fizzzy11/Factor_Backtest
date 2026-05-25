"""Factor ranking backtest framework."""

from factor_backtest.config import BacktestConfig
from factor_backtest.result_loader import LoadedBacktestResult, load_backtest_result
from factor_backtest.runner import (
    render_factor_backtest_report,
    run_factor_backtest,
    run_factor_backtest_data,
    run_factor_backtest_minimal,
)

__version__ = "v1"

__all__ = [
    "BacktestConfig",
    "LoadedBacktestResult",
    "__version__",
    "load_backtest_result",
    "render_factor_backtest_report",
    "run_factor_backtest",
    "run_factor_backtest_data",
    "run_factor_backtest_minimal",
]
