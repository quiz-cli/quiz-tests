"""Test step interpreter — the core engine."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

import httpx
from httpx_ws.transport import ASGIWebSocketTransport
from quiz_common.models import Quiz
from ruamel.yaml import YAML

from framework.actors import Actor
from framework.app import reset_app
from framework.assertions import (
    assert_contains,
    assert_json,
    assert_matches,
    assert_text_exact,
)
from framework.models import ActorRole, Step, TestCase

if TYPE_CHECKING:
    from pathlib import Path

BASE_URL = "http://test"


def load_test(test_dir: Path) -> TestCase:
    """Load and validate a test YAML file."""
    yaml = YAML(typ="safe")
    test_path = test_dir / "test.yaml"
    data = yaml.load(test_path)
    return TestCase(**data)


def load_quiz_data(test: TestCase, test_dir: Path) -> dict[str, Any]:
    """Load quiz data from file or inline, validate, and return as dict."""
    if test.quiz is not None:
        quiz = Quiz(**test.quiz)
        return quiz.model_dump()

    assert test.quiz_file is not None  # noqa: S101
    quiz_path = test_dir / test.quiz_file
    yaml = YAML(typ="safe")
    raw = yaml.load(quiz_path)
    quiz = Quiz(**raw)
    return quiz.model_dump()


async def run_test(test_dir: Path) -> None:
    """Execute a full test from a directory."""
    test = load_test(test_dir)
    quiz_data = load_quiz_data(test, test_dir)

    app = reset_app()
    transport = ASGIWebSocketTransport(app)

    async with (
        httpx.AsyncClient(transport=transport) as client,
        AsyncExitStack() as exit_stack,
    ):
        actors: dict[str, Actor] = {}
        for name, actor_def in test.actors.items():
            actors[name] = Actor(
                name=name,
                role=actor_def.role,
                client=client,
                exit_stack=exit_stack,
            )

        for i, step in enumerate(test.steps):
            await _execute_step(i, step, actors, quiz_data)


async def _execute_step(  # noqa: C901
    index: int,
    step: Step,
    actors: dict[str, Actor],
    quiz_data: dict[str, Any],
) -> None:
    """Dispatch a single test step to the appropriate actor."""
    action = step.action

    if action == "comment":
        return

    assert step.actor is not None  # noqa: S101
    actor = actors[step.actor]

    if action == "connect":
        await actor.connect(
            BASE_URL,
            quiz_data=quiz_data if actor.role == ActorRole.admin else None,
        )

    elif action == "send":
        assert step.data is not None  # noqa: S101
        await actor.send(step.data)

    elif action == "expect":
        timeout = step.timeout if step.timeout is not None else 5.0
        received = await actor.receive_raw(timeout=timeout)

        if step.text is not None:
            assert_text_exact(received, step.text, index)
        if step.contains is not None:
            assert_contains(received, step.contains, index)
        if step.matches is not None:
            assert_matches(received, step.matches, index)
        if step.expected_json is not None:
            assert_json(received, step.expected_json, index)

    elif action == "expect_nothing":
        timeout = step.timeout if step.timeout is not None else 0.5
        await actor.expect_nothing(timeout=timeout)

    elif action == "disconnect":
        await actor.disconnect()

    elif action == "sleep":
        seconds = step.seconds if step.seconds is not None else 0.5
        await asyncio.sleep(seconds)

    else:
        msg = f"Step {index}: unknown action '{action}'"
        raise ValueError(msg)
