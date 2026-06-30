from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd


DATE_COLUMNS = ("trade_date", "date")
SYMBOL_COLUMNS = ("symbol", "asset")
VALUE_COLUMNS = ("factor_value", "factor", "value")


def resolve_factor_path(
    *,
    data_root: str | Path,
    factor_name: str | None = None,
    factor_path: str | Path | None = None,
    suffixes: Iterable[str] = (".h5", ".csv"),
) -> Path:
    if factor_path is not None:
        path = Path(factor_path)
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    if not factor_name:
        raise ValueError("factor_name or factor_path is required")

    root = Path(data_root)
    factor_dir = root / f"factor_{factor_name}"
    stem = f"factor_{factor_name}"
    for suffix in suffixes:
        candidate = factor_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"No factor file found for {factor_name!r} in {factor_dir}")


def normalize_factor_dataframe(df: pd.DataFrame, value_column: str | None = None) -> pd.DataFrame:
    if isinstance(df.index, pd.MultiIndex):
        return _normalize_multiindex_long(df, value_column=value_column)
    if _looks_like_long_dataframe(df):
        return _normalize_long_dataframe(df, value_column=value_column)
    return _normalize_wide_dataframe(df)


def load_factor_file(path: str | Path, h5_key: str | None = None, value_column: str | None = None) -> pd.DataFrame:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        raw = pd.read_csv(path)
    elif path.suffix.lower() in {".h5", ".hdf", ".hdf5"}:
        if h5_key is None:
            with pd.HDFStore(path, mode="r") as store:
                keys = store.keys()
            if len(keys) != 1:
                raise ValueError(f"H5 file has multiple keys; specify h5_key. Available keys: {keys}")
            h5_key = keys[0]
        raw = pd.read_hdf(path, key=h5_key)
    else:
        raise ValueError(f"Unsupported factor file suffix: {path.suffix}")
    return normalize_factor_dataframe(raw, value_column=value_column)


def _normalize_wide_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    date_col = _pick_column(set(out.columns), DATE_COLUMNS)
    if date_col is not None:
        out = out.set_index(date_col)
    out.index = pd.to_datetime(out.index)
    out.index.name = "trade_date"
    out.columns = pd.Index([str(c) for c in out.columns], name=None)
    out = out.apply(pd.to_numeric, errors="coerce")
    return out.sort_index()


def _normalize_multiindex_long(df: pd.DataFrame, value_column: str | None) -> pd.DataFrame:
    reset = df.reset_index()
    return _normalize_long_dataframe(reset, value_column=value_column)


def _normalize_long_dataframe(df: pd.DataFrame, value_column: str | None) -> pd.DataFrame:
    columns = set(df.columns)
    date_col = _pick_column(columns, DATE_COLUMNS)
    symbol_col = _pick_column(columns, SYMBOL_COLUMNS)
    value_col = value_column or _pick_column(columns, VALUE_COLUMNS)
    if date_col is None or symbol_col is None or value_col is None:
        raise ValueError("Long factor data requires date/trade_date, symbol/asset, and value column")

    long_df = df[[date_col, symbol_col, value_col]].copy()
    long_df = long_df.rename(columns={date_col: "trade_date", symbol_col: "symbol", value_col: "factor_value"})
    long_df["trade_date"] = pd.to_datetime(long_df["trade_date"])
    long_df["symbol"] = long_df["symbol"].astype(str)
    long_df["factor_value"] = pd.to_numeric(long_df["factor_value"], errors="coerce")
    out = long_df.pivot(index="trade_date", columns="symbol", values="factor_value")
    out.index.name = "trade_date"
    out.columns.name = "symbol"
    return out.sort_index().sort_index(axis=1)


def _looks_like_long_dataframe(df: pd.DataFrame) -> bool:
    columns = set(df.columns)
    return _pick_column(columns, DATE_COLUMNS) is not None and _pick_column(columns, SYMBOL_COLUMNS) is not None


def _pick_column(columns: set, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None
