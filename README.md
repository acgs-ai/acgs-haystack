# acgs-haystack

[![CI](https://github.com/acgs-ai/acgs-haystack/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/acgs-ai/acgs-haystack/actions/workflows/ci.yml)
[![PyPI - Version](https://img.shields.io/pypi/v/acgs-haystack.svg)](https://pypi.org/project/acgs-haystack)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/acgs-haystack.svg)](https://pypi.org/project/acgs-haystack)

Rule-based checks for the tool calls a Haystack `Agent` makes, using the [ACGS](https://pypi.org/project/acgs-lite/)
constitution engine from `acgs-lite`.

`acgs-haystack` provides one class, `ACGSConfirmationStrategy`. It plugs into Haystack's own
`ConfirmationHook` at the `before_tool` hook point, so tool calls are approved, rejected, or sent to a human
reviewer using the same mechanism as Haystack's Human-in-the-Loop confirmation. It does not wrap the `Agent`,
its tools, or the chat generator.

- [Installation](#installation)
- [Usage](#usage)
- [How a tool call is evaluated](#how-a-tool-call-is-evaluated)
- [Human review](#human-review)
- [Custom constitutions](#custom-constitutions)
- [Limitations](#limitations)
- [Development](#development)
- [License](#license)

## Installation

```console
pip install acgs-haystack
```

Requires Python 3.11+, `haystack-ai>=3.0.0` and `acgs-lite>=3.0.1`.

## Usage

The example below runs offline. `ScriptedChatGenerator` stands in for an LLM; in real use, pass any Haystack chat
generator instead (for example `OpenAIChatGenerator`). The same file is in
[`examples/quickstart.py`](examples/quickstart.py) and is executed by the test suite.

```python
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
```

Output:

```text
send_email: rejected: Tool call 'send_email' was not executed. Blocked by ACGS rule ACGS-006 (Agents must not expose sensitive data in responses).
get_weather: ran: Sunny in Paris
```

The rejected call never runs. `ConfirmationHook` replaces it with an error tool result carrying the reason, so the
model sees why the call was refused and can continue. Argument values are not included in the reason.

To govern only some tools, use tool names or tuples of tool names instead of `"*"` as keys of
`confirmation_strategies`. Note that a more specific key takes precedence over `"*"`: a tool with its own entry is
not checked by an ACGS strategy registered under `"*"`.

## How a tool call is evaluated

1. The tool name and arguments are rendered as text by `format_tool_call`:

   ```python
   from haystack_integrations.hooks.acgs import format_tool_call

   print(format_tool_call("update_config", {"service": "billing", "change": "disable\nlogging", "dryRun": False}))
   ```

   ```text
   tool: update_config
   arguments:
   change: disable logging
   dryRun: false
   service: billing
   normalized: update config | dry run
   ```

   Each argument leaf is one `path: value` line (nested values use `a.b[0].c` paths), so both argument names and
   values are visible to rules. Whitespace inside values is collapsed so phrases split across lines still match.
   Identifiers written in `snake_case`, `kebab-case` or `camelCase` (the tool name, argument names, and
   single-token string values) are repeated as plain words on the `normalized:` line, so the keyword
   `skip audit` also matches a tool called `skip_audit`. Nothing is truncated.
   Pass `tool_call_formatter=` to use a different representation.

2. The text is validated with `acgs_lite.GovernanceEngine.validate` against the constitution
   (`Constitution.default()` unless you pass one).

3. The result is mapped to a Haystack `ToolExecutionDecision`:

   | ACGS outcome | Decision |
   |---|---|
   | No blocking rule matched (warn-only rules may match) | execute with the original parameters |
   | Only `require_human_review` / `escalate_to_senior` rules matched | send to `review_strategy`; reject if none is configured |
   | Any `block`, `block_and_notify` or `halt_and_alert` rule matched | reject |
   | A rule ID not found in the constitution, or an invalid result without a rule | reject |
   | Any exception while formatting, validating, or reviewing | reject (fail-closed) |

   A matched blocking rule is never downgraded to review because a review rule matched as well.
   The denial reason then lists only the blocking rules after "Blocked by" and names the matched review
   rules separately.

`ACGSConfirmationStrategy.evaluate(tool_name, params)` returns the underlying `GovernanceDecision`
(`verdict`, `reason`, `rule_ids`, `warning_rule_ids`, `error`) without running anything.

## Human review

Rules whose workflow action is `require_human_review` or `escalate_to_senior` (in the default constitution:
`ACGS-002` and `ACGS-005`) produce a review verdict. Pass a native Haystack confirmation strategy to handle it:

```python
from haystack.hooks.human_in_the_loop import AlwaysAskPolicy, BlockingConfirmationStrategy, SimpleConsoleUI

from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy

strategy = ACGSConfirmationStrategy(
    review_strategy=BlockingConfirmationStrategy(
        confirmation_policy=AlwaysAskPolicy(), confirmation_ui=SimpleConsoleUI()
    )
)
```

The reviewer is only consulted for review verdicts. If the reviewer modifies the parameters, the modified call is
evaluated again and rejected if a blocking rule matches. Use `AlwaysAskPolicy`: with `NeverAskPolicy` the
review strategy approves without asking.

## Custom constitutions

```python
from acgs_lite import Constitution, Rule, Severity, ViolationAction

from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy

constitution = Constitution.from_rules(
    [
        Rule(
            id="NO-DESTRUCTIVE-SQL",
            text="No destructive SQL",
            severity=Severity.CRITICAL,
            keywords=["drop table", "truncate table"],
        ),
        Rule(
            id="REFUND-REVIEW",
            text="Refunds need approval",
            severity=Severity.HIGH,
            keywords=["refund"],
            workflow_action=ViolationAction.REQUIRE_HUMAN_REVIEW,
        ),
    ],
    name="payments",
)
strategy = ACGSConfirmationStrategy(constitution=constitution, agent_id="payments-agent")
```

`Constitution.from_yaml("rules.yaml")` works as well. The strategy supports `to_dict` / `from_dict`, so an
`Agent` using it can be serialized; the constitution is stored as YAML.

## Limitations

- `Constitution.default()` focuses on governance integrity (self-approval, skipping audits, privilege escalation)
  and on secrets and personal data (`password`, `api_key`, `sk-...` and `ghp_...` tokens, US SSN
  patterns). It does **not** cover destructive operations such as `rm -rf` or `DROP TABLE`. Write rules for the
  tools you expose. `tests/test_default_constitution.py` lists what the default rules do and do not catch.
- Keyword rules in acgs-lite 3.0.1 match literal, case-insensitive substrings. They do not understand negation
  ("never share the password" matches `password`) and do not treat `-`, `_` and spaces as equivalent
  (`api-key` does not match the keyword `api_key`).
- Because argument names are part of the evaluated text, a tool with a parameter named `password` is rejected
  under the default constitution whenever it is called.
- Tool calls naming a tool the Agent does not have are passed through by `ConfirmationHook` without consulting any
  strategy; Haystack then reports them as unknown tools and they do not run.
- Other `before_tool` hooks registered after the `ConfirmationHook` can still rewrite tool calls. Register
  the ACGS hook last if other hooks modify tool calls.

## Development

```console
hatch run fmt-check          # ruff lint + format check
hatch run test:types         # mypy --strict
hatch run test:cov           # pytest with coverage (fails under 95%)
hatch run test-min:all       # tests against haystack-ai 3.0.0 and acgs-lite 3.0.1
```

## License

`acgs-haystack` is distributed under the terms of the [Apache-2.0](LICENSE) license.
