"""Run one allowed and one denied Haystack tool call offline, then print a decision trail."""

import json
import os
from dataclasses import asdict
from typing import Annotated, Any

# Disable telemetry before importing Haystack; the scripted generator uses no model service.
os.environ["HAYSTACK_TELEMETRY_ENABLED"] = "False"

from haystack import component
from haystack.components.agents import Agent
from haystack.dataclasses import ChatMessage, ToolCall
from haystack.hooks.human_in_the_loop import ConfirmationHook
from haystack.tools import tool

from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy, GovernanceDecision


class AuditedStrategy(ACGSConfirmationStrategy):
    """Collect decisions through the public API without retaining argument values."""

    def __init__(self) -> None:
        super().__init__()
        self.decisions: list[GovernanceDecision] = []

    def evaluate(self, tool_name: str, tool_params: dict[str, Any]) -> GovernanceDecision:
        decision = super().evaluate(tool_name, tool_params)
        self.decisions.append(decision)
        return decision


@component
class ScriptedChatGenerator:
    """Request two prewritten tool calls, then finish without a network request."""

    def __init__(self) -> None:
        self.step = 0

    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.step += 1
        if self.step > 1:
            return {"replies": [ChatMessage.from_assistant("Done.")]}
        calls = [
            ToolCall(id=f"note-{index}", tool_name="record_note", arguments={"body": body})
            for index, body in enumerate(["The meeting starts at noon.", "The admin password is demo-secret."])
        ]
        return {"replies": [ChatMessage.from_assistant(tool_calls=calls)]}


@tool
def record_note(body: Annotated[str, "Message body"]) -> str:
    """Record a demo note by printing it locally."""
    print(f"Executed record_note: {body}")
    return "Note recorded."


def main() -> None:
    strategy = AuditedStrategy()
    agent = Agent(
        chat_generator=ScriptedChatGenerator(),
        tools=[record_note],
        hooks={"before_tool": [ConfirmationHook(confirmation_strategies={"*": strategy})]},
    )
    agent.run(messages=[ChatMessage.from_user("Record the two notes.")])
    print("Audit trail (application decisions):")
    for decision in strategy.decisions:
        print(json.dumps(asdict(decision), sort_keys=True))


if __name__ == "__main__":
    main()
