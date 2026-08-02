from __future__ import annotations

from app.agents.user_turn_intent import (
    classify_consultative_control_intent,
    classify_user_turn,
)

import asyncio
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from app.agents.behavior_signal_extractor import (
    extract_behavior_evidence_spans,
    extract_behavior_signals,
)
from app.agents.interview_blueprint import GeneratedScenarioBlueprint
from app.agents.measurement_contract import load_measurement_contract
from app.agents.progressive_schemas import (
    EvidenceObservation,
    InterviewMemory,
    InterviewPlanOutput,
    InterviewState,
    PlannerBudget,
)
from app.agents.schemas import AgentRuntimeContext
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService


PLANNER_PROMPT_VERSION = "progressive_planner_v3_3"
EVENT_SEQUENCE = [
    "opening_context",
    "evidence_uncertainty",
    "stakeholder_conflict",
    "decision_pressure",
    "counter_evidence",
    "integration",
]


@dataclass
class PlannerAgentResult:
    output: InterviewPlanOutput
    raw_output: str | None
    model_name: str | None
    duration_ms: int
    status: str = "ok"
    error_code: str | None = None
    fallback_type: str | None = None


class InterviewPlannerAgent:
    def generate(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
        template_content: str | None = None,
    ) -> PlannerAgentResult:
        started = perf_counter()
        settings = get_settings()
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            output = self._mock_plan(context, state, blueprint)
            return PlannerAgentResult(
                output=output,
                raw_output=output.model_dump_json(),
                model_name="mock",
                duration_ms=int((perf_counter() - started) * 1000),
            )

        raw = ""
        try:
            raw, model = self._call(
                context, state, blueprint, template_content, repair_error=None
            )
            output = self._parse(raw)
            if output is None:
                raw, model = self._call(
                    context,
                    state,
                    blueprint,
                    template_content,
                    repair_error="上一次输出不符合 InterviewPlanOutput，请只返回修复后的 JSON。",
                )
                output = self._parse(raw)
            if output is None:
                raise ValueError("planner output remained invalid after repair")
            output = self.enforce(output, state, blueprint)
            return PlannerAgentResult(
                output=output,
                raw_output=raw,
                model_name=model,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            output = self._mock_plan(context, state, blueprint).model_copy(
                update={
                    "fallback_used": True,
                    "warnings": ["planner model unavailable; deterministic policy used"],
                }
            )
            return PlannerAgentResult(
                output=output,
                raw_output=raw or str(exc),
                model_name=None,
                duration_ms=int((perf_counter() - started) * 1000),
                status="failed",
                error_code="PLANNER_MODEL_FALLBACK",
                fallback_type="deterministic_planner",
            )

    def enforce(
        self,
        plan: InterviewPlanOutput,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
    ) -> InterviewPlanOutput:
        budget = blueprint.conversation_budget
        used = state.formal_user_turn_count
        updates: dict[str, Any] = {
            "budget": PlannerBudget(
                used_turns=used,
                remaining_turns=max(budget.max_total_user_turns - used, 0),
                reserved_update_turns=budget.reserved_update_turns,
                reserved_closure_turns=budget.reserved_closure_turns,
            ),
            "delivery_mode": self._delivery_mode(plan.action, used),
        }
        if (
            plan.target_dimension is not None
            and plan.target_dimension not in state.dimension_slots
        ):
            updates.update(
                target_dimension=self._next_dimension(state),
                target_evidence="补充一项构念相关的用户原话证据",
            )
        if plan.response_intent != "assess_answer":
            if (
                state.clarification_count_for_last_answer
                >= budget.max_clarifications_per_answer
            ):
                updates.update(
                    action="PROBE",
                    release_event_code=None,
                    release_unit_code=None,
                    delivery_mode=self._delivery_mode("PROBE", used),
                    target_dimension=self._next_dimension(state),
                    target_evidence="换一个具体角度继续访谈",
                    question_intent="不再重复澄清，换一个容易回答的具体角度",
                )
                return plan.model_copy(update=updates)
            updates.update(
                action="CLARIFY",
                release_event_code=None,
                release_unit_code=None,
                delivery_mode="clarification",
                target_dimension=None,
                target_evidence=None,
            )
            return plan.model_copy(update=updates)
        if used >= budget.max_total_user_turns:
            updates.update(
                action="CONCLUDE",
                release_event_code=None,
                release_unit_code=None,
                delivery_mode="closing",
            )
            return plan.model_copy(update=updates)
        if "integration" in state.released_event_codes and used >= budget.min_total_user_turns:
            incomplete_dimension = self._next_incomplete_dimension(state)
            if incomplete_dimension and used < budget.max_total_user_turns:
                updates.update(
                    action="PROBE",
                    release_event_code=None,
                    release_unit_code=None,
                    delivery_mode=self._delivery_mode("PROBE", used),
                    target_dimension=incomplete_dimension,
                    target_evidence="补足结束前仍不充分的维度证据",
                    active_topic="结束前证据补充",
                    question_intent="为尚未充分的维度提供一次新的公平作答机会",
                    warnings=[
                        *plan.warnings,
                        "conclusion deferred for incomplete dimension coverage",
                    ],
                )
                return plan.model_copy(update=updates)
            updates.update(
                action="CONCLUDE",
                release_event_code=None,
                release_unit_code=None,
                delivery_mode="closing",
            )
            return plan.model_copy(update=updates)

        next_event = self._next_event(state)
        remaining = budget.max_total_user_turns - used
        if remaining <= budget.reserved_update_turns + budget.reserved_closure_turns:
            if "counter_evidence" not in state.released_event_codes:
                next_event = "counter_evidence"
            elif "integration" not in state.released_event_codes:
                next_event = "integration"

        current_probes = state.topic_probe_counters.get(plan.active_topic, 0)
        target_count = (
            state.dimension_probe_counters.get(plan.target_dimension or "", 0)
            if plan.target_dimension
            else 0
        )
        if (
            plan.target_dimension
            and state.consecutive_dimension == plan.target_dimension
            and state.consecutive_dimension_count
            >= budget.max_consecutive_same_dimension
        ):
            updates.update(
                target_dimension=self._next_dimension_excluding(
                    state, plan.target_dimension
                ),
                target_evidence="换一个尚未充分的观察角度",
                active_topic="换一个判断角度",
            )
        release_due = current_probes >= budget.max_probes_per_topic or target_count >= 2
        scheduled_thresholds = {
            "evidence_uncertainty": 1,
            "stakeholder_conflict": 3,
            "decision_pressure": 5,
            "counter_evidence": 7,
            "integration": 9,
        }
        if next_event and used >= scheduled_thresholds.get(next_event, 99):
            release_due = True
        if next_event == "counter_evidence" and not state.memory.prior_decision_formed:
            if not state.initial_decision_prompted:
                updates.update(
                    action="PROBE",
                    release_event_code=None,
                    release_unit_code=None,
                    delivery_mode="reflective_probe",
                    target_dimension="integrative_decision",
                    target_evidence="形成反向信息前的明确初步决定",
                    active_topic="初步决定",
                    question_intent="在既有安排、减少检查或小范围试用中形成初步决定",
                )
                return plan.model_copy(update=updates)
            next_event = "integration"
            release_due = True

        if release_due and next_event:
            release_unit = self._select_release_unit(blueprint, state, next_event)
            updates.update(
                action="RELEASE_EVENT",
                release_event_code=next_event,
                release_unit_code=release_unit,
                delivery_mode="event_link",
                target_dimension=None,
                target_evidence=None,
                question_intent=self._event_question_intent(next_event),
            )
        elif plan.action in {"RELEASE_EVENT", "CONCLUDE"} or (
            plan.action == "INTEGRATE"
            and "integration" not in state.released_event_codes
        ):
            updates.update(
                action="PROBE",
                release_event_code=None,
                release_unit_code=None,
                delivery_mode=self._delivery_mode("PROBE", used),
                target_dimension=plan.target_dimension or self._next_dimension(state),
                target_evidence=plan.target_evidence or "补充当前判断的一项关键依据",
            )
        return plan.model_copy(update=updates)

    def avoid_duplicate(
        self,
        plan: InterviewPlanOutput,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
    ) -> InterviewPlanOutput:
        key = self.intent_key(plan)
        if key not in state.asked_intent_keys:
            return plan
        next_event = self._next_event(state)
        if next_event == "counter_evidence" and not state.memory.prior_decision_formed:
            next_event = "integration" if state.initial_decision_prompted else None
        if next_event:
            return plan.model_copy(
                update={
                    "action": "RELEASE_EVENT",
                    "release_event_code": next_event,
                    "release_unit_code": self._select_release_unit(
                        blueprint, state, next_event
                    ),
                    "delivery_mode": "event_link",
                    "target_dimension": None,
                    "target_evidence": None,
                    "question_intent": self._event_question_intent(next_event),
                    "warnings": [*plan.warnings, "duplicate intent advanced"],
                }
            )
        target = self._next_dimension_excluding(
            state, plan.target_dimension or ""
        )
        replacement = plan.model_copy(
            update={
                "action": "PROBE",
                "target_dimension": target,
                "target_evidence": "换一个尚未充分且未重复的观察角度",
                "active_topic": self._topic("", target),
                "question_intent": f"从{target}角度提出一个新的具体问题",
                "release_event_code": None,
                "release_unit_code": None,
                "delivery_mode": "reflective_probe",
                "warnings": [*plan.warnings, "duplicate intent changed dimension"],
            }
        )
        if self.intent_key(replacement) in state.asked_intent_keys:
            return replacement.model_copy(
                update={
                    "action": "INTEGRATE",
                    "target_dimension": None,
                    "target_evidence": None,
                    "delivery_mode": "integration",
                    "question_intent": "整合已谈到的依据、风险和行动安排",
                }
            )
        return replacement

    @staticmethod
    def intent_key(plan: InterviewPlanOutput) -> str:
        return "|".join(
            (
                plan.action,
                plan.target_dimension or "-",
                plan.question_intent.strip().lower(),
                plan.release_unit_code or "-",
            )
        )

    def build_deterministic_plan(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
    ) -> InterviewPlanOutput:
        """Build the measurement plan from auditable server-side rules."""
        latest = context.latest_user_turn
        text = latest.content.strip() if latest else ""
        intent = self._intent(text)
        observations = self._mock_observations(
            text,
            allow_dynamic="counter_evidence" in state.released_event_codes,
        )
        target = observations[0].dimension_key if observations else self._next_dimension(state)
        topic = self._topic(text, target)
        memory = state.memory.model_copy(deep=True)
        if intent == "assess_answer":
            memory.user_position = text[:160]
            if any(
                marker in text
                for marker in (
                    "决定", "方案", "先", "安排", "选择", "倾向", "试用", "保留"
                )
            ):
                memory.prior_decision_formed = True
        plan = InterviewPlanOutput(
            response_intent=intent,
            action="CLARIFY" if intent != "assess_answer" else "PROBE",
            active_topic=topic,
            target_dimension=target if intent == "assess_answer" else None,
            target_evidence=(
                "补充当前判断的一项关键依据" if intent == "assess_answer" else None
            ),
            release_event_code=None,
            release_unit_code=None,
            delivery_mode=(
                "clarification"
                if intent != "assess_answer"
                else self._delivery_mode("PROBE", state.formal_user_turn_count)
            ),
            question_intent=(
                "用更容易回答的方式澄清用户意思"
                if intent != "assess_answer"
                else "顺着当前话题补充一项尚未充分的证据"
            ),
            reflection_basis_turn_ids=[latest.turn_id] if latest and latest.turn_id else [],
            reason="根据最新回答、全局证据槽位和剩余轮次选择下一步",
            evidence_observations=observations,
            memory_update=memory,
            budget=PlannerBudget(
                used_turns=state.formal_user_turn_count,
                remaining_turns=max(
                    blueprint.conversation_budget.max_total_user_turns
                    - state.formal_user_turn_count,
                    0,
                ),
                reserved_update_turns=blueprint.conversation_budget.reserved_update_turns,
                reserved_closure_turns=blueprint.conversation_budget.reserved_closure_turns,
            ),
        )
        return self.enforce(plan, state, blueprint)

    # Kept for older tests and the progressive-v3 mock path.
    def _mock_plan(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
    ) -> InterviewPlanOutput:
        return self.build_deterministic_plan(context, state, blueprint)

    def _call(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
        template_content: str | None,
        *,
        repair_error: str | None,
    ) -> tuple[str, str | None]:
        settings = get_settings()
        contract = load_measurement_contract()
        prompt = {
            "instruction": (
                f"{template_content or ''}\n"
                "分析最新用户回答，提取六维证据并选择唯一下一动作。"
                "同时选择会谈呈现方式；释放事件时只能选择一个 presentation unit。"
                "不得写用户可见问题，只输出 InterviewPlanOutput JSON。"
            ),
            "allowed_actions": [
                "CLARIFY", "PROBE", "RELEASE_EVENT", "CHALLENGE", "INTEGRATE", "CONCLUDE"
            ],
            "measurement_contract": contract.model_dump(mode="json"),
            "blueprint": blueprint.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
            "latest_user_turn": (
                context.latest_user_turn.model_dump(mode="json")
                if context.latest_user_turn
                else None
            ),
            "recent_dialogue": [
                item.model_dump(mode="json") for item in context.dialogue_history[-10:]
            ],
            "repair": repair_error,
        }
        request = ModelChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是渐进式审辩访谈的 Turn Analyzer 与 Planner。"
                        "证据只能引用用户原话，只输出严格 JSON。"
                    ),
                ),
                ChatMessage(role="user", content=json.dumps(prompt, ensure_ascii=False)),
            ],
            temperature=0.1,
            max_tokens=2600,
            json_mode=True,
            thinking_enabled=False,
            reasoning_effort="low",
        )
        response = asyncio.run(ModelGatewayService(settings).chat(request))
        return response.content, response.model

    @staticmethod
    def _parse(raw: str) -> InterviewPlanOutput | None:
        try:
            return InterviewPlanOutput.model_validate(json.loads(raw.strip()))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _intent(text: str) -> str:
        control_intent = classify_consultative_control_intent(text)
        if control_intent is not None:
            return (
                "redirect"
                if control_intent == "boundary_redirect"
                else control_intent
            )

        user_intent = classify_user_turn(text)
        if user_intent == "clarification_request":
            return "clarify_question"
        if user_intent == "term_definition_request":
            return "explain_term"
        if user_intent == "low_information":
            return "low_information"
        if user_intent == "irrelevant":
            return "redirect"
        if any(item in text for item in ("天气", "吃饭", "游戏")) and len(text) < 30:
            return "redirect"
        return "assess_answer"

    @staticmethod
    def _mock_observations(
        text: str,
        *,
        allow_dynamic: bool = False,
    ) -> list[EvidenceObservation]:
        signals = extract_behavior_signals(text, allow_dynamic=allow_dynamic)
        observations: list[EvidenceObservation] = []
        quote = text[:500]
        for dimension, behaviors in signals.items():
            for behavior, matched in behaviors.items():
                observations.append(
                    EvidenceObservation(
                        dimension_key=dimension,
                        behavior_key=behavior,
                        quote=quote,
                        rationale=(
                            f"确定性降级识别到{dimension}的{behavior}关系信号："
                            + "、".join(matched[:2])
                        ),
                        extraction_confidence=0.76,
                    )
                )
        return observations

    @staticmethod
    def _v11_observations(
        text: str,
        *,
        allow_dynamic: bool = False,
    ) -> list[EvidenceObservation]:
        """Return v1.1-only minimum spans without changing frozen legacy quotes."""

        signals = extract_behavior_evidence_spans(
            text,
            allow_dynamic=allow_dynamic,
        )
        observations: list[EvidenceObservation] = []
        for dimension, behaviors in signals.items():
            for behavior, evidence_span in behaviors.items():
                observations.append(
                    EvidenceObservation(
                        dimension_key=dimension,
                        behavior_key=behavior,
                        quote=evidence_span.quote,
                        rationale=(
                            f"确定性降级识别到{dimension}的{behavior}关系信号："
                            + "、".join(evidence_span.matched_patterns[:2])
                        ),
                        extraction_confidence=0.76,
                    )
                )
        return observations

    @staticmethod
    def _next_dimension(state: InterviewState) -> str:
        available = [
            item for item in state.dimension_slots.values() if item.status != "not_available"
        ]
        for status in ("not_started", "partial", "blocked", "sufficient"):
            for slot in available:
                if slot.status == status:
                    return slot.dimension_key
        return "integrative_decision"

    @staticmethod
    def _next_dimension_excluding(
        state: InterviewState, excluded: str
    ) -> str:
        available = [
            item
            for item in state.dimension_slots.values()
            if item.status != "not_available" and item.dimension_key != excluded
        ]
        for status in ("not_started", "partial", "blocked", "sufficient"):
            for slot in available:
                if slot.status == status:
                    return slot.dimension_key
        return "integrative_decision"

    @staticmethod
    def _next_incomplete_dimension(state: InterviewState) -> str | None:
        available = [
            item
            for item in state.dimension_slots.values()
            if item.status != "not_available"
            and item.status != "sufficient"
        ]
        for require_missing_opportunity in (True, False):
            for status in ("not_started", "partial", "blocked"):
                for slot in available:
                    opportunity_count = int(
                        state.dimension_opportunity_counts.get(
                            slot.dimension_key, 0
                        )
                        or 0
                    )
                    if slot.status == status and (
                        not require_missing_opportunity
                        or opportunity_count == 0
                    ):
                        return slot.dimension_key
        return None

    @staticmethod
    def _next_event(state: InterviewState) -> str | None:
        return next(
            (item for item in EVENT_SEQUENCE if item not in state.released_event_codes),
            None,
        )

    @staticmethod
    def _delivery_mode(action: str, used_turns: int) -> str:
        if action == "CLARIFY":
            return "clarification"
        if action == "RELEASE_EVENT":
            return "event_link"
        if action == "CHALLENGE":
            return "perspective_shift"
        if action == "INTEGRATE":
            return "integration"
        if action == "CONCLUDE":
            return "closing"
        if used_turns > 0 and used_turns % 3 == 0:
            return "summary_check"
        return "reflective_probe"

    @staticmethod
    def _select_release_unit(
        blueprint: GeneratedScenarioBlueprint,
        state: InterviewState,
        event_code: str,
    ) -> str:
        event = next(item for item in blueprint.event_cards if item.event_code == event_code)
        available = [
            unit
            for unit in event.presentation_units
            if unit.unit_code not in state.released_unit_codes
            and all(
                prerequisite in state.released_unit_codes
                for prerequisite in unit.prerequisite_unit_codes
            )
        ]
        if not available:
            available = [
                unit
                for unit in event.presentation_units
                if unit.unit_code not in state.released_unit_codes
            ]
        if not available:
            return event.presentation_units[0].unit_code
        if event_code == "counter_evidence":
            position = (state.memory.user_position or "").lower()
            conservative = any(
                marker in position
                for marker in ("延期", "停止", "不做", "放弃", "风险", "保守")
            )
            desired = "benefit" if conservative else "risk"
            directional = [
                unit for unit in available if unit.counterevidence_direction == desired
            ]
            if directional:
                return directional[0].unit_code
        required = [unit for unit in available if unit.required]
        return (required or available)[0].unit_code

    @staticmethod
    def _topic(text: str, target: str) -> str:
        if any(marker in text for marker in ("数据", "日志", "核实")):
            return "信息核实"
        if any(marker in text for marker in ("方案", "安排", "执行")):
            return "行动安排"
        return {
            "problem_definition": "核心判断",
            "evidence_evaluation": "信息可靠性",
            "reasoning_argumentation": "判断依据",
            "multiple_perspectives": "相关方影响",
            "integrative_decision": "行动安排",
            "dynamic_adjustment": "新信息影响",
        }.get(target, "当前判断")

    @staticmethod
    def _event_question_intent(event_code: str) -> str:
        return {
            "evidence_uncertainty": "结合新出现的不确定信息说明下一步核实重点",
            "stakeholder_conflict": "比较新出现角色冲突中的优先考虑",
            "decision_pressure": "在约束下形成初步安排",
            "counter_evidence": "说明新信息是否改变原判断及原因",
            "integration": "形成最终可执行方案和调整条件",
        }.get(event_code, "回应刚刚出现的新情况")


__all__ = ["InterviewPlannerAgent", "PLANNER_PROMPT_VERSION", "PlannerAgentResult"]
