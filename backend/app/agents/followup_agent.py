from __future__ import annotations

import logging

from app.agents.dialogue_llm_client import DialogueLLMClient
from app.agents.dialogue_policy import DialoguePolicy, _unreleased_dynamic_infos
from app.agents.mock_dialogue import MockFollowupAgent
from app.agents.question_contract import (
    adopt_probe_wording,
    count_stage_followups,
    enforce_constraints,
    load_contract,
    probe_coverage_real,
    resolve_probe,
)
from app.agents.schemas import AgentRuntimeContext, FollowupOutput
from app.agents.user_turn_intent import (
    apply_model_resolution_to_context,
    build_stage_incomplete_prompt,
    validate_resolved_evidence,
)
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class FollowupAgent:
    def __init__(
        self,
        llm_client: DialogueLLMClient | None = None,
        mock_agent: MockFollowupAgent | None = None,
        policy: DialoguePolicy | None = None,
    ) -> None:
        self._policy = policy or DialoguePolicy()
        self._llm_client = llm_client
        self._mock_agent = mock_agent or MockFollowupAgent(policy=self._policy)

    @property
    def llm_client(self) -> DialogueLLMClient:
        if self._llm_client is None:
            self._llm_client = DialogueLLMClient()
        return self._llm_client

    def generate(self, context: AgentRuntimeContext) -> FollowupOutput:
        if get_settings().MODEL_GATEWAY_MODE.lower() == "mock":
            return self._mock_agent.generate(context)

        # In real mode DeepSeek is the only semantic judge. Do not pre-classify
        # the answer with keyword rules or feed a local verdict into the model.
        result = self.llm_client.call_followup(context)
        if result.success and isinstance(result.output, FollowupOutput):
            resolved_category = result.output.resolved_response_category
            if resolved_category is None:
                return _model_failure_output(
                    error_code="MISSING_SEMANTIC_CLASSIFICATION",
                    error_reason="DeepSeek omitted resolved_response_category",
                )
            model_evidence = list(result.output.resolved_evidence)
            resolved_evidence, evidence_warnings = validate_resolved_evidence(
                context,
                model_evidence,
            )
            if resolved_category != "assess_answer":
                if resolved_evidence:
                    evidence_warnings.append(
                        "discarded semantic evidence from a non-assessment response"
                    )
                resolved_evidence = []
            resolved_context = apply_model_resolution_to_context(
                context,
                resolved_response_category=resolved_category,
                resolved_evidence=resolved_evidence,
            )
            decision = self._policy.decide(resolved_context)
            merged_warnings = (
                list(result.output.warnings)
                + evidence_warnings
                + list(decision.warnings)
            )
            updates: dict[str, object] = {
                "warnings": merged_warnings,
                "next_action": "ask_followup",
                "resolved_response_category": resolved_category,
                "resolved_evidence": resolved_evidence,
            }

            non_assessment_content_types = {
                "clarify_question": "clarification_response",
                "explain_term": "term_explanation",
                "encourage_answer": "guidance_response",
                "redirect": "redirect_response",
            }
            if resolved_category != "assess_answer":
                updates.update(
                    {
                        "content_type": non_assessment_content_types[resolved_category],
                        "question_type": "clarify",
                        "selected_rule_code": None,
                        "selected_dynamic_info_code": None,
                        "released_dynamic_info_text": None,
                        "target_dimensions": [],
                    }
                )
                return result.output.model_copy(update=updates)

            contract = load_contract(context.stage)
            probe_coverage = probe_coverage_real(resolved_evidence)
            probe_updates = resolve_probe(
                contract,
                probe_coverage,
                expected_evidence=list(
                    context.stage.exit_criteria.get("expected_evidence") or []
                ),
                followups_used=count_stage_followups(context),
                max_followups=context.stage.max_followups,
            )
            updates.update(probe_updates)
            if probe_updates:
                adopted_wording, probe_warnings = adopt_probe_wording(
                    contract,
                    probe_updates,
                    result.output.question,
                    context,
                    coverage=probe_coverage,
                )
                if adopted_wording:
                    adopted_wording["ai_generation_weight"] = (
                        result.output.ai_generation_weight
                    )
                    updates.update(adopted_wording)
                if probe_warnings:
                    updates["warnings"] = (
                        list(updates.get("warnings") or []) + probe_warnings
                    )

            def _finalize() -> FollowupOutput:
                # Structural gate for model-worded questions only: configured
                # probes are trusted fixed text, and non-question outputs
                # (advance prompts, clarifications) never pass through here.
                if not probe_updates and updates.get("content_type") in {
                    "followup_question",
                    "dynamic_info_question",
                }:
                    final_question, contract_warnings = enforce_constraints(
                        contract,
                        str(updates.get("question") or result.output.question or ""),
                        context,
                        coverage=probe_coverage,
                        selected_rule_code=result.output.selected_rule_code,
                    )
                    if contract_warnings:
                        updates["question"] = final_question
                        updates["warnings"] = (
                            list(updates.get("warnings") or []) + contract_warnings
                        )
                        updates["fallback_used"] = True
                return result.output.model_copy(update=updates)

            if decision.next_action in {"advance_stage", "finish_ready"}:
                updates.update(
                    {
                        "question": (
                            "我已经记录你的回答，接下来将基于完整对话生成测评报告。"
                            if decision.next_action == "finish_ready"
                            else "我已经记录你的回答，我们进入下一部分。"
                        ),
                        "next_action": decision.next_action,
                        "content_type": "advance_prompt",
                        "question_type": "advance",
                        "selected_rule_code": None,
                        "selected_dynamic_info_code": None,
                        "released_dynamic_info_text": None,
                        "transition_reason": decision.transition_reason,
                    }
                )
                return result.output.model_copy(update=updates)
            if decision.waiting_for_stage_choice:
                updates.update(
                    {
                        "question": build_stage_incomplete_prompt(
                            list(decision.missing_evidence)
                        ),
                        "content_type": "stage_incomplete_prompt",
                        "question_type": "clarify",
                        "selected_rule_code": None,
                        "selected_dynamic_info_code": None,
                        "released_dynamic_info_text": None,
                    }
                )
                return result.output.model_copy(update=updates)

            unreleased_infos = sorted(
                _unreleased_dynamic_infos(context),
                key=lambda item: (item.priority, item.info_code),
            )
            info_by_code = {item.info_code: item for item in unreleased_infos}
            selected_info = result.output.selected_dynamic_info_code
            if not selected_info:
                updates.update(
                    {
                        "selected_dynamic_info_code": None,
                        "released_dynamic_info_text": None,
                        "content_type": "followup_question",
                    }
                )
            else:
                info = info_by_code.get(selected_info or "")
                if info is None:
                    updates["warnings"] = merged_warnings + [
                        "ignored invalid or already released dynamic info selected by DeepSeek"
                    ]
                    updates["selected_dynamic_info_code"] = None
                    updates["released_dynamic_info_text"] = None
                    updates["content_type"] = "followup_question"
                    return _finalize()
                updates["selected_dynamic_info_code"] = info.info_code
                updates["released_dynamic_info_text"] = info.content
                updates["content_type"] = "dynamic_info_question"
            if not selected_info and result.output.content_type in {
                "advance_prompt",
                "stage_incomplete_prompt",
                "supplement_question",
                "clarification_response",
                "guidance_response",
                "term_explanation",
                "redirect_response",
            }:
                updates["content_type"] = "followup_question"
            return _finalize()

        logger.warning(
            "followup agent real model failed: %s %s",
            result.error_code,
            result.error_reason,
        )
        return _model_failure_output(
            error_code=result.error_code or "UNKNOWN",
            error_reason=result.error_reason or "no detail",
        )


__all__ = ["FollowupAgent"]


def _model_failure_output(*, error_code: str, error_reason: str) -> FollowupOutput:
    return FollowupOutput(
        question="DeepSeek 暂时无法分析这次回答，请稍后重新发送。",
        content_type="system_message",
        question_type="retry",
        resolved_response_category=None,
        resolved_evidence=[],
        selected_rule_code=None,
        selected_dynamic_info_code=None,
        released_dynamic_info_text=None,
        target_dimensions=[],
        reason="DeepSeek semantic analysis failed; no local classification was used",
        next_action="ask_followup",
        generation_mode="ai_open",
        ai_generation_weight=100,
        confidence=0,
        fallback_used=True,
        warnings=[f"real model failed: {error_code} ({error_reason})"],
    )
