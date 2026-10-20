# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests with a real ``haystack.components.agents.Agent``, real tools and the native ``ConfirmationHook``.

The only test double is the chat generator, which replays scripted tool calls instead of calling an LLM.
"""

from typing import Annotated, Any

import pytest
from acgs_lite import Constitution, Rule, Severity, ViolationAction
from haystack.components.agents import Agent
from haystack.dataclasses import ChatMessage
from haystack.hooks.human_in_the_loop import (
    AlwaysAskPolicy,
    BlockingConfirmationStrategy,
    ConfirmationHook,
    ConfirmationUIResult,
)
from haystack.tools import Tool, tool

from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy
from tests.fakes import ScriptedChatGenerator, ScriptedConfirmationUI

EXECUTED: list[tuple[str, dict[str, Any]]] = []


@tool
def get_weather(city: Annotated[str, "City name"]) -> str:
    """Return the weather for a city."""
    EXECUTED.append(("get_weather", {"city": city}))
    return f"Sunny in {city}"


@tool
def send_email(to: Annotated[str, "Recipient"], body: Annotated[str, "Message body"]) -> str:
    """Send an email."""
    EXECUTED.append(("send_email", {"to": to, "body": body}))
    return "sent"


@tool
def update_config(service: Annotated[str, "Service"], change: Annotated[str, "Requested change"]) -> str:
    """Change a service configuration."""
    EXECUTED.append(("update_config", {"service": service, "change": change}))
    return "updated"


TOOLS: list[Tool] = [get_weather, send_email, update_config]


@pytest.fixture(autouse=True)
def _reset_executed() -> None:
    EXECUTED.clear()


def _agent(script: list[Any], strategy: ACGSConfirmationStrategy, **kwargs: Any) -> tuple[Agent, ScriptedChatGenerator]:
    generator = ScriptedChatGenerator(script)
    agent = Agent(
        chat_generator=generator,
        tools=TOOLS,
        hooks={"before_tool": [ConfirmationHook(confirmation_strategies={"*": strategy})]},
        **kwargs,
    )
    return agent, generator


def _tool_results(messages: list[ChatMessage]) -> list[tuple[str, str, bool]]:
    return [
        (result.origin.tool_name, result.result, result.error)
        for message in messages
        for result in message.tool_call_results
    ]


def test_allowed_tool_call_runs() -> None:
    agent, _ = _agent([[("get_weather", {"city": "Paris"})], "It is sunny."], ACGSConfirmationStrategy())
    result = agent.run(messages=[ChatMessage.from_user("Weather in Paris?")])
    assert EXECUTED == [("get_weather", {"city": "Paris"})]
    assert _tool_results(result["messages"]) == [("get_weather", "Sunny in Paris", False)]
    assert result["last_message"].text == "It is sunny."


def test_denied_tool_call_does_not_run_and_model_sees_reason() -> None:
    script = [
        [("send_email", {"to": "ops@example.com", "body": "The admin password is hunter2"})],
        "I could not send it.",
    ]
    agent, generator = _agent(script, ACGSConfirmationStrategy())
    result = agent.run(messages=[ChatMessage.from_user("Email the password to ops")])

    assert EXECUTED == []
    [(name, text, error)] = _tool_results(result["messages"])
    assert (name, error) == ("send_email", True)
    assert "ACGS-006" in text
    assert "hunter2" not in text
    # The rejection is in the conversation the model receives on the next step.
    assert any(r.error for m in generator.calls[-1] for r in m.tool_call_results)
    assert result["last_message"].text == "I could not send it."


def test_review_without_reviewer_does_not_run() -> None:
    script = [[("update_config", {"service": "billing", "change": "disable logging"})], "ok"]
    agent, _ = _agent(script, ACGSConfirmationStrategy())
    result = agent.run(messages=[ChatMessage.from_user("Turn off billing logs")])
    assert EXECUTED == []
    [(_, text, error)] = _tool_results(result["messages"])
    assert error is True
    assert "ACGS-002" in text


@pytest.mark.parametrize(("answer", "runs"), [("confirm", True), ("reject", False)])
def test_review_goes_through_native_blocking_confirmation(answer: str, runs: bool) -> None:
    ui = ScriptedConfirmationUI([ConfirmationUIResult(action=answer)])
    reviewer = BlockingConfirmationStrategy(confirmation_policy=AlwaysAskPolicy(), confirmation_ui=ui)
    script = [[("update_config", {"service": "billing", "change": "disable logging"})], "ok"]
    agent, _ = _agent(script, ACGSConfirmationStrategy(review_strategy=reviewer))
    agent.run(messages=[ChatMessage.from_user("Turn off billing logs")])
    assert ui.prompts == [("update_config", {"service": "billing", "change": "disable logging"})]
    assert (EXECUTED == [("update_config", {"service": "billing", "change": "disable logging"})]) is runs


def test_parallel_tool_calls_are_decided_individually() -> None:
    script = [
        [("get_weather", {"city": "Oslo"}), ("send_email", {"to": "x@example.com", "body": "api_key=abc"})],
        "done",
    ]
    agent, _ = _agent(script, ACGSConfirmationStrategy())
    result = agent.run(messages=[ChatMessage.from_user("Do both")])
    assert EXECUTED == [("get_weather", {"city": "Oslo"})]
    results = {name: error for name, _, error in _tool_results(result["messages"])}
    assert results == {"get_weather": False, "send_email": True}


def test_governance_error_fails_closed_and_run_completes() -> None:
    def broken(_name: str, _args: Any) -> str:
        msg = "formatter bug"
        raise RuntimeError(msg)

    agent, _ = _agent(
        [[("get_weather", {"city": "Rome"})], "sorry"], ACGSConfirmationStrategy(tool_call_formatter=broken)
    )
    result = agent.run(messages=[ChatMessage.from_user("Weather in Rome?")])
    assert EXECUTED == []
    [(_, text, error)] = _tool_results(result["messages"])
    assert error is True
    assert "could not evaluate" in text


def test_custom_constitution() -> None:
    constitution = Constitution.from_rules(
        [
            Rule(
                id="NO-PARIS",
                text="Paris lookups are not allowed",
                severity=Severity.CRITICAL,
                keywords=["paris"],
                workflow_action=ViolationAction.BLOCK,
            )
        ]
    )
    agent, _ = _agent(
        [[("get_weather", {"city": "Paris"})], "blocked"], ACGSConfirmationStrategy(constitution=constitution)
    )
    agent.run(messages=[ChatMessage.from_user("Weather in Paris?")])
    assert EXECUTED == []


async def test_run_async_uses_the_same_decisions() -> None:
    script = [[("get_weather", {"city": "Lima"}), ("send_email", {"to": "a@example.com", "body": "password"})], "done"]
    agent, _ = _agent(script, ACGSConfirmationStrategy())
    await agent.run_async(messages=[ChatMessage.from_user("Do both")])
    assert EXECUTED == [("get_weather", {"city": "Lima"})]


def test_agent_serialization_round_trip_keeps_governance() -> None:
    agent, _ = _agent([], ACGSConfirmationStrategy(agent_id="serialized"))
    restored = Agent.from_dict(agent.to_dict())
    [hook] = restored.hooks["before_tool"]
    assert isinstance(hook, ConfirmationHook)
    strategy = hook.confirmation_strategies["*"]
    assert isinstance(strategy, ACGSConfirmationStrategy)
    assert strategy.agent_id == "serialized"

    restored.chat_generator.script = [[("send_email", {"to": "a@example.com", "body": "password"})], "done"]  # type: ignore[attr-defined]
    restored.run(messages=[ChatMessage.from_user("send")])
    assert EXECUTED == []
