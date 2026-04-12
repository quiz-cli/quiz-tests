"""Parametrized integration tests — one test case per test directory."""

from pathlib import Path

import pytest

from framework.runner import run_test

_TESTS_DIR = Path(__file__).parent / "tests"

_test_dirs = (
    sorted(d for d in _TESTS_DIR.iterdir() if d.is_dir() and (d / "test.yaml").exists())
    if _TESTS_DIR.exists()
    else []
)


@pytest.mark.parametrize("test_dir", _test_dirs, ids=lambda d: d.name)
async def test_case(test_dir: Path) -> None:
    """Run a single test case end-to-end."""
    await run_test(test_dir)
