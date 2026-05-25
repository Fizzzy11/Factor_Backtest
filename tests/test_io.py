from pathlib import Path
import tempfile

import pandas as pd

from factor_backtest.io import write_table


def test_write_table_does_not_disguise_pickle_as_parquet_when_engine_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "data.parquet"

        original = pd.DataFrame.to_parquet

        def raise_import_error(self, *args, **kwargs):
            raise ImportError("missing parquet engine")

        pd.DataFrame.to_parquet = raise_import_error
        try:
            written = write_table(pd.DataFrame({"a": [1]}), path)
        finally:
            pd.DataFrame.to_parquet = original

        assert written == Path(tmp) / "data.parquet.pkl"
        assert written.exists()
        assert not path.exists()
