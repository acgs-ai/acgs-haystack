# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``ACGSConfirmationStrategy`` using real Haystack HITL types."""

from __future__ import annotations

from typing import Any

import pytest
from acgs_lite import Constitution, Rule, Severity, ViolationAction
from haystack.hooks.human_in_the_loop import (
    AlwaysAskPolicy,
    BlockingConfirmationStrategy,
    ConfirmationUIResult,
    SimpleConsoleUI,
    ToolExecutionDecision,
)

from haystack_integrations.hooks.acgs import ACGSConfirmationStrategy, Verdict
from tests.fakes import ScriptedConfirmationUI, shout_formatter

REVIEW_RULE = Rule(
    id="T-REVIEW",
    text="refunds need review",
    severity=Severity.HIGH,
    keywords=["refund"],
    workflow_action=ViolationAction.REQUIRE_HUMAN_REVIEW,
)
BLOCK_RULE = Rule(id="T-BLOCK", text="no deletes", severity=Severity.CRITICAL, keywords=["delete everything"])
CONSTITUTION = Constitution.from_rules([REVIEW_RULE, BLOCK_RULE], name="strategy-test")


def _run(strategy: ACGSConfirmationStrategy, params: dict[str, Any], **kwargs: Any) -> ToolExecutionDecision:
    return strategy.run(tool_name="payments", tool_description="Payments API", tool_params=params, **kwargs)


def _reviewer(*answers: ConfirmationUIResult) -> tuple[BlockingConfirmationStrategy, ScriptedConfirmationUI]:
    ui = ScriptedConfirmationUI(list(answers))
    return BlockingConfirmationStrategy(confirmation_policy=AlwaysAskPolicy(), confirmation_ui=ui), ui


class RaisingStrategy:
    def run(self, **_kwargs: Any) -> ToolExecutionDecision:
        msg = "review backend down"
        raise ConnectionError(msg)

    async def run_async(self, **_kwargs: Any) -> ToolExecutionDecision:
        msg = "review backend down"
        raise ConnectionError(msg)


class WrongTypeStrategy:
    def run(self, **_kwargs: Any) -> Any:
        return {"execute": True}

    async def run_async(self, **_kwargs: Any) -> Any:
        return {"execute": True}


class TestDecisions:
    def test_allow_executes_with_original_params(self) -> None:
        decision = _run(ACGSConfirmationStrategy(constitution=CONSTITUTION), {"action": "status"}, tool_call_id="c1")
        assert decision == ToolExecutionDecision(
            tool_name="payments", execute=True, tool_call_id="c1", final_tool_params={"action": "status"}
        )

    def test_deny_rejects_with_feedback(self) -> None:
        decision = _run(ACGSConfirmationStrategy(constitution=CONSTITUTION), {"action": "delete everything"})
        assert decision.execute is False
        assert decision.feedback is not None
        assert "T-BLOCK" in decision.feedback
        assert "not executed" in decision.feedback

    def test_review_without_review_strategy_is_rejected(self) -> None:
        decision = _run(ACGSConfirmationStrategy(constitution=CONSTITUTION), {"action": "refund"})
        assert decision.execute is False
        assert decision.feedback is not None
        assert "No review strategy is configured" in decision.feedback

    def test_review_confirmed_by_human_executes(self) -> None:
        reviewer, ui = _reviewer(ConfirmationUIResult(action="confirm"))
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=reviewer)
        decision = _run(strategy, {"action": "refund", "order": 7}, tool_call_id="c2")
        assert decision.execute is True
        assert decision.final_tool_params == {"action": "refund", "order": 7}
        assert ui.prompts == [("payments", {"action": "refund", "order": 7})]

    def test_review_rejected_by_human_is_rejected(self) -> None:
        reviewer, _ = _reviewer(ConfirmationUIResult(action="reject", feedback="not today"))
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=reviewer)
        decision = _run(strategy, {"action": "refund"})
        assert decision.execute is False
        assert decision.feedback is not None
        assert "not today" in decision.feedback

    def test_review_modified_to_allowed_params_executes(self) -> None:
        reviewer, _ = _reviewer(ConfirmationUIResult(action="modify", new_tool_params={"action": "status"}))
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=reviewer)
        decision = _run(strategy, {"action": "refund"})
        assert decision.execute is True
        assert decision.final_tool_params == {"action": "status"}

    def test_review_modified_to_blocked_params_is_rejected(self) -> None:
        reviewer, _ = _reviewer(
            ConfirmationUIResult(action="modify", new_tool_params={"action": "refund then delete everything"})
        )
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=reviewer)
        decision = _run(strategy, {"action": "refund"})
        assert decision.execute is False
        assert decision.feedback is not None
        assert "reviewed parameters were rejected" in decision.feedback

    def test_review_returning_execute_without_params_keeps_original(self) -> None:
        class ApproveWithoutParams:
            def run(self, **kwargs: Any) -> ToolExecutionDecision:
                return ToolExecutionDecision(tool_name=kwargs["tool_name"], execute=True)

        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=ApproveWithoutParams())  # type: ignore[arg-type]
        assert _run(strategy, {"action": "refund"}).execute is True

    def test_review_context_is_forwarded(self) -> None:
        seen: dict[str, Any] = {}

        class Recorder:
            def run(self, **kwargs: Any) -> ToolExecutionDecision:
                seen.update(kwargs)
                return ToolExecutionDecision(tool_name=kwargs["tool_name"], execute=False, feedback="no")

        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=Recorder())  # type: ignore[arg-type]
        _run(strategy, {"action": "refund"}, tool_call_id="c9", confirmation_strategy_context={"queue": "q"})
        assert seen["confirmation_strategy_context"] == {"queue": "q"}
        assert seen["tool_call_id"] == "c9"
        assert seen["tool_description"] == "Payments API"


