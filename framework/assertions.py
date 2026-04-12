"""Helpers for matching received WebSocket messages."""

from __future__ import annotations

import json
import re
from typing import Any


def assert_text_exact(received: str, expected: str, step_index: int) -> None:
    """Assert received text matches exactly."""
    if received != expected:
        msg = (
            f"Step {step_index}: expected exact text:\n"
            f"  {expected!r}\n"
            f"got:\n"
            f"  {received!r}"
        )
        raise AssertionError(msg)


def assert_contains(received: str, substring: str, step_index: int) -> None:
    """Assert received text contains the substring."""
    if substring not in received:
        msg = (
            f"Step {step_index}: expected text to contain:\n"
            f"  {substring!r}\n"
            f"got:\n"
            f"  {received!r}"
        )
        raise AssertionError(msg)


def assert_matches(received: str, pattern: str, step_index: int) -> None:
    """Assert received text matches the regex pattern."""
    if not re.search(pattern, received):
        msg = (
            f"Step {step_index}: expected text to match pattern:\n"
            f"  {pattern!r}\n"
            f"got:\n"
            f"  {received!r}"
        )
        raise AssertionError(msg)


def assert_json(received: str, expected: Any, step_index: int) -> None:  # noqa: ANN401
    """
    Assert received JSON matches expected structure.

    If expected is a dict, checks that all expected keys/values are present
    in the received data (subset match). Otherwise, checks for equality.
    """
    try:
        data = json.loads(received)
    except (json.JSONDecodeError, TypeError) as exc:
        msg = f"Step {step_index}: expected JSON but could not parse:\n  {received!r}"
        raise AssertionError(msg) from exc

    if isinstance(expected, dict):
        if not isinstance(data, dict):
            msg = f"Step {step_index}: expected JSON object, got:\n  {data!r}"
            raise AssertionError(msg)  # noqa: TRY004
        for key, value in expected.items():
            if key not in data:
                msg = (
                    f"Step {step_index}: expected key {key!r} in JSON, "
                    f"got keys: {list(data.keys())}"
                )
                raise AssertionError(msg)
            if data[key] != value:
                msg = (
                    f"Step {step_index}: key {key!r}: "
                    f"expected {value!r}, got {data[key]!r}"
                )
                raise AssertionError(msg)
    elif data != expected:
        msg = f"Step {step_index}: expected JSON:\n  {expected!r}\ngot:\n  {data!r}"
        raise AssertionError(msg)
