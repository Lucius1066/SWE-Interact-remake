"""Keep pytest's temp directories working under a restricted file sandbox.

Under DSH workspace-write mode the pytest temp root is writable only at its top
level: ``tempfile.mkdtemp()`` succeeds, but pytest's own create-and-clean
numbered run directory is denied. That is an environment limitation, not a test
failure, so when ``LEAKAGE_TEST_OUTPUT_DIR`` points at a usable scratch
directory the tests create their own output directories there and pytest's temp
machinery is bypassed (``-p no:tmpdir``). Everywhere else the standard
``tmp_path`` fixture is used unchanged.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


def _next_run_dir(override: str) -> Path:
    for index in range(1000):
        target = Path(override) / f"leakage-run-{os.getpid()}-{index}"
        if not target.exists():
            target.mkdir(parents=True)
            return target
    raise RuntimeError(f"no free run directory under {override}")


@pytest.fixture
def output_dir() -> Path:
    """Writable output directory for a mock run.

    Uses the pytest temp root by default. When ``LEAKAGE_TEST_OUTPUT_DIR`` is
    set, a sandboxed run gets a plain ``tempfile.mkdtemp()`` directory there
    instead, because pytest cannot create or remove numbered run directories in
    a workspace-write sandbox (see the module docstring).
    """
    override = os.environ.get("LEAKAGE_TEST_OUTPUT_DIR")
    if not override:
        return Path(tempfile.mkdtemp(prefix="leakage-"))
    return _next_run_dir(override)
