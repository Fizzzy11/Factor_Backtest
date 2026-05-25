from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


DEFAULT_HORIZON_COLORS = {
    1: "#4C78A8",
    5: "#F58518",
    10: "#54A24B",
    20: "#E45756",
}


@dataclass(frozen=True)
class PathConfig:
    project_dir: Path = Path("/app/workspace/zhangyuan/Factor_Backtest")
    data_root: Path = Path("/data/zhangyuan")
    pool_dir: Path = Path("/data/zhangyuan/pool")


@dataclass(frozen=True)
class PoolDefinition:
    path: Path | None
    display_name: str
    is_virtual: bool = False


POOL_REGISTRY: dict[str, PoolDefinition] = {
    "all": PoolDefinition(path=None, display_name="全市场", is_virtual=True),
    "hs300_pool": PoolDefinition(path=Path("/data/zhangyuan/pool/hs300_pool.csv"), display_name="沪深300"),
    "zz500_pool": PoolDefinition(path=Path("/data/zhangyuan/pool/zz500_pool.csv"), display_name="中证500"),
    "zz1000_pool": PoolDefinition(path=Path("/data/zhangyuan/pool/zz1000_pool.csv"), display_name="中证1000"),
    "gz1000_pool": PoolDefinition(path=Path("/data/zhangyuan/pool/gz1000_pool.csv"), display_name="国证1000"),
    "gz2000_pool": PoolDefinition(path=Path("/data/zhangyuan/pool/gz2000_pool.csv"), display_name="国证2000"),
    "gzMidsmallcap_pool": PoolDefinition(
        path=Path("/data/zhangyuan/pool/gzMidsmallcap_pool.csv"),
        display_name="国证中小盘800",
    ),
    "miMicrocap_pool": PoolDefinition(
        path=Path("/data/zhangyuan/pool/miMicrocap_pool.csv"),
        display_name="米筐微盘",
    ),
}


@dataclass
class BacktestConfig:
    framework_version: str = "v1"
    paths: PathConfig = field(default_factory=PathConfig)
    output_root: Path | None = None
    factor_name: str | None = None
    selected_pools: list[str] = field(default_factory=lambda: ["all"])
    horizons: list[int] = field(default_factory=lambda: [1, 5, 10, 20])
    horizon_colors: dict[int, str] = field(default_factory=lambda: dict(DEFAULT_HORIZON_COLORS))
    min_listed_days: int = 120
    listed_days_source: Literal["market_data", "listing_dates"] = "market_data"
    tradability_filter: bool = True
    min_ic_stocks: int = 30
    min_group_stocks: int = 10
    analysis_windows: list[int] = field(default_factory=lambda: [120, 250, 750])
    group_return_windows: dict[str, int] = field(
        default_factory=lambda: {"6m": 120, "1y": 250, "3y": 750, "5y": 1250}
    )
    enabled_sections: str | list[str] = "all"
    winsorize_factor: bool = False
    standardize_factor: bool = False
    output_layout: Literal["latest_runs", "timestamp"] = "latest_runs"
    render_plots: bool = True
    verbose: bool = True

    def __post_init__(self) -> None:
        if self.output_root is None:
            self.output_root = self.paths.data_root / "Factor_Backtest_Result"
        else:
            self.output_root = Path(self.output_root)
