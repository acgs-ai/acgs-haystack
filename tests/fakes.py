# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""Test doubles that implement real Haystack protocols. No network access, no API keys."""

from __future__ import annotations

from typing import Any

from haystack import component, default_from_dict, default_to_dict
from haystack.dataclasses import ChatMessage, ToolCall
from haystack.hooks.human_in_the_loop import ConfirmationUIResult
from haystack.tools import Tool


@component
class ScriptedChatGenerator:
    """A chat generator that replays scripted assistant replies and records what it was sent.

    Each script entry is either a string (a final text reply) or a list of ``(tool_name, arguments)``
    tuples (one assistant message requesting those tool calls).
    """

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [])
        self.calls: list[list[ChatMessage]] = []

    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools: list[Tool] | None = None, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(list(messages))
        step = self.script.pop(0) if self.script else "done"
        if isinstance(step, str):
            return {"replies": [ChatMessage.from_assistant(step)]}
        tool_calls = [
            ToolCall(tool_name=name, arguments=arguments, id=f"call_{len(self.calls)}_{index}")
            for index, (name, arguments) in enumerate(step)
        ]
        return {"replies": [ChatMessage.from_assistant(tool_calls=tool_calls)]}

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(self, script=self.script)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScriptedChatGenerator:
        return default_from_dict(cls, data)


class ScriptedConfirmationUI:
    """A ``ConfirmationUI`` that returns pre-defined answers instead of prompting a person."""

    def __init__(self, answers: list[ConfirmationUIResult] | None = None) -> None:
        self.answers = list(answers or [])
        self.prompts: list[tuple[str, dict[str, Any]]] = []

    def get_user_confirmation(
        self, tool_name: str, tool_description: str, tool_params: dict[str, Any]
    ) -> ConfirmationUIResult:
        self.prompts.append((tool_name, dict(tool_params)))
        return self.answers.pop(0)

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScriptedConfirmationUI:
        return default_from_dict(cls, data)


def shout_formatter(tool_name: str, arguments: Any) -> str:
    """Importable custom formatter used by serialization tests."""
    return f"{tool_name.upper()} {arguments}"
