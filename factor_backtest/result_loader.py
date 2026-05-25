from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from factor_backtest.io import read_json, read_table


@dataclass(frozen=True)
class LoadedBacktestResult:
    run_dir: Path
    meta: dict
    log: dict

    def pool_dir(self, pool: str) -> Path:
        return self.run_dir / "pools" / pool

    def table_path(self, pool: str, table_name: str) -> Path:
        return self.pool_dir(pool) / "tables" / f"{table_name}.csv"

    def artifact_path(self, pool: str, artifact_name: str) -> Path:
        base = self.pool_dir(pool) / "artifacts" / artifact_name
        if base.exists():
            return base
        fallback = base.with_suffix(base.suffix + ".pkl")
        if fallback.exists():
            return fallback
        return base

    def plot_path(self, pool: str, plot_name: str) -> Path:
        return self.pool_dir(pool) / "plots" / plot_name

    def read_table(self, pool: str, table_name: str) -> pd.DataFrame:
        return read_table(self.table_path(pool, table_name))

    def read_artifact(self, pool: str, artifact_name: str) -> pd.DataFrame:
        return read_table(self.artifact_path(pool, artifact_name))


def load_backtest_result(
    *,
    factor_name: str,
    output_root: str | Path = "/data/zhangyuan/Factor_Backtest_Result",
    run: str = "latest",
) -> LoadedBacktestResult:
    factor_root = Path(output_root) / factor_name
    run_dir = factor_root / "latest" if run == "latest" else factor_root / "runs" / run
    if not run_dir.exists():
        raise FileNotFoundError(f"Backtest result directory does not exist: {run_dir}")
    return LoadedBacktestResult(
        run_dir=run_dir,
        meta=read_json(run_dir / "run_meta.json"),
        log=read_json(run_dir / "run_log.json"),
    )
