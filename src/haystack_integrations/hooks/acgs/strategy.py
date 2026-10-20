# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""ACGS governance as a Haystack Human-in-the-Loop ``ConfirmationStrategy``."""

from __future__ import annotations

import logging
from typing import Any

from acgs_lite import Constitution
from haystack.core.serialization import component_to_dict, default_from_dict, default_to_dict
from haystack.hooks.human_in_the_loop import ToolExecutionDecision
from haystack.hooks.human_in_the_loop.types import ConfirmationStrategy
from haystack.utils.callable_serialization import deserialize_callable, serialize_callable
from haystack.utils.deserialization import deserialize_component_inplace

from haystack_integrations.hooks.acgs._governance import (
    GovernanceDecision,
    ToolCallFormatter,
    ToolCallGovernor,
    Verdict,
)

logger = logging.getLogger(__name__)

DENY_FEEDBACK_TEMPLATE = "Tool call '{tool_name}' was not executed. {reason}"
REVIEW_UNAVAILABLE_FEEDBACK_TEMPLATE = (
    "Tool call '{tool_name}' was not executed. {reason} No review strategy is configured, so the call was denied."
)
REVIEW_FAILED_FEEDBACK_TEMPLATE = "Tool call '{tool_name}' was not executed because the review step failed."
MODIFIED_DENIED_FEEDBACK_TEMPLATE = (
    "Tool call '{tool_name}' was not executed. The reviewed parameters were rejected. {reason}"
)


