"""Pydantic models for validating test YAML files."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActorRole(StrEnum):
    """Supported actor roles."""

    admin = "admin"
    client = "client"


class ActorDef(BaseModel):
    """Definition of a single actor in a test."""

    role: ActorRole


class Step(BaseModel):
    """A single step in a test."""

    model_config = ConfigDict(populate_by_name=True)

    actor: str | None = None
    action: str

    # send
    data: str | dict[str, Any] | None = None

    # expect
    text: str | None = None
    expected_json: Any = Field(default=None, alias="json")
    contains: str | None = None
    matches: str | None = None

    # expect_nothing / sleep
    timeout: float | None = None
    seconds: float | None = None

    @model_validator(mode="after")
    def validate_actor_required(self) -> Step:
        """Ensure actor is present for actions that need it."""
        if self.action != "comment" and self.actor is None:
            msg = f"'actor' is required for action '{self.action}'"
            raise ValueError(msg)
        return self


class TestCase(BaseModel):
    """Top-level test case structure."""

    name: str
    description: str = ""
    quiz_file: str | None = None
    quiz: dict[str, Any] | None = None
    actors: dict[str, ActorDef]
    steps: list[Step] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_quiz_source(self) -> TestCase:
        """Ensure exactly one of quiz_file or quiz is provided."""
        if self.quiz_file is None and self.quiz is None:
            msg = "Either 'quiz_file' or 'quiz' must be provided"
            raise ValueError(msg)
        if self.quiz_file is not None and self.quiz is not None:
            msg = "Only one of 'quiz_file' or 'quiz' can be provided"
            raise ValueError(msg)
        return self
