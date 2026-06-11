"""Test-suite hygiene for sqlite-vec heavy embedded DB tests."""
from __future__ import annotations

import gc


def pytest_runtest_teardown(item, nextitem):  # pragma: no cover - pytest hook
    # The sqlite-vec extension can keep sizable native buffers reachable until
    # cyclic GC runs.  Release them between tests so the full suite can run in
    # memory-constrained CI containers instead of relying on per-file execution.
    gc.collect()