class ACGSConfirmationStrategy:
    """
    Apply an ACGS constitution to every tool call the Haystack ``Agent`` is about to run.

    Register it inside Haystack's native ``ConfirmationHook`` under the ``before_tool`` hook point::

        from haystack.components.agents import Agent
        from haystack.hooks.human_in_the_loop import ConfirmationHook
        from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy

        agent = Agent(
            chat_generator=...,
            tools=[...],
            hooks={"before_tool": [ConfirmationHook(confirmation_strategies={"*": ACGSConfirmationStrategy()})]},
        )

    For each tool call the tool name and arguments are rendered with ``format_tool_call`` (or a custom
    ``tool_call_formatter``) and validated with ``acgs_lite.GovernanceEngine``. The verdict is mapped to a
    native ``ToolExecutionDecision``:

    - ``allow``: the call runs with its original parameters;
    - ``deny``: the call is rejected; the ``ConfirmationHook`` adds a tool-result message with the reason so the
      model can react;
    - ``review``: the call is handed to ``review_strategy`` (for example a ``BlockingConfirmationStrategy``).
      Without a ``review_strategy`` it is rejected. If the reviewer modifies the parameters, the modified call is
      evaluated again and rejected unless it is allowed or again only needs review.

    Any error while evaluating, or while running the review strategy, rejects the call.
    """

    def __init__(
        self,
        *,
        constitution: Constitution | None = None,
        review_strategy: ConfirmationStrategy | None = None,
        agent_id: str = "haystack-agent",
        tool_call_formatter: ToolCallFormatter | None = None,
    ) -> None:
        """
        Create the strategy.

        :param constitution: The ACGS constitution to enforce. Defaults to ``acgs_lite.Constitution.default()``.
        :param review_strategy: Native Haystack confirmation strategy used when a rule requires human review.
            When ``None``, calls that require review are rejected.
        :param agent_id: Agent identifier recorded by ACGS for each validation.
        :param tool_call_formatter: Optional function ``(tool_name, arguments) -> str`` that replaces
            ``format_tool_call``. It must be importable to serialize the strategy, and on Haystack versions with a
            deserialization allowlist its module must be allowlisted to deserialize it.
        """
        self.constitution = constitution if constitution is not None else Constitution.default()
        self.review_strategy = review_strategy
        self.agent_id = agent_id
        self.tool_call_formatter = tool_call_formatter
        self._governor = ToolCallGovernor(self.constitution, agent_id=agent_id, tool_call_formatter=tool_call_formatter)

    def evaluate(self, tool_name: str, tool_params: dict[str, Any]) -> GovernanceDecision:
        """
        Evaluate a tool call without executing anything. Never raises.

        :param tool_name: Name of the tool.
        :param tool_params: Arguments of the tool call.
        :returns: The ACGS governance decision.
        """
        return self._governor.evaluate(tool_name, tool_params)

    def run(
        self,
        *,
        tool_name: str,
        tool_description: str,
        tool_params: dict[str, Any],
        tool_call_id: str | None = None,
        confirmation_strategy_context: dict[str, Any] | None = None,
    ) -> ToolExecutionDecision:
        """
        Decide whether a tool call may run.

        :param tool_name: Name of the tool to be executed.
        :param tool_description: Description of the tool.
        :param tool_params: Parameters the model passed to the tool.
        :param tool_call_id: ID of the tool call, used to bind the decision to the call.
        :param confirmation_strategy_context: Request-scoped resources, forwarded to ``review_strategy``.
        :returns: The tool execution decision.
        """
        decision = self.evaluate(tool_name, tool_params)
        if decision.verdict is not Verdict.REVIEW or self.review_strategy is None:
            return self._decision_without_review(decision, tool_params, tool_call_id)
        try:
            reviewed = self.review_strategy.run(
                tool_name=tool_name,
                tool_description=tool_description,
                tool_params=tool_params,
                tool_call_id=tool_call_id,
                confirmation_strategy_context=confirmation_strategy_context,
            )
        except Exception:
            logger.warning("ACGS review strategy failed for tool '%s'; rejecting the call.", tool_name, exc_info=True)
            return self._reject(tool_name, tool_call_id, REVIEW_FAILED_FEEDBACK_TEMPLATE.format(tool_name=tool_name))
        return self._check_review_result(reviewed, tool_name, tool_params, tool_call_id)

    async def run_async(
        self,
        *,
        tool_name: str,
        tool_description: str,
        tool_params: dict[str, Any],
        tool_call_id: str | None = None,
        confirmation_strategy_context: dict[str, Any] | None = None,
    ) -> ToolExecutionDecision:
        """
        Async version of ``run``; awaits ``review_strategy.run_async`` when review is needed.

        :param tool_name: Name of the tool to be executed.
        :param tool_description: Description of the tool.
        :param tool_params: Parameters the model passed to the tool.
        :param tool_call_id: ID of the tool call, used to bind the decision to the call.
        :param confirmation_strategy_context: Request-scoped resources, forwarded to ``review_strategy``.
        :returns: The tool execution decision.
        """
        decision = self.evaluate(tool_name, tool_params)
        if decision.verdict is not Verdict.REVIEW or self.review_strategy is None:
            return self._decision_without_review(decision, tool_params, tool_call_id)
        try:
            reviewed = await self.review_strategy.run_async(
                tool_name=tool_name,
                tool_description=tool_description,
                tool_params=tool_params,
                tool_call_id=tool_call_id,
                confirmation_strategy_context=confirmation_strategy_context,
            )
        except Exception:
            logger.warning("ACGS review strategy failed for tool '%s'; rejecting the call.", tool_name, exc_info=True)
            return self._reject(tool_name, tool_call_id, REVIEW_FAILED_FEEDBACK_TEMPLATE.format(tool_name=tool_name))
        return self._check_review_result(reviewed, tool_name, tool_params, tool_call_id)

    def _decision_without_review(
        self, decision: GovernanceDecision, tool_params: dict[str, Any], tool_call_id: str | None
    ) -> ToolExecutionDecision:
        tool_name = decision.tool_name
        if decision.verdict is Verdict.ALLOW:
            return ToolExecutionDecision(
                tool_name=tool_name, execute=True, tool_call_id=tool_call_id, final_tool_params=tool_params
            )
        template = (
            REVIEW_UNAVAILABLE_FEEDBACK_TEMPLATE if decision.verdict is Verdict.REVIEW else DENY_FEEDBACK_TEMPLATE
        )
        return self._reject(tool_name, tool_call_id, template.format(tool_name=tool_name, reason=decision.reason))

    def _check_review_result(
        self, reviewed: Any, tool_name: str, tool_params: dict[str, Any], tool_call_id: str | None
    ) -> ToolExecutionDecision:
        if not isinstance(reviewed, ToolExecutionDecision):
            logger.warning("ACGS review strategy returned %s instead of a ToolExecutionDecision.", type(reviewed))
            return self._reject(tool_name, tool_call_id, REVIEW_FAILED_FEEDBACK_TEMPLATE.format(tool_name=tool_name))
        if not reviewed.execute:
            return reviewed
        final_params = reviewed.final_tool_params if reviewed.final_tool_params is not None else tool_params
        if final_params != tool_params:
            recheck = self.evaluate(tool_name, final_params)
            if recheck.verdict is Verdict.DENY:
                return self._reject(
                    tool_name,
                    tool_call_id,
                    MODIFIED_DENIED_FEEDBACK_TEMPLATE.format(tool_name=tool_name, reason=recheck.reason),
                )
        return reviewed

    @staticmethod
    def _reject(tool_name: str, tool_call_id: str | None, feedback: str) -> ToolExecutionDecision:
        return ToolExecutionDecision(tool_name=tool_name, execute=False, tool_call_id=tool_call_id, feedback=feedback)

    def to_dict(self) -> dict[str, Any]:
        """
        Serialize the strategy. The constitution is stored as YAML.

        :returns: Dictionary with serialized data.
        """
        return default_to_dict(
            self,
            constitution=self.constitution.to_yaml(),
            review_strategy=(
                component_to_dict(obj=self.review_strategy, name="review_strategy")
                if self.review_strategy is not None
                else None
            ),
            agent_id=self.agent_id,
            tool_call_formatter=(
                serialize_callable(self.tool_call_formatter) if self.tool_call_formatter is not None else None
            ),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ACGSConfirmationStrategy:
        """
        Deserialize the strategy.

        :param data: Dictionary produced by ``to_dict``.
        :returns: The deserialized strategy.
        """
        init_params = data.get("init_parameters", {})
        if isinstance(init_params.get("constitution"), str):
            init_params["constitution"] = Constitution.from_yaml_str(init_params["constitution"])
        if init_params.get("review_strategy") is not None:
            deserialize_component_inplace(init_params, key="review_strategy")
        if init_params.get("tool_call_formatter") is not None:
            init_params["tool_call_formatter"] = deserialize_callable(init_params["tool_call_formatter"])
        return default_from_dict(cls, data)