class TestFailClosed:
    def test_review_strategy_exception_rejects(self) -> None:
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=RaisingStrategy())  # type: ignore[arg-type]
        decision = _run(strategy, {"action": "refund"})
        assert decision.execute is False
        assert decision.feedback is not None
        assert "review step failed" in decision.feedback

    def test_review_strategy_wrong_return_type_rejects(self) -> None:
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=WrongTypeStrategy())  # type: ignore[arg-type]
        assert _run(strategy, {"action": "refund"}).execute is False

    def test_formatter_exception_rejects(self) -> None:
        def broken(_name: str, _args: Any) -> str:
            msg = "x"
            raise KeyError(msg)

        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, tool_call_formatter=broken)
        decision = _run(strategy, {"action": "status"})
        assert decision.execute is False
        assert decision.feedback is not None
        assert "could not evaluate" in decision.feedback

    def test_review_strategy_is_not_consulted_for_deny(self) -> None:
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=RaisingStrategy())  # type: ignore[arg-type]
        assert _run(strategy, {"action": "refund and delete everything"}).execute is False

    def test_mixed_match_feedback_names_only_the_blocking_rule_as_blocking(self) -> None:
        # Regression (0.1.0): the review rule was listed under "Blocked by" next to the blocking rule.
        decision = _run(ACGSConfirmationStrategy(constitution=CONSTITUTION), {"action": "refund and delete everything"})
        assert decision.execute is False
        assert decision.feedback == (
            "Tool call 'payments' was not executed. Blocked by ACGS rule T-BLOCK (no deletes). "
            "Review rules that also matched: ACGS rule T-REVIEW (refunds need review)."
        )


class TestAsync:
    async def test_allow_and_deny(self) -> None:
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION)
        allowed = await strategy.run_async(tool_name="p", tool_description="", tool_params={"action": "status"})
        denied = await strategy.run_async(
            tool_name="p", tool_description="", tool_params={"action": "delete everything"}
        )
        assert allowed.execute is True
        assert denied.execute is False

    async def test_review_uses_run_async(self) -> None:
        reviewer, _ = _reviewer(ConfirmationUIResult(action="confirm"))
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=reviewer)
        decision = await strategy.run_async(tool_name="p", tool_description="", tool_params={"action": "refund"})
        assert decision.execute is True

    async def test_review_exception_rejects(self) -> None:
        strategy = ACGSConfirmationStrategy(constitution=CONSTITUTION, review_strategy=RaisingStrategy())  # type: ignore[arg-type]
        decision = await strategy.run_async(tool_name="p", tool_description="", tool_params={"action": "refund"})
        assert decision.execute is False


class TestEvaluate:
    def test_evaluate_does_not_execute_anything(self) -> None:
        strategy = ACGSConfirmationStrategy()
        assert strategy.evaluate("send_email", {"body": "password: x"}).verdict is Verdict.DENY
        assert strategy.constitution.hash == Constitution.default().hash


class TestSerialization:
    def test_round_trip_defaults(self) -> None:
        strategy = ACGSConfirmationStrategy()
        data = strategy.to_dict()
        assert data["type"] == "haystack_integrations.hooks.acgs.strategy.ACGSConfirmationStrategy"
        assert data["init_parameters"]["review_strategy"] is None
        assert data["init_parameters"]["tool_call_formatter"] is None
        restored = ACGSConfirmationStrategy.from_dict(data)
        assert restored.constitution.hash == strategy.constitution.hash
        assert restored.agent_id == "haystack-agent"

    def test_round_trip_with_review_strategy_and_formatter(self) -> None:
        reviewer = BlockingConfirmationStrategy(
            confirmation_policy=AlwaysAskPolicy(), confirmation_ui=SimpleConsoleUI()
        )
        strategy = ACGSConfirmationStrategy(
            constitution=CONSTITUTION, review_strategy=reviewer, agent_id="billing", tool_call_formatter=shout_formatter
        )
        restored = ACGSConfirmationStrategy.from_dict(strategy.to_dict())
        assert restored.constitution.hash == CONSTITUTION.hash
        assert isinstance(restored.review_strategy, BlockingConfirmationStrategy)
        assert isinstance(restored.review_strategy.confirmation_policy, AlwaysAskPolicy)
        assert restored.tool_call_formatter is shout_formatter
        assert restored.agent_id == "billing"

    def test_from_dict_without_constitution_uses_default(self) -> None:
        data = {"type": "haystack_integrations.hooks.acgs.strategy.ACGSConfirmationStrategy", "init_parameters": {}}
        restored = ACGSConfirmationStrategy.from_dict(data)
        assert restored.constitution.hash == Constitution.default().hash

    @pytest.mark.parametrize("tool_name", ["payments"])
    def test_restored_strategy_enforces_same_rules(self, tool_name: str) -> None:
        restored = ACGSConfirmationStrategy.from_dict(ACGSConfirmationStrategy(constitution=CONSTITUTION).to_dict())
        decision = restored.run(tool_name=tool_name, tool_description="", tool_params={"action": "delete everything"})
        assert decision.execute is False
