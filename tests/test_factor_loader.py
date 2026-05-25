from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from factor_backtest.factor_loader import normalize_factor_dataframe, resolve_factor_path


def test_normalize_wide_factor_dataframe_keeps_trade_date_by_symbol_shape():
    raw = pd.DataFrame(
        {
            "000001.XSHE": [1.0, np.nan],
            "600000.XSHG": [2.0, 3.0],
        },
        index=["2026-05-15", "2026-05-18"],
    )

    out = normalize_factor_dataframe(raw)

    assert out.index.name == "trade_date"
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]
    assert out.index.tolist() == [pd.Timestamp("2026-05-15"), pd.Timestamp("2026-05-18")]
    assert out.loc[pd.Timestamp("2026-05-15"), "600000.XSHG"] == 2.0


def test_normalize_multiindex_factor_dataframe_pivots_date_asset_to_wide():
    idx = pd.MultiIndex.from_tuples(
        [
            ("2026-05-15", "000001.XSHE"),
            ("2026-05-15", "600000.XSHG"),
            ("2026-05-18", "000001.XSHE"),
        ],
        names=["date", "asset"],
    )
    raw = pd.DataFrame({"factor": [1.0, 2.0, 3.0]}, index=idx)

    out = normalize_factor_dataframe(raw)

    assert out.index.name == "trade_date"
    assert out.columns.name == "symbol"
    assert out.loc[pd.Timestamp("2026-05-15"), "000001.XSHE"] == 1.0
    assert pd.isna(out.loc[pd.Timestamp("2026-05-18"), "600000.XSHG"])


def test_normalize_long_factor_dataframe_uses_symbol_and_value_columns():
    raw = pd.DataFrame(
        {
            "trade_date": ["2026-05-15", "2026-05-15", "2026-05-18"],
            "symbol": ["000001.XSHE", "600000.XSHG", "000001.XSHE"],
            "value": [1.0, 2.0, 4.0],
        }
    )

    out = normalize_factor_dataframe(raw)

    assert out.loc[pd.Timestamp("2026-05-18"), "000001.XSHE"] == 4.0
    assert list(out.columns) == ["000001.XSHE", "600000.XSHG"]


def test_resolve_factor_path_prefers_explicit_path_and_discovers_default():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        explicit = tmp_path / "custom.csv"
        explicit.write_text("trade_date,symbol,value\n2026-05-15,000001.XSHE,1\n", encoding="utf-8")

        assert resolve_factor_path(data_root=tmp_path, factor_path=explicit) == explicit

        factor_dir = tmp_path / "factor_dm_20d"
        factor_dir.mkdir()
        discovered = factor_dir / "factor_dm_20d.csv"
        discovered.write_text("trade_date,symbol,value\n2026-05-15,000001.XSHE,1\n", encoding="utf-8")

        assert resolve_factor_path(data_root=tmp_path, factor_name="dm_20d") == discovered
