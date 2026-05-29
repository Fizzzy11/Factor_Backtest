from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

from factor_backtest.config import BacktestConfig, DataSourceConfig


DEFAULT_STYLE_COLUMNS = (
    "size",
    "non_linear_size",
    "momentum",
    "liquidity",
    "book_to_price",
    "leverage",
    "growth",
    "earnings_yield",
    "beta",
    "residual_volatility",
)

IGNORED_EXPOSURE_COLUMNS = ("comovement",)
DATE_COLUMNS = ("trade_date", "date")


@dataclass(frozen=True)
class RiskExposureData:
    exposures: pd.DataFrame
    style_columns: tuple[str, ...] = DEFAULT_STYLE_COLUMNS
    industry_columns: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def slice_date(self, trade_date, symbols: Iterable[str]) -> pd.DataFrame:
        date = pd.Timestamp(trade_date)
        symbol_index = pd.Index([str(symbol) for symbol in symbols], name="symbol")
        if date not in self.exposures.index.get_level_values("trade_date"):
            return pd.DataFrame(index=symbol_index, columns=self.exposures.columns, dtype="float64")
        daily = self.exposures.xs(date, level="trade_date")
        return daily.reindex(symbol_index)

    def wide_style(self, column: str, dates: Iterable, symbols: Iterable[str]) -> pd.DataFrame:
        return self._wide_column(column, dates, symbols)

    def wide_industry(self, column: str, dates: Iterable, symbols: Iterable[str]) -> pd.DataFrame:
        return self._wide_column(column, dates, symbols)

    def _wide_column(self, column: str, dates: Iterable, symbols: Iterable[str]) -> pd.DataFrame:
        date_index = pd.DatetimeIndex(pd.to_datetime(list(dates)), name="trade_date")
        symbol_index = pd.Index([str(symbol) for symbol in symbols])
        values = []
        for date in date_index:
            daily = self.slice_date(date, symbol_index)
            values.append(pd.to_numeric(daily[column], errors="coerce") if column in daily else pd.Series(index=symbol_index, dtype="float64"))
        return pd.DataFrame(values, index=date_index, columns=symbol_index)


def load_risk_exposure_from_csv(
    path: str | Path,
    *,
    style_columns: Iterable[str] = DEFAULT_STYLE_COLUMNS,
    ignored_columns: Iterable[str] = IGNORED_EXPOSURE_COLUMNS,
) -> RiskExposureData:
    raw = pd.read_csv(path)
    return dataframe_to_risk_exposure(raw, style_columns=style_columns, ignored_columns=ignored_columns)


def resolve_risk_exposure(config: BacktestConfig) -> RiskExposureData | None:
    source = config.data_sources.risk_exposure_source
    if source == "none":
        return None
    if source == "csv":
        path = Path(config.paths.risk_exposure_path)
        if not path.is_absolute():
            path = Path(config.paths.data_root) / path
        return load_risk_exposure_from_csv(path)
    if source == "clickhouse":
        return load_risk_exposure_from_clickhouse(config=config.data_sources)
    raise ValueError(f"Unknown risk_exposure_source: {source}")


def load_risk_exposure_from_clickhouse(*, config: DataSourceConfig) -> RiskExposureData:
    table = config.clickhouse_tables.risk_exposure
    if not table:
        raise ValueError("risk_exposure_source='clickhouse' requires clickhouse_tables.risk_exposure")
    raise NotImplementedError("ClickHouse risk exposure loading is not implemented yet")


def dataframe_to_risk_exposure(
    raw: pd.DataFrame,
    *,
    style_columns: Iterable[str] = DEFAULT_STYLE_COLUMNS,
    ignored_columns: Iterable[str] = IGNORED_EXPOSURE_COLUMNS,
) -> RiskExposureData:
    style_cols = tuple(style_columns)
    ignored = set(ignored_columns)
    df = _standardize_risk_exposure_dataframe(raw)
    missing_styles = [col for col in style_cols if col not in df.columns]
    if missing_styles:
        raise ValueError(f"Missing required style exposure columns: {missing_styles}")

    metadata_cols = {"trade_date", "symbol", *ignored}
    industry_cols = tuple(col for col in df.columns if col not in metadata_cols and col not in style_cols)
    if not industry_cols:
        raise ValueError("Risk exposure data requires at least one industry dummy column")

    keep_cols = [*style_cols, *industry_cols]
    out = df[["trade_date", "symbol", *keep_cols]].copy()
    for col in keep_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["symbol"] = out["symbol"].astype(str)
    out["trade_date"] = pd.to_datetime(out["trade_date"])
    out = out.sort_values(["trade_date", "symbol"])
    exposures = out.set_index(["trade_date", "symbol"])[keep_cols].sort_index()

    warnings = _industry_membership_warnings(exposures, industry_cols)
    return RiskExposureData(
        exposures=exposures,
        style_columns=style_cols,
        industry_columns=industry_cols,
        warnings=tuple(warnings),
    )


def _standardize_risk_exposure_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.index.name in DATE_COLUMNS and not any(col in raw.columns for col in DATE_COLUMNS):
        df = raw.reset_index()
    else:
        df = raw.copy()
    date_col = _pick_column(df.columns, DATE_COLUMNS)
    if date_col is None:
        unnamed_cols = [col for col in df.columns if str(col).startswith("Unnamed")]
        if unnamed_cols:
            date_col = unnamed_cols[0]
    if date_col is None or "symbol" not in df.columns:
        raise ValueError("Risk exposure data requires date/trade_date index or column and symbol column")
    return df.rename(columns={date_col: "trade_date"})


def _industry_membership_warnings(exposures: pd.DataFrame, industry_columns: tuple[str, ...]) -> list[str]:
    industry_sum = exposures.loc[:, industry_columns].fillna(0).sum(axis=1)
    missing = int((industry_sum == 0).sum())
    multiple = int((industry_sum > 1).sum())
    warnings = []
    if missing:
        warnings.append(f"risk exposure has {missing} date-symbol rows with missing industry membership")
    if multiple:
        warnings.append(f"risk exposure has {multiple} date-symbol rows with multiple industry memberships")
    return warnings


def _pick_column(columns, candidates: Iterable[str]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None
