from __future__ import annotations

import importlib
import inspect
import sys
import traceback
from pathlib import Path


TEST_MODULES = [
    "tests.test_config",
    "tests.test_factor_loader",
    "tests.test_external_returns",
    "tests.test_calendar_pools_market_data",
    "tests.test_filters_analytics",
    "tests.test_runner_sections",
    "tests.test_clickhouse_adapter",
    "tests.test_io",
    "tests.test_result_loader",
]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    passed = 0
    failed = 0
    for module_name in TEST_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            failed += 1
            print(f"IMPORT FAIL {module_name}")
            traceback.print_exc()
            continue

        for name, func in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            try:
                func()
            except Exception:
                failed += 1
                print(f"FAIL {module_name}.{name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS {module_name}.{name}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
