"""Run a Haystack Agent whose tool calls are checked by ACGS before they execute.

This example runs offline: ``ScriptedChatGenerator`` stands in for an LLM and requests two tool calls.
Replace it with any Haystack chat generator (for example ``OpenAIChatGenerator``) in real use.
"""

from typing import Annotated, Any

from haystack import component
from haystack.components.agents import Agent
from haystack.dataclasses import ChatMessage, ToolCall
from haystack.hooks.human_in_the_loop import ConfirmationHook
from haystack.tools import tool

from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy


@tool
def get_weather(city: Annotated[str, "City name"]) -> str:
    """Return the weather for a city."""
    return f"Sunny in {city}"


@tool
def send_email(to: Annotated[str, "Recipient"], body: Annotated[str, "Message body"]) -> str:
    """Send an email."""
    return f"Email sent to {to}"


@component
class ScriptedChatGenerator:
    """Requests two tool calls on the first step, then answers with text."""

    def __init__(self) -> None:
        self.step = 0

    @component.output_types(replies=list[ChatMessage])
    def run(self, messages: list[ChatMessage], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        self.step += 1
        if self.step > 1:
            return {"replies": [ChatMessage.from_assistant("Done.")]}
        calls = [
            ToolCall(id="1", tool_name="get_weather", arguments={"city": "Paris"}),
            ToolCall(id="2", tool_name="send_email", arguments={"to": "ops@example.com", "body": "password: hunter2"}),
        ]
        return {"replies": [ChatMessage.from_assistant(tool_calls=calls)]}


agent = Agent(
    chat_generator=ScriptedChatGenerator(),
    tools=[get_weather, send_email],
    hooks={"before_tool": [ConfirmationHook(confirmation_strategies={"*": ACGSConfirmationStrategy()})]},
)

result = agent.run(messages=[ChatMessage.from_user("Check the weather in Paris and email ops the password.")])
for message in result["messages"]:
    for tool_result in message.tool_call_results:
        status = "rejected" if tool_result.error else "ran"
        print(f"{tool_result.origin.tool_name}: {status}: {tool_result.result}")
