"""Test-suite hygiene for sqlite-vec heavy embedded DB tests."""
from __future__ import annotations

import gc

import pytest


def pytest_runtest_teardown(item, nextitem):  # pragma: no cover - pytest hook
    # The sqlite-vec extension can keep sizable native buffers reachable until
    # cyclic GC runs.  Release them between tests so the full suite can run in
    # memory-constrained CI containers instead of relying on per-file execution.
    gc.collect()


# ── slow-test gating ─────────────────────────────────────────────────────────
# A handful of end-to-end export/import/upgrade smoke tests take ~160s of *call*
# time each and dominate the whole sweep (the rest of the suite runs in ~9s under
# `-n auto`). They're marked `@pytest.mark.slow` and skipped by default so the
# inner loop stays fast; pass `--run-slow` (CI does) to include them.
def pytest_addoption(parser):  # pragma: no cover - pytest hook
    parser.addoption(
        "--run-slow", action="store_true", default=False,
        help="run @pytest.mark.slow tests (the ~3min export/import/upgrade smoke suite)",
    )


def pytest_configure(config):  # pragma: no cover - pytest hook
    config.addinivalue_line(
        "markers", "slow: heavy end-to-end/upgrade smoke test; skipped unless --run-slow",
    )


def pytest_collection_modifyitems(config, items):  # pragma: no cover - pytest hook
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="slow; pass --run-slow to include")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
