from __future__ import annotations

# Live runtime renderer. Frozen candidate generation continues to use
# app.agents.interviewer_agent.

import asyncio
import json
import re
from dataclasses import dataclass, field
from time import perf_counter

from app.agents.interview_blueprint import GeneratedScenarioBlueprint
from app.agents.interviewer_output_contract import (
    INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION,
)
from app.agents.interview_question_validator import (
    INTERNAL_TERMS,
    InterviewQuestionValidator,
)
from app.agents.humanistic_interviewer_v11 import (
    V11_OUTPUT_MARKER,
    build_v11_microstructure,
    compose_v11_message,
    fit_v11_length_budget,
    normalize_spoken_focus,
)
from app.agents.humanistic_v11_intent_registry import (
    resolve_intent_binding,
    semantic_binding_contract_errors,
    surface_question_semantic_errors,
)
from app.agents.progressive_schemas import (
    InterviewPlanOutput,
    InterviewQualityFlags,
    InterviewerOutput,
    ReflectionSourceQuote,
)
from app.agents.schemas import AgentRuntimeContext
from app.core.config import get_settings
from app.core.runtime_interview_config import get_runtime_interview_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService


INTERVIEWER_PROMPT_VERSION = "progressive_interviewer_v3_1"
HUMANISTIC_INTERVIEWER_PROMPT_VERSION = "humanistic_interviewer_v1"
RUNTIME_INTERVIEWER_PROMPT_VERSION = "progressive_interviewer_compact_v2"
RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION = "humanistic_interviewer_compact_v2"
RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION_V1_1 = "humanistic_compact_v1_2"
BASELINE_INTERVIEWER_STYLE = "baseline_v1"
HUMANISTIC_INTERVIEWER_STYLE = "humanistic_v1"
HUMANISTIC_INTERVIEWER_STYLE_V1_1 = "humanistic_v1_1"
CANDIDATE_GENERATION_MODE = "frozen_candidate_v1"
INTERVIEWER_DISPLAY_NAME = "罗杰斯教授"
EVENT_INTRO_SELECTOR_VERSION = "adjacent_visible_frame_v1"
EVENT_INTRO_FRAME_SUPPLEMENT = "speaker_supplement"
EVENT_INTRO_FRAME_EVIDENCE = "evidence_arrival"

_EVENT_INTRO_FRAME_MARKERS: dict[str, tuple[str, ...]] = {
    EVENT_INTRO_FRAME_SUPPLEMENT: (
        "我补充一条新",
        "我补充一项新",
        "我补充新",
        "补充一条新",
        "补充一项新",
    ),
    EVENT_INTRO_FRAME_EVIDENCE: (
        "这里先看一条新",
        "这里先看一项新",
        "先看一条新",
        "先看一项新",
    ),
}


def _candidate_reliability_instruction() -> str:
    internal_terms = "、".join(sorted(INTERNAL_TERMS))
    return (
        "这是离线冻结候选生成。"
        "最终 message 中由访谈员新写的部分，禁止出现 Validator "
        f"定义的任何内部术语：{internal_terms}。"
        "如果用户原话本身包含上述字符串，只能作为经过核验的"
        "用户原话引用处理；访谈员自写部分不得重复或扩展它们。"
        "quality_flags 是对最终输出的自检声明，不能替代硬校验。"
        "输出前必须根据最终 message、引用和事实字段逐项检查；"
        "若某项不满足，应先在本次生成内改写 message，不能把不合规"
        "内容与 true 声明同时输出；如果仍无法满足，必须如实填写 false。"
    )


CANDIDATE_RELIABILITY_INSTRUCTION = _candidate_reliability_instruction()
CANDIDATE_EVENT_SHAPE_INSTRUCTION = (
    "当 validated_plan.action 为 RELEASE_EVENT 时，必须使用单一标点结构。"
    "先用经过核验的逐字用户原话形成反映从句；逐字引语结束后"
    "不得添加句号、问号或叹号，只能使用逗号、冒号或分号连接事件事实。"
    "随后原样包含 presentation_unit.text，再紧接一个围绕 question_intent 的问题。"
    "将经过核验的逐字用户引语整体视为不参与计数的引用后，"
    "访谈员自写部分必须恰好包含一个问号，除该问号外最多只包含一个"
    "句末标点；不得通过修改 quality_flags 掩盖标点超限。"
    "结构示意中的占位符不得照抄："
    "你提到“<逐字原话>”；<presentation_unit.text><一个问题？>"
)

INTERVIEWER_RENDER_PROMPT_VARIANT = "compact_message_v2"
INTERVIEWER_RENDER_MAX_TOKENS = 220
INTERVIEWER_RENDER_FAST_RETRY_LIMIT = 1
RENDER_RETRY_ERROR_MARKERS = (
    "RemoteProtocolError",
    "ReadTimeout",
    "ConnectError",
    "ReadError",
    "incomplete chunked read",
    "peer closed connection",
)

_CLARIFICATION_OVERLAP_STOP_BIGRAMS = {
    "什么",
    "怎么",
    "你会",
    "可以",
    "问题",
    "刚才",
    "现在",
    "一个",
    "需要",
    "想问",
}
_V11_REPETITIVE_STOCK_PHRASES = {
    "为了判断得更稳妥",
    "顺着这个关注点",
    "顺着这个思路",
    "回到这个话题",
    "围绕这一点",
    "你提到",
    "你把重点放在",
    "你现在关注的是",
    "我们先沿着",
    "还有一条情况",
    "我刚才问的是",
    "刚才问的是",
    "刚才是在问",
    "刚才的问题是想了解",
    "我刚才想了解的是",
    "上一问是",
    "我可能没有问清楚",
    "我可能没有理解准确",
    "你能换一种说法",
    "换一种说法",
    "从最确定的一点",
}

PROBE_MESSAGE_BANK: dict[str, tuple[str, ...]] = {
    "problem_definition": (
        "你觉得眼下最需要先判断的具体问题是什么？",
        "在开始行动前，你最需要先弄清哪一个问题？",
        "哪些是表面上的困难，真正要决定的又是什么？",
        "这个问题受到哪些时间、人员或范围条件限制？",
    ),
    "evidence_evaluation": (
        "为了判断得更稳妥，你会先核实哪一类信息？",
        "现有说法里，哪一点还需要查证后才能相信？",
        "你会怎样确认这些数字或意见是否适用于眼前情况？",
        "还缺少什么信息，才能排除另一种可能？",
    ),
    "reasoning_argumentation": (
        "你这个判断最主要的依据是什么？",
        "哪些事实能支持这个结论，它们之间是什么关系？",
        "什么情况出现时，你现在的理由会不再成立？",
        "有没有另一种解释也能说明眼前的情况？",
    ),
    "multiple_perspectives": (
        "这项安排还会直接影响谁？",
        "不同参与者最关心的事情分别是什么？",
        "如果两边的目标冲突，你会依据什么决定先后？",
        "谁可能承担较多风险，你准备怎样协调？",
    ),
    "integrative_decision": (
        "如果现在开始执行，你会先安排哪一步？",
        "请把你的方案按先后顺序说一遍，并说明谁来做？",
        "遇到什么情况时，你会暂停或改用另一种办法？",
        "你准备用什么结果判断这个安排可以继续？",
    ),
    "dynamic_adjustment": (
        "这条新信息具体会改变你原安排的哪一部分？",
        "原计划哪些部分保留，哪些部分需要马上调整？",
        "你会先采取什么动作来应对刚出现的变化？",
        "接下来看到什么结果时，你会再次改变安排？",
    ),
}


@dataclass
class InterviewerAgentResult:
    output: InterviewerOutput
    raw_output: str | None
    model_name: str | None
    duration_ms: int
    status: str = "ok"
    error_code: str | None = None
    fallback_type: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    model_attempt_count: int = 0
    retry_reason: str | None = None
    transport_errors: list[str] = field(default_factory=list)
    attempt_durations_ms: list[int] = field(default_factory=list)
    audit_metadata: dict[str, object] = field(default_factory=dict)


class InterviewerAgent:
    def __init__(self) -> None:
        self.validator = InterviewQuestionValidator()

    @staticmethod
    def _event_intro_frames_in_message(message: str) -> set[str]:
        return {
            frame
            for frame, markers in _EVENT_INTRO_FRAME_MARKERS.items()
            if any(marker in message for marker in markers)
        }

    @classmethod
    def _classify_event_intro_frame(cls, message: str) -> str | None:
        matches = cls._event_intro_frames_in_message(message)
        return next(iter(matches)) if len(matches) == 1 else None

    @classmethod
    def event_intro_frame_audit(
        cls,
        context: AgentRuntimeContext,
        *,
        event_turn: bool,
    ) -> dict[str, str | None]:
        """Select one visible event frame from persisted visible history.

        The latest visible AI turn is the only predecessor considered.  This
        keeps replay deterministic: rebuilding the same turn from the same
        persisted history always produces the same selection.
        """

        preceding_ai_turn = next(
            (
                item
                for item in reversed(context.dialogue_history)
                if item.speaker == "ai" and item.content.strip()
            ),
            None,
        )
        previous_frame = (
            cls._classify_event_intro_frame(preceding_ai_turn.content)
            if preceding_ai_turn is not None
            and preceding_ai_turn.content_type == "interview_event"
            else None
        )
        selected_frame: str | None = None
        if event_turn:
            selected_frame = (
                EVENT_INTRO_FRAME_EVIDENCE
                if previous_frame == EVENT_INTRO_FRAME_SUPPLEMENT
                else EVENT_INTRO_FRAME_SUPPLEMENT
            )
        return {
            "event_intro_selector_version": EVENT_INTRO_SELECTOR_VERSION,
            "previous_event_intro_frame": previous_frame,
            "selected_event_intro_frame": selected_frame,
        }

    @staticmethod
    def _event_intro_phrase(
        frame: str,
        *,
        counter: str,
        information_type: str,
    ) -> str:
        if frame == EVENT_INTRO_FRAME_EVIDENCE:
            return f"这里先看一{counter}新的{information_type}"
        return f"我补充一{counter}新的{information_type}"

    @classmethod
    def _event_intro_message(
        cls,
        *,
        frame: str,
        reason: str,
        fact: str,
        question: str,
        information_type: str = "信息",
        counter: str = "条",
        preface: str = "",
    ) -> str:
        intro = cls._event_intro_phrase(
            frame,
            counter=counter,
            information_type=information_type,
        )
        message = f"{preface}{reason}，{intro}：{fact}；{question}"
        if len(message) <= 90:
            return message
        compact_intro = cls._event_intro_phrase(
            frame,
            counter="条",
            information_type="信息",
        )
        compact = f"{preface}为了继续判断，{compact_intro}：{fact}；{question}"
        if len(compact) <= 90:
            return compact
        compact_question = cls._compact_event_question(question)
        compact_reason = (
            "为核实"
            if "核实" in compact_question or "查" in compact_question
            else "为比较"
            if "比较" in compact_question
            else "为调整"
            if "调整" in compact_question
            else "为选择"
            if "选" in compact_question or "安排" in compact_question
            else "为判断"
        )
        compact = (
            f"{preface}{compact_reason}，{compact_intro}："
            f"{fact}；{compact_question}"
        )
        if len(compact) <= 90:
            return compact
        return f"{compact_reason}，{compact_intro}：{fact}；{compact_question}"

    @staticmethod
    def _compact_event_question(question: str) -> str:
        if "核实" in question:
            if "记录" in question:
                return "先核实哪项记录？"
            return "先核实什么？"
        if "查" in question:
            if "记录" in question:
                return "先查哪项记录？"
            return "先查什么？"
        if "比较" in question:
            return "先比较什么？"
        if "调整" in question:
            return "怎样调整？"
        if "安排" in question:
            return "怎样安排？"
        if "选" in question:
            return "怎样选择？"
        return question

    @classmethod
    def runtime_expression_errors(
        cls,
        message: str,
        renderer_input: dict[str, object],
    ) -> list[str]:
        """Validate mutable visible-expression rules outside the frozen validator."""

        errors: list[str] = []
        if re.search(r"(?:AI\s*|\u4eba\u5de5\u667a\u80fd)\u8bbf\u8c08\u5458", message, flags=re.IGNORECASE):
            errors.append("legacy_interviewer_identity")
        identity_declaration = re.search(
            r"\u6211\u662f[^\uff0c\u3002\uff1b\uff01\uff1f!?]{0,20}(?:\u8bbf\u8c08\u5458|\u8bbf\u8c08\u89d2\u8272)",
            message,
        )
        if identity_declaration is not None and INTERVIEWER_DISPLAY_NAME not in message:
            errors.append("interviewer_identity_mismatch")

        validated_plan = renderer_input.get("validated_plan")
        action = (
            validated_plan.get("action")
            if isinstance(validated_plan, dict)
            else None
        )
        if action != "RELEASE_EVENT":
            return errors

        selected = renderer_input.get("selected_event_intro_frame")
        frames = cls._event_intro_frames_in_message(message)
        if selected not in {
            EVENT_INTRO_FRAME_SUPPLEMENT,
            EVENT_INTRO_FRAME_EVIDENCE,
        }:
            errors.append("missing_event_intro_selection")
        elif frames != {selected}:
            errors.append("event_intro_frame_mismatch")
        else:
            marker_positions = [
                message.find(marker)
                for marker in _EVENT_INTRO_FRAME_MARKERS[str(selected)]
                if marker in message
            ]
            reason_positions = [
                message.find(marker)
                for marker in (
                    "为了",
                    "为核实",
                    "为比较",
                    "为选择",
                    "为调整",
                    "为整合",
                    "为判断",
                    "为继续判断",
                )
                if marker in message
            ]
            if not reason_positions:
                errors.append("missing_event_introduction_reason")
            elif min(reason_positions) > min(marker_positions):
                errors.append("event_introduction_out_of_order")
        return list(dict.fromkeys(errors))

    def render(
        self,
        context: AgentRuntimeContext,
        blueprint: GeneratedScenarioBlueprint,
        plan: InterviewPlanOutput,
        *,
        previous_questions: list[str],
        template_content: str | None = None,
        style_version: str = BASELINE_INTERVIEWER_STYLE,
        timeout_seconds: float | None = None,
        primary_timeout_seconds: float | None = None,
        allow_model_call: bool = True,
        deterministic_primary: bool = False,
        renderer_input: dict[str, object] | None = None,
    ) -> InterviewerAgentResult:
        started = perf_counter()
        settings = get_settings()
        humanistic = style_version in {
            HUMANISTIC_INTERVIEWER_STYLE,
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        }
        humanistic_v11 = style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
        deterministic_output = self._fallback(
            plan,
            blueprint,
            context,
            style_version=style_version,
        )
        if humanistic_v11:
            renderer_input = renderer_input or self.runtime_renderer_input_payload(
                context,
                blueprint,
                plan,
                style_version=style_version,
            )
        audit_metadata = self._v11_audit_metadata(renderer_input or {})
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            return InterviewerAgentResult(
                output=deterministic_output,
                raw_output=deterministic_output.model_dump_json(),
                model_name="deterministic-renderer-mock-v1",
                duration_ms=int((perf_counter() - started) * 1000),
                audit_metadata=audit_metadata,
            )
        if deterministic_primary:
            return InterviewerAgentResult(
                output=deterministic_output,
                raw_output=deterministic_output.model_dump_json(),
                model_name="deterministic-humanistic-v1_1",
                duration_ms=int((perf_counter() - started) * 1000),
                model_attempt_count=0,
                audit_metadata={
                    **audit_metadata,
                    "model_polish_skipped": True,
                    "deterministic_primary": True,
                },
            )
        if not allow_model_call or (
            timeout_seconds is not None and timeout_seconds < 1
        ):
            output = deterministic_output.model_copy(
                update={
                    "fallback_used": True,
                    "warnings": [
                        *deterministic_output.warnings,
                        "renderer latency budget exhausted; deterministic fallback used",
                    ],
                }
            )
            return InterviewerAgentResult(
                output=output,
                raw_output=None,
                model_name=None,
                duration_ms=int((perf_counter() - started) * 1000),
                status="failed",
                error_code="INTERVIEWER_BUDGET_FALLBACK",
                fallback_type=(
                    "humanistic_deterministic_renderer"
                    if humanistic
                    else "neutral_renderer"
                ),
                validation_errors=["renderer_budget_exhausted"],
                audit_metadata=audit_metadata,
            )

        raw = ""
        errors: list[str] = []
        transport_errors: list[str] = []
        attempt_durations_ms: list[int] = []
        attempt_count = 0
        retry_reason: str | None = None
        renderer_input = renderer_input or self.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=style_version,
        )
        audit_metadata = self._v11_audit_metadata(renderer_input)
        allowed = {plan.release_unit_code} if plan.release_unit_code else set()
        event = next(
            (
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            ),
            None,
        )
        unit = self._selected_unit(event, plan.release_unit_code)
        total_budget = max(
            float(timeout_seconds or 0),
            0,
        )
        primary_budget = max(
            min(
                float(
                    primary_timeout_seconds
                    if primary_timeout_seconds is not None
                    else get_runtime_interview_settings().RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS
                ),
                total_budget,
            ),
            0,
        )
        deadline = started + total_budget
        model: str | None = None
        try:
            repair: str | None = None
            while True:
                remaining_seconds = deadline - perf_counter()
                if remaining_seconds < 1:
                    raise TimeoutError("renderer total latency budget exhausted")
                attempt_count += 1
                attempt_timeout = min(
                    primary_budget if attempt_count == 1 else remaining_seconds,
                    remaining_seconds,
                )
                attempt_started = perf_counter()
                try:
                    raw, model = self._call(
                        renderer_input,
                        template_content,
                        style_version=style_version,
                        timeout_seconds=attempt_timeout,
                        repair=repair,
                    )
                except Exception as exc:  # noqa: BLE001
                    attempt_durations_ms.append(
                        int((perf_counter() - attempt_started) * 1000)
                    )
                    transport_errors.append(self._error_text(exc))
                    retry_reason = self._retry_reason(exc)
                    if (
                        attempt_count > INTERVIEWER_RENDER_FAST_RETRY_LIMIT
                        or retry_reason is None
                        or deadline - perf_counter() < 1
                    ):
                        raise
                    continue
                attempt_durations_ms.append(
                    int((perf_counter() - attempt_started) * 1000)
                )
                output = self._parse(
                    raw,
                    deterministic_output=deterministic_output,
                )
                if output is None:
                    errors = ["invalid_json"]
                    retry_reason = "invalid_json"
                    repair = '上次输出不是有效的 {"message":"..."} JSON。'
                    if (
                        attempt_count > INTERVIEWER_RENDER_FAST_RETRY_LIMIT
                        or deadline - perf_counter() < 1
                    ):
                        raise ValueError("invalid compact renderer JSON")
                    continue
                valid, errors = self.validator.validate(
                    output,
                    plan=plan,
                    allowed_fact_codes=allowed,
                    previous_questions=previous_questions,
                    allowed_source_turn_ids=set(plan.reflection_basis_turn_ids),
                    source_turn_texts={
                        item.turn_id: item.content
                        for item in context.dialogue_history
                        if item.turn_id is not None
                    },
                    allowed_fact_text=unit.text if unit else None,
                    enforce_humanistic_safety=humanistic,
                )
                errors.extend(
                    self.runtime_expression_errors(output.message, renderer_input)
                )
                errors = list(dict.fromkeys(errors))
                valid = not errors
                if humanistic_v11:
                    errors.extend(self._v11_contract_errors(output, renderer_input))
                    errors = list(dict.fromkeys(errors))
                    valid = not errors
                    normalized = self._v11_remove_rejected_stock_preface(
                        output,
                        errors,
                        renderer_input,
                    )
                    if normalized is not None:
                        normalized_valid, normalized_errors = self.validator.validate(
                            normalized,
                            plan=plan,
                            allowed_fact_codes=allowed,
                            previous_questions=previous_questions,
                            allowed_source_turn_ids=set(
                                plan.reflection_basis_turn_ids
                            ),
                            source_turn_texts={
                                item.turn_id: item.content
                                for item in context.dialogue_history
                                if item.turn_id is not None
                            },
                            allowed_fact_text=unit.text if unit else None,
                            enforce_humanistic_safety=True,
                        )
                        normalized_errors.extend(
                            self._v11_contract_errors(normalized, renderer_input)
                        )
                        normalized_errors.extend(
                            self.runtime_expression_errors(
                                normalized.message,
                                renderer_input,
                            )
                        )
                        normalized_errors = list(dict.fromkeys(normalized_errors))
                        if normalized_valid and not normalized_errors:
                            output = normalized
                            errors = []
                            valid = True
                if not valid:
                    retry_reason = "validation_error"
                    action = (renderer_input.get("validated_plan") or {}).get(
                        "action"
                    )
                    selected_event_frame = renderer_input.get(
                        "selected_event_intro_frame"
                    )
                    required_event_intro = self._event_intro_phrase(
                        str(selected_event_frame),
                        counter="条",
                        information_type="信息",
                    )
                    repair = (
                        "上次表述未通过结构校验。不要重复承接或解释规则；"
                        f"严格按‘为了继续判断，{required_event_intro}：<required_fact>；"
                        "<一个与 selected_question 目标相同的问题>’这一顺序重写；"
                        "required_fact 必须原样保留，全文只有一个问号，"
                        "且不超过90个汉字。"
                        if action == "RELEASE_EVENT"
                        else (
                            "上次表述未通过结构与语义校验。"
                            "请保持 selected_question 的同一问题目标，"
                            "去掉套话、重复承接和无关信息，"
                            "用一条自然中文重写；全文只有一个问号。"
                        )
                    )
                    if (
                        (humanistic_v11 or action == "RELEASE_EVENT")
                        and attempt_count <= INTERVIEWER_RENDER_FAST_RETRY_LIMIT
                        and deadline - perf_counter() >= 1
                    ):
                        continue
                    raise ValueError(f"interviewer output invalid: {errors}")
                break
            if not output or not valid:
                raise ValueError(f"interviewer output invalid: {errors}")
            return InterviewerAgentResult(
                output=output,
                raw_output=raw,
                model_name=model,
                duration_ms=int((perf_counter() - started) * 1000),
                validation_errors=list(dict.fromkeys(errors)),
                model_attempt_count=attempt_count,
                retry_reason=retry_reason,
                transport_errors=transport_errors,
                attempt_durations_ms=attempt_durations_ms,
                audit_metadata=audit_metadata,
            )
        except Exception as exc:  # noqa: BLE001
            failure_text = self._error_text(exc)
            fallback_output = (
                self._v11_graceful_degradation_output(
                    deterministic_output,
                    renderer_input,
                )
                if humanistic_v11 and renderer_input is not None
                else deterministic_output
            )
            output = fallback_output.model_copy(
                update={
                    "fallback_used": True,
                    "warnings": [
                        *fallback_output.warnings,
                        "interviewer model unavailable or unsafe; deterministic renderer used",
                    ],
                }
            )
            return InterviewerAgentResult(
                output=output,
                raw_output=raw or failure_text,
                # Preserve the actual API identity when transport succeeded but
                # the visible text was rejected by a local safety/quality gate.
                # This keeps degraded traces distinguishable from no-call or
                # transport-failure fallbacks without exposing the rejected text.
                model_name=model,
                duration_ms=int((perf_counter() - started) * 1000),
                status="failed",
                error_code=(
                    "HUMANISTIC_RENDERER_FALLBACK"
                    if humanistic
                    else "INTERVIEWER_MODEL_FALLBACK"
                ),
                fallback_type=(
                    "humanistic_deterministic_renderer"
                    if humanistic
                    else "neutral_renderer"
                ),
                validation_errors=list(dict.fromkeys([*errors, "renderer_exception"])),
                model_attempt_count=attempt_count,
                retry_reason=retry_reason,
                transport_errors=transport_errors,
                attempt_durations_ms=attempt_durations_ms,
                audit_metadata=audit_metadata,
            )

    def _v11_graceful_degradation_output(
        self,
        deterministic_output: InterviewerOutput,
        renderer_input: dict[str, object],
    ) -> InterviewerOutput:
        """Compose one general, plan-bound message when live polish is unavailable.

        This path deliberately avoids domain keywords and screenshot-specific
        branches.  It may only use the frozen semantic question, the latest
        visible user focus and the already-authorized event fact.
        """

        validated_plan = renderer_input.get("validated_plan")
        if not isinstance(validated_plan, dict):
            return deterministic_output
        action = str(validated_plan.get("action") or "")
        response_intent = str(validated_plan.get("response_intent") or "")
        if action == "CONCLUDE":
            return deterministic_output

        question = str(renderer_input.get("selected_question") or "").strip()
        if not question or question.count("？") + question.count("?") != 1:
            return deterministic_output

        if action == "RELEASE_EVENT":
            required_fact = renderer_input.get("required_fact")
            fact = (
                str(required_fact.get("text") or "").rstrip("。！？!?")
                if isinstance(required_fact, dict)
                else ""
            )
            message = (
                self._event_intro_message(
                    frame=str(renderer_input.get("selected_event_intro_frame")),
                    reason="为了继续判断",
                    fact=fact,
                    question=question,
                )
                if fact
                else question
            )
        elif response_intent == "clarify_question":
            message = f"我说具体一点：{question}"
        elif response_intent == "low_information":
            message = f"没关系，我们先从一个小问题开始：{question}"
        else:
            focus = normalize_spoken_focus(
                str(renderer_input.get("latest_user_text") or "")
            )
            message = (
                f"好，我们先看{focus}。{question}"
                if 2 <= len(focus) <= 24
                else f"好，我们继续往下看。{question}"
            )

        if len(message) > 90:
            message = question
        candidate = deterministic_output.model_copy(
            update={
                "message": message,
                "question_count": message.count("？") + message.count("?"),
                "warnings": [
                    *deterministic_output.warnings,
                    "general plan-bound graceful degradation",
                ],
            }
        )
        return (
            candidate
            if not self._v11_contract_errors(candidate, renderer_input)
            else deterministic_output
        )

    def _v11_remove_rejected_stock_preface(
        self,
        output: InterviewerOutput,
        errors: list[str],
        renderer_input: dict[str, object],
    ) -> InterviewerOutput | None:
        """Remove only a rejected stock preface while preserving the live question.

        This normalization is intentionally narrow: it runs only when the
        complete output is otherwise valid, never on event turns, and the
        resulting question is validated again by both safety contracts.
        """

        if set(errors) != {"repetitive_stock_phrase"}:
            return None
        validated_plan = renderer_input.get("validated_plan")
        if not isinstance(validated_plan, dict) or validated_plan.get(
            "action"
        ) == "RELEASE_EVENT":
            return None
        question_match = re.search(
            r"([^.。！？!?;；\n]+[？?])\s*$",
            output.message,
        )
        if question_match is None:
            return None
        question = question_match.group(1).strip()
        if question == output.message.strip():
            return None
        return output.model_copy(
            update={
                "message": question,
                "question_count": 1,
                "warnings": [
                    *output.warnings,
                    "rejected stock preface removed; live semantic question retained",
                ],
            }
        )

    def render_opening(
        self,
        blueprint: GeneratedScenarioBlueprint,
        nickname: str,
        *,
        style_version: str = BASELINE_INTERVIEWER_STYLE,
    ) -> InterviewerOutput:
        opening = blueprint.event_cards[0]
        first_unit = opening.presentation_units[0]
        question = (
            "你愿意先说说，眼下最想确认的是哪一点？"
            if style_version
            in {
                HUMANISTIC_INTERVIEWER_STYLE,
                HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            }
            else "你最想先确认哪一点？"
        )
        return InterviewerOutput(
            message=f"{nickname}，{first_unit.text}{question}",
            message_type="opening",
            question_count=1,
            introduced_fact_codes=[first_unit.unit_code],
            quality_flags=self._quality_flags(),
        )

    def _fallback(
        self,
        plan: InterviewPlanOutput,
        blueprint: GeneratedScenarioBlueprint,
        context: AgentRuntimeContext,
        *,
        style_version: str = BASELINE_INTERVIEWER_STYLE,
    ) -> InterviewerOutput:
        event = next(
            (
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            ),
            None,
        )
        unit = self._selected_unit(event, plan.release_unit_code)
        humanistic = style_version in {
            HUMANISTIC_INTERVIEWER_STYLE,
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        }
        humanistic_v11 = style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
        if humanistic_v11:
            return self._fallback_v11(plan, blueprint, context)
        event_frame = self.event_intro_frame_audit(
            context,
            event_turn=plan.action == "RELEASE_EVENT",
        )["selected_event_intro_frame"]
        reflection = (
            self._humanistic_reflection(context, plan)
            if humanistic
            else self._grounded_reflection(context, plan.delivery_mode)
        )
        previous_messages = [
            item.content for item in context.dialogue_history if item.speaker == "ai"
        ]
        reflection_turn = self._reflection_turn(context, plan) if reflection else None
        reflection_sources = (
            [
                ReflectionSourceQuote(
                    turn_id=reflection_turn.turn_id,
                    quote=self._reflection_quote(reflection_turn.content),
                )
            ]
            if reflection_turn and reflection_turn.turn_id is not None
            else []
        )
        if plan.action == "CONCLUDE":
            message = "谢谢，你的回答已经完整记录下来。接下来会基于整段访谈生成报告。"
            message_type = "closing"
            question_count = 0
        elif plan.action == "RELEASE_EVENT" and event is not None and unit is not None:
            fact = unit.text.rstrip("。！？!?")
            message = self._event_intro_message(
                frame=str(event_frame),
                reason=(
                    "为了把这项新情况放进刚才的判断"
                    if humanistic
                    else "为了继续判断"
                ),
                fact=fact,
                question=self._event_question(event.event_code),
                information_type="情况",
                preface=f"{reflection}；" if reflection else "",
            )
            message_type = "event"
            question_count = 1
        elif plan.action == "INTEGRATE":
            message = self._choose_unused_question(
                (
                    (
                        "把这些信息放在一起，你现在会怎样安排并说明取舍？",
                        "考虑到目前的限制，你会保留什么，又会调整什么？",
                        "如果由你决定，下一步会怎样安排？",
                    )
                    if humanistic
                    else (
                        "综合前面的信息，你最终会怎样安排并说明取舍？",
                        "在当前限制下，你会保留什么安排，又会调整什么？",
                        "你准备怎样把前面的判断转成可以执行的方案？",
                    )
                ),
                previous_messages,
            )
            message_type = "integration"
            question_count = 1
        elif plan.action == "CLARIFY":
            message = self._clarify_message(
                plan.response_intent,
                previous_messages,
                humanistic=humanistic,
            )
            message_type = "clarification"
            question_count = 1
        else:
            question = self.select_probe_message(
                plan.target_dimension,
                previous_questions=previous_messages,
            )
            if reflection:
                message = f"{reflection}。{question}"
            else:
                message = question
            message_type = "followup"
            question_count = 1
        visible_reflection_turn = (
            reflection_turn
            if reflection_turn is not None and reflection and reflection in message
            else None
        )
        visible_reflection_sources = (
            reflection_sources if visible_reflection_turn is not None else []
        )
        return InterviewerOutput(
            message=message,
            message_type=message_type,
            question_count=question_count,
            introduced_fact_codes=[unit.unit_code] if unit else [],
            reflection_turn_ids=(
                [visible_reflection_turn.turn_id]
                if visible_reflection_turn
                and visible_reflection_turn.turn_id is not None
                else []
            ),
            reflection_source_quotes=visible_reflection_sources,
            quality_flags=self._quality_flags(),
        )

    def _fallback_v11(
        self,
        plan: InterviewPlanOutput,
        blueprint: GeneratedScenarioBlueprint,
        context: AgentRuntimeContext,
    ) -> InterviewerOutput:
        previous_questions = [
            item.content
            for item in context.dialogue_history
            if item.speaker == "ai" and "?" in item.content.replace("？", "?")
        ]
        microstructure = build_v11_microstructure(
            context,
            plan,
            previous_questions=previous_questions,
        )
        event = next(
            (
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            ),
            None,
        )
        unit = self._selected_unit(event, plan.release_unit_code)
        event_frame = self.event_intro_frame_audit(
            context,
            event_turn=plan.action == "RELEASE_EVENT",
        )["selected_event_intro_frame"]
        microstructure = fit_v11_length_budget(
            plan,
            microstructure,
            event_fact=unit.text if unit is not None else None,
        )
        message = compose_v11_message(
            plan,
            microstructure,
            event_fact=unit.text if unit is not None else None,
        )
        latest = (
            context.latest_user_turn.content.strip()
            if context.latest_user_turn is not None
            else ""
        )
        if plan.action == "RELEASE_EVENT" and unit is not None:
            message = self._contextual_v11_event_message(
                plan,
                unit.text.rstrip("。！？!?"),
                latest,
                frame=str(event_frame),
                default=message,
            )
        elif plan.response_intent == "clarify_question":
            preceding = self._clarification_source_message(context)
            topic = preceding.strip().rstrip("。！？!?")[:320]
            if topic:
                message = self._choose_unused_question(
                    self._plain_clarification_messages(topic),
                    previous_questions,
                )
        elif plan.response_intent == "low_information":
            topic = normalize_spoken_focus(latest)[:24]
            if self._is_substantive_low_information(topic) and not any(
                marker in topic for marker in INTERNAL_TERMS
            ):
                if any(marker in topic for marker in ("组员", "同学", "成员", "团队", "参与者")):
                    message = f"这些{topic}里，谁的任务最需要先确认？"
                else:
                    message = f"{topic}具体指哪一部分？"
        elif plan.action in {"PROBE", "CHALLENGE"}:
            message = self._contextual_v11_fallback_message(
                plan,
                latest,
                default=message,
            )
        source_quotes = [
            ReflectionSourceQuote.model_validate(item)
            for item in microstructure["reflection_source_quotes"]
        ]
        return InterviewerOutput(
            message=message,
            message_type=self._message_type(plan.action),
            question_count=0 if plan.action == "CONCLUDE" else 1,
            introduced_fact_codes=(
                [unit.unit_code]
                if plan.action == "RELEASE_EVENT" and unit is not None
                else []
            ),
            reflection_turn_ids=list(
                dict.fromkeys(item.turn_id for item in source_quotes)
            ),
            reflection_source_quotes=source_quotes,
            quality_flags=self._quality_flags(),
            warnings=[
                V11_OUTPUT_MARKER,
                f"selected_candidate:{microstructure['selected_candidate_id']}",
                *(
                    ["reflection_omitted_for_length"]
                    if microstructure.get("reflection_adjustment_reason")
                    == "omitted_for_length"
                    else []
                ),
            ],
        )

    @staticmethod
    def _contextual_v11_fallback_message(
        plan: InterviewPlanOutput,
        latest_user_text: str,
        *,
        default: str,
    ) -> str:
        """Keep the protected dimension while grounding it in the user's focus."""

        latest = normalize_spoken_focus(latest_user_text)
        target = plan.target_dimension
        cause_focus = "延迟" in latest
        progress_focus = any(
            marker in latest for marker in ("进度", "完成情况", "完成度")
        )
        division_focus = any(
            marker in latest for marker in ("分工", "分别负责", "职责", "负责什么")
        )
        people_focus = any(
            marker in latest for marker in ("组员", "同学", "成员", "参与者", "团队")
        )
        meeting_focus = any(
            marker in latest for marker in ("开会", "召集", "讨论", "沟通")
        )

        if meeting_focus:
            by_dimension = {
                "problem_definition": "你想把大家叫到一起谈谈。会上最想先弄清哪个问题？",
                "evidence_evaluation": "你想把大家叫到一起谈谈。会上最想先核实哪项信息？",
                "reasoning_argumentation": "你想把大家叫到一起谈谈。会上最想先听哪项依据？",
                "multiple_perspectives": "你想把大家叫到一起谈谈。会上最想先听哪处不同想法？",
                "integrative_decision": "你想把大家叫到一起谈谈。会上最想先定下哪项安排？",
                "dynamic_adjustment": "你想把大家叫到一起谈谈。听到什么结果时你会调整安排？",
            }
            return by_dimension.get(target, default)

        if division_focus:
            by_dimension = {
                "problem_definition": "好，那就先把分工弄清楚：眼下最需要解决的是哪处职责不清？",
                "evidence_evaluation": "好，那就先把分工弄清楚：你会先核实哪一项任务记录或分工信息？",
                "reasoning_argumentation": "好，那就先看分工：你会依据什么判断当前安排是否合适？",
                "multiple_perspectives": "好，那就先看分工：哪些组员的任务需要一起确认？",
                "integrative_decision": "好，那就先明确分工：你准备先安排谁负责哪一步？",
                "dynamic_adjustment": "好，那就先确认分工：出现什么情况时你会调整任务？",
            }
            return by_dimension.get(target, default)

        if cause_focus:
            by_dimension = {
                "problem_definition": "好，先把延迟出在哪儿弄清楚：你会从哪个环节查起？",
                "evidence_evaluation": "好，先查延迟原因：你会先核实哪项记录或信息？",
                "reasoning_argumentation": "好，先看延迟的依据：哪项事实最能说明原因？",
                "multiple_perspectives": "好，先查清延迟发生在哪个环节：你会先核对谁的任务或交接？",
                "integrative_decision": "好，先把原因查清楚：你会安排谁先核对哪项记录？",
                "dynamic_adjustment": "好，先查延迟原因：看到什么结果时你会调整原来的分工？",
            }
            return by_dimension.get(target, default)

        if progress_focus:
            by_dimension = {
                "problem_definition": "好，那就先看当前进度：你最需要先弄清哪个问题？",
                "evidence_evaluation": "好，那就先看项目进度：你会先核实哪项信息？",
                "reasoning_argumentation": "好，那就先看进度：你会依据哪项事实判断它是否正常？",
                "multiple_perspectives": "好，那就先看项目进度：谁可能需要调整手上的任务？",
                "integrative_decision": "好，那就先看当前进度：你会先安排哪一步？",
                "dynamic_adjustment": "好，那就先看进度：出现什么结果时你会调整原来的安排？",
            }
            return by_dimension.get(target, default)

        if people_focus:
            by_dimension = {
                "problem_definition": "好，先看组员这边：眼下最需要弄清哪个分工问题？",
                "evidence_evaluation": "好，先看组员这边：你会先核实谁的任务记录？",
                "reasoning_argumentation": "好，先看组员这边：你会依据什么判断谁需要调整？",
                "multiple_perspectives": "好，先看组员这边：谁的任务最需要先确认？",
                "integrative_decision": "好，先看组员这边：你准备先安排谁做什么？",
                "dynamic_adjustment": "好，先看组员这边：出现什么情况时你会调整任务？",
            }
            return by_dimension.get(target, default)
        return default

    @classmethod
    def _contextual_v11_event_message(
        cls,
        plan: InterviewPlanOutput,
        fact: str,
        latest_user_text: str,
        *,
        frame: str,
        default: str,
    ) -> str:
        latest = normalize_spoken_focus(latest_user_text)
        event_code = plan.release_event_code

        if event_code == "evidence_uncertainty":
            if any(marker in latest for marker in ("分工", "分别负责", "职责")):
                return cls._event_intro_message(
                    frame=frame,
                    reason="为了让核实有依据",
                    fact=fact,
                    question="有了这条记录，接下来先核实谁的任务？",
                    information_type="记录信息",
                    preface="你想先把分工弄清楚；",
                )
            return cls._event_intro_message(
                frame=frame,
                reason="为了看看现有依据是否足够",
                fact=fact,
                question="有了这条记录，接下来先核实什么？",
                information_type="记录信息",
            )

        if event_code == "stakeholder_conflict":
            if any(marker in latest for marker in ("延迟", "原因", "负责")):
                preface = "现有信息还不能说明延迟原因；"
                reason = "为了比较团队里的不同顾虑"
                question = "这两边先比较什么？"
            elif any(marker in latest for marker in ("开会", "召集", "讨论", "沟通")):
                preface = "你想把大家叫到一起谈谈；"
                reason = "为了让会上的讨论更具体"
                question = "会上先比较这两边的哪方面？"
            elif any(marker in latest for marker in ("进度", "完成情况", "完成度")):
                preface = "你想先看当前进度；"
                reason = "为了了解这种安排会影响谁"
                question = "这两边先比较什么？"
            else:
                preface = ""
                reason = "为了看清不同参与者的顾虑"
                question = "你会先比较进度还是返工风险？"
            return cls._event_intro_message(
                frame=frame,
                reason=reason,
                fact=fact,
                question=question,
                information_type="参与者信息",
                preface=preface,
            )

        if event_code == "decision_pressure":
            return cls._event_intro_message(
                frame=frame,
                reason=(
                    "为了把这个想法放进具体选择"
                    if "进度" in latest
                    else "为了把前面的考虑放进具体选择"
                ),
                fact=fact,
                question="这些限制下，接下来怎样安排？",
                information_type="安排限制",
                counter="项",
                preface=("你想在进度允许时顾好质量；" if "进度" in latest else ""),
            )

        if event_code == "counter_evidence":
            if any(
                marker in latest
                for marker in ("组员", "同学", "成员", "参与者", "团队")
            ):
                reason = "为了看看组员这边的安排是否还需要调整"
            elif any(marker in latest for marker in ("开会", "召集", "讨论")):
                reason = "为了让开会时有新的判断依据"
            else:
                reason = "为了看看原来的安排是否还站得住"
            return cls._event_intro_message(
                frame=frame,
                reason=reason,
                fact=fact,
                question="看到这个变化，原来的安排中先调整哪一部分？",
                information_type="试用结果",
            )

        if event_code == "integration":
            return cls._event_intro_message(
                frame=frame,
                reason="为了把前面的判断放进实际条件里",
                fact=fact,
                question="最终安排想怎么定，之后依据什么调整？",
                information_type="执行限制",
                counter="项",
            )

        question_match = re.search(r"([^。！？!?\n]+[？?])\s*$", default)
        question = question_match.group(1) if question_match else "这会让你怎样调整？"
        return cls._event_intro_message(
            frame=frame,
            reason="为了继续检验刚才的判断",
            fact=fact,
            question=question,
            information_type="情境信息",
        )

    def _call(
        self,
        renderer_input: dict[str, object],
        template_content: str | None,
        style_version: str,
        timeout_seconds: float | None,
        *,
        repair: str | None,
    ) -> tuple[str, str | None]:
        settings = get_settings()
        humanistic = style_version in {
            HUMANISTIC_INTERVIEWER_STYLE,
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        }
        humanistic_v11 = style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
        candidate_generation = (
            renderer_input.get("generation_mode") == CANDIDATE_GENERATION_MODE
        )
        strict_output = candidate_generation or "draft" not in renderer_input
        validated_plan = renderer_input.get("validated_plan")
        plan_action = (
            validated_plan.get("action") if isinstance(validated_plan, dict) else None
        )
        plan_response_intent = (
            validated_plan.get("response_intent")
            if isinstance(validated_plan, dict)
            else None
        )
        selected_event_frame = renderer_input.get("selected_event_intro_frame")
        selected_event_intro = self._event_intro_phrase(
            str(selected_event_frame),
            counter="条",
            information_type="信息",
        )
        rejected_event_intro = self._event_intro_phrase(
            (
                EVENT_INTRO_FRAME_EVIDENCE
                if selected_event_frame == EVENT_INTRO_FRAME_SUPPLEMENT
                else EVENT_INTRO_FRAME_SUPPLEMENT
            ),
            counter="条",
            information_type="信息",
        )
        event_frame_instruction = (
            f"本轮已选定新信息框架 {selected_event_frame}："
            f"必须在事实前使用‘为了……，{selected_event_intro}：’"
            f"这一类表达，不得使用‘{rejected_event_intro}’所属的另一类框架。"
            if plan_action == "RELEASE_EVENT"
            else ""
        )
        v11_surface_instruction = (
            "这是 clarify_question：selected_question 只是安全兜底，"
            "不得复述整句或说‘刚才问的是’；必须保留 "
            "preceding_interviewer_message 中至少一个具体主题，"
            "用不超过60个汉字的日常语言直接解释后再问一个更小的问题。"
            "latest_user_text 如果是‘具体说说’‘你在说什么’"
            "或仅引用上一句问题，不得追问用户‘具体指哪部分’，"
            "必须由访谈员说清上一句中的具体对象和选择。"
            if humanistic_v11 and plan_response_intent == "clarify_question"
            else (
                "selected_question 只表示问题目标，不是推荐话术；"
                "请结合 latest_user_focus 和可见对话重新组织自然中文，"
                "最后一个问题仍需保留 selected_question_semantic_groups 的语义。"
                if humanistic_v11
                else ""
            )
        )
        turn_shape_instruction = (
            "这是结束轮：只做有来源支持的中性总结，不得提出问题。"
            if plan_action == "CONCLUDE"
            else (
                "这是事件轮：反映从句不得以句末标点结束，必须使用分号" "连接原样事件事实和一个问题。"
                if candidate_generation and plan_action == "RELEASE_EVENT"
                else (
                    "直接承接用户此刻的意思，再提出一个开放或聚焦问题；" "反映是可选的，不要为了承接而复述原句。"
                    if humanistic_v11
                    else "先用用户原话支持的中性反映承接，再提出一个开放或聚焦问题。"
                )
            )
        )
        candidate_reliability_instruction = (
            CANDIDATE_RELIABILITY_INSTRUCTION
            + (
                CANDIDATE_EVENT_SHAPE_INSTRUCTION
                if plan_action == "RELEASE_EVENT"
                else ""
            )
            if candidate_generation
            else ""
        )
        if strict_output:
            render_instruction = (
                "只把计划写成一条自然、非诱导的中文访谈消息。"
                + turn_shape_instruction
                + "一轮一个信息目标，最多两句、90个汉字和一个问号。"
                "不得猜测情绪、人格或动机，不得使用评价性表扬。"
                "反映可以自然转述，但必须在 reflection_source_quotes 中逐字引用依据原话。"
                "事件轮必须原样包含已选 presentation_unit.text，不得改写事实。"
                + event_frame_instruction
                + "当 validated_plan.action 为 RELEASE_EVENT 时，"
                "introduced_fact_codes 必须且只能包含 release_unit_code；"
                "把事件事实和问题合并在同一句中，使整条 message 最多只有两个句末标点。"
                "严格按 validated_plan.action 填写结构元数据："
                "CONCLUDE 不得含问号，question_count=0，message_type=closing；"
                "PROBE 或 CHALLENGE 必须只有一个问号，question_count=1，message_type=followup；"
                "RELEASE_EVENT 必须只有一个问号，question_count=1，message_type=event；"
                "CLARIFY 必须只有一个问号，question_count=1，message_type=clarification；"
                "INTEGRATE 必须只有一个问号，question_count=1，message_type=integration。"
                "非 RELEASE_EVENT 时 introduced_fact_codes 必须为空列表。"
            )
            output_instruction = INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION
            system_output_instruction = "只输出 InterviewerOutput JSON。"
            max_tokens = 700
        else:
            repair_instruction = (
                "这是同一轮的第二次输出，上一次不会展示给用户。"
                f"本次必须优先执行以下修复要求：{repair}"
                if repair
                else ""
            )
            render_instruction = (
                repair_instruction
                + turn_shape_instruction
                + "后端已经冻结测量计划，你只负责生成用户可见的自然表达。"
                + (
                    v11_surface_instruction + "reflection_source_quotes 只用于核验来源，可以不显示；"
                    "不要默认引用或复述用户原句，如需加引号则必须逐字保留；"
                    if humanistic_v11
                    else ""
                )
                + "不得改变问题目标、事实、问号数量或结束含义。"
                "除 CONCLUDE 外，消息必须以一个明确、可回答的问题结束，"
                "并且整条消息恰好只有一个问号，不能只给安抚性陈述。"
                "一轮一个目标，最多两句、90个汉字；不得评分、诱导、"
                "猜测情绪人格动机或使用评价性表扬。"
                "required_fact非空时必须原样保留。"
                + event_frame_instruction
                + "要先读 latest_user_text、preceding_interviewer_message 和"
                " recent_visible_dialogue，承接用户刚才真正关注的内容，"
                "避免连续复用相同开头或把回答改写一遍；目标长度25至60个汉字。"
                "不得使用‘为了判断得更稳妥’或‘顺着这个关注点’"
                "这类可见的固定模板连接语。"
                "不得使用‘你提到’‘你把重点放在’‘你现在关注的是’"
                "‘还有一条情况’‘刚才问的是’‘刚才是在问’"
                "‘刚才的问题是想了解’等报告式或客服式开头。"
                "不得把用户的做法评价为‘第一步’‘好的起点’或类似正向结论。"
                "不得说用户的做法是‘直接’‘实际’‘稳妥’‘合理’或‘不错’的做法。"
                "优先用一个简短、不评价的承接表明已听到用户的当前选择，"
                "再问一个与该选择紧密相关的问题。"
                "使用用户逐字原话承接时，引用后用逗号或分号直接进入"
                "与当前任务有关的问题；不得加入‘顺着’‘从这个关注点’"
                "‘回到这个话题’等解说对话过程的元话语。"
                "如果 response_intent 是 clarify_question：先用具体日常语言"
                "解释或重述前一个访谈问题，再问一个更小的问题；"
                "不得要求用户换一种说法解释他为什么没懂。"
                "如果 response_intent 是 low_information：允许暂不下结论，"
                "结合当前情境把任务拆成一个容易回答的具体小问题；"
                "如果 latest_user_text 是‘不知道’‘不确定’‘没想法’等真正的"
                "不确定表达，不得把这句话加引号复述或当成讨论主题；"
                "如果 latest_user_text 已包含分工、责任、时间、质量等具体主题，"
                "最后的问题必须直接包含该主题，不能只问‘眼前哪一点’；"
                "不得反复说‘换一种说法’或‘从最确定的一点说’。"
                "如果 action 是 RELEASE_EVENT：直接把 required_fact 当作"
                "对话中刚出现的信息自然说出；"
                "必须先用一个短句承接 latest_user_text，再说明新事实为什么在此时出现，"
                "必须严格使用 selected_event_intro_frame 指定的框架"
                "明确标记这是新信息，并用‘为了……’说明引入它的原因；"
                "缺少‘新信息标识’或‘引入原因’任何一项都视为无效；"
                "引入原因必须出现在新信息标识之前，同一条消息不得重复使用‘你想’"
                "或‘你会’反复称呼用户；"
                "不得将新事实冒充为用户追问的答案；"
                "用户问延迟原因而现有事实不足以回答时，要先说明信息边界；"
                "事实后的问题要承接用户刚才准备采取的行动。"
            )
            output_instruction = '只输出严格JSON：{"message":"用户可见消息"}。'
            system_output_instruction = '只输出{"message":"..."} JSON，不能输出其他字段。'
            max_tokens = INTERVIEWER_RENDER_MAX_TOKENS
        payload = {
            "instruction": (
                f"{template_content or ''}\n"
                + candidate_reliability_instruction
                + render_instruction
                + (
                    "只理解用户明确说出的内容，不解释隐藏意义、潜意识、童年因果或人格。"
                    "不得声称与用户亲近、被用户感动、拥有私人经历或被用户改变。"
                    "不得充当亲友、心理咨询师或权威决策者，不提供治疗承诺和行为背书。"
                    "第一人称只可用于承认误解、重复或信息边界。"
                    "尊重语气不得随答案强弱变化，不夸奖、贴标签或把回答评价扩展到整个人。"
                    "允许矛盾、犹豫和不确定性存在，不强迫用户立即选边或消除矛盾。"
                    "依据不足时用暂定措辞邀请纠正，不声称完全理解或拥有用户的体验。"
                    "不得寻求附和、暗示唯一答案、纠正用户或教授标准思路。"
                    "回答含混或偏离时只做中性澄清；不得遗漏 validated_plan 指定的表达任务。"
                    if humanistic
                    else ""
                )
                + output_instruction
            ),
            **renderer_input,
            "repair": repair,
        }
        request = ModelChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        f"你是{INTERVIEWER_DISPLAY_NAME}，负责审辩式思维测评对话。不得评分、暗示答案、"
                        "暴露内部阶段或编造未释放事实。"
                        + ("你不是心理咨询师，不建立私人关系，不推断隐藏心理。" if humanistic else "")
                        + system_output_instruction
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ],
            temperature=0.25 if humanistic_v11 else 0.15,
            max_tokens=max_tokens,
            json_mode=True,
            thinking_enabled=False,
            reasoning_effort="low",
            timeout_seconds=timeout_seconds,
        )

        async def run_with_deadline():
            return await asyncio.wait_for(
                ModelGatewayService(settings).chat(request),
                timeout=timeout_seconds,
            )

        response = asyncio.run(run_with_deadline())
        return response.content, response.model

    @classmethod
    def _parse(
        cls,
        raw: str,
        *,
        deterministic_output: InterviewerOutput | None = None,
        plan: InterviewPlanOutput | None = None,
        unit: object | None = None,
    ) -> InterviewerOutput | None:
        try:
            payload = json.loads(raw.strip())
            if isinstance(payload, dict) and isinstance(
                payload.get("InterviewerOutput"), dict
            ):
                payload = payload["InterviewerOutput"]
            if not isinstance(payload, dict) or not isinstance(
                payload.get("message"), str
            ):
                return None
            message = payload["message"].strip()
            if not message:
                return None
            if deterministic_output is not None:
                return deterministic_output.model_copy(
                    update={
                        "message": message,
                        "question_count": message.count("？") + message.count("?"),
                        "fallback_used": False,
                        "warnings": [
                            *deterministic_output.warnings,
                            (
                                "model supplied compact message only; "
                                "metadata stayed deterministic"
                            ),
                        ],
                    }
                )
            try:
                return InterviewerOutput.model_validate(payload)
            except Exception:  # noqa: BLE001
                if plan is None:
                    return None
                unit_code = getattr(unit, "unit_code", None)
                unit_text = getattr(unit, "text", "").rstrip("。！？!?")
                return InterviewerOutput(
                    message=message,
                    message_type=cls._message_type(plan.action),
                    question_count=message.count("？") + message.count("?"),
                    introduced_fact_codes=(
                        [unit_code]
                        if unit_code and unit_text and unit_text in message
                        else []
                    ),
                    reflection_turn_ids=[],
                    reflection_source_quotes=[],
                    quality_flags=cls._quality_flags(),
                    warnings=["normalized minimal interviewer JSON envelope"],
                )
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def renderer_input_payload(
        cls,
        context: AgentRuntimeContext,
        blueprint: GeneratedScenarioBlueprint,
        plan: InterviewPlanOutput,
        *,
        style_version: str,
    ) -> dict[str, object]:
        """Build the bounded, visible-only payload shared by model and trace."""
        event = next(
            (
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            ),
            None,
        )
        unit = cls._selected_unit(event, plan.release_unit_code)
        reflection_turn_ids = set(plan.reflection_basis_turn_ids)
        reflection_candidates = [
            item
            for item in context.dialogue_history
            if item.speaker == "user"
            and item.turn_id is not None
            and item.turn_id in reflection_turn_ids
        ]
        reflection_source_turns = [
            {
                "turn_id": item.turn_id,
                "speaker": item.speaker,
                "content": item.content,
            }
            for item in reflection_candidates[-1:]
        ]
        latest = context.latest_user_turn
        event_intro_audit = cls.event_intro_frame_audit(
            context,
            event_turn=plan.action == "RELEASE_EVENT",
        )
        payload: dict[str, object] = {
            "style_version": style_version,
            **event_intro_audit,
            "validated_plan": plan.model_dump(mode="json"),
            "allowed_facts": (
                [
                    {
                        "event_code": event.event_code,
                        "unit_code": unit.unit_code,
                        "text": unit.text,
                    }
                ]
                if event is not None and unit is not None
                else []
            ),
            "reflection_source_turns": reflection_source_turns,
            "specified_user_turn": (
                {
                    "turn_id": latest.turn_id,
                    "speaker": latest.speaker,
                    "content": latest.content,
                }
                if latest is not None and latest.speaker == "user"
                else None
            ),
            "recent_visible_messages": [
                {
                    "turn_id": item.turn_id,
                    "speaker": item.speaker,
                    "content": item.content,
                }
                for item in context.dialogue_history[-4:]
                if item.speaker in {"user", "ai"}
            ],
        }
        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1:
            previous_questions = [
                item.content
                for item in context.dialogue_history
                if item.speaker == "ai" and "?" in item.content.replace("？", "?")
            ][-8:]
            microstructure = build_v11_microstructure(
                context,
                plan,
                previous_questions=previous_questions,
            )
            payload.update(
                fit_v11_length_budget(
                    plan,
                    microstructure,
                    event_fact=unit.text if unit is not None else None,
                )
            )
        return payload

    @classmethod
    def runtime_renderer_input_payload(
        cls,
        context: AgentRuntimeContext,
        blueprint: GeneratedScenarioBlueprint,
        plan: InterviewPlanOutput,
        *,
        style_version: str,
    ) -> dict[str, object]:
        """Build the compact visible-only payload used by live rendering."""
        event = next(
            (
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            ),
            None,
        )
        unit = cls._selected_unit(event, plan.release_unit_code)
        event_intro_audit = cls.event_intro_frame_audit(
            context,
            event_turn=plan.action == "RELEASE_EVENT",
        )
        draft = cls()._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=style_version,
        )
        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1:
            previous_questions = [
                item.content
                for item in context.dialogue_history
                if item.speaker == "ai" and "?" in item.content.replace("？", "?")
            ][-8:]
            microstructure = build_v11_microstructure(
                context,
                plan,
                previous_questions=previous_questions,
            )
            microstructure = fit_v11_length_budget(
                plan,
                microstructure,
                event_fact=unit.text if unit is not None else None,
            )
            return {
                "style_version": style_version,
                **event_intro_audit,
                "validated_plan": {
                    "response_intent": plan.response_intent,
                    "action": plan.action,
                    "delivery_mode": plan.delivery_mode,
                    "target_dimension": plan.target_dimension,
                    "target_evidence": plan.target_evidence,
                    "question_intent": plan.question_intent,
                    "release_event_code": plan.release_event_code,
                    "release_unit_code": plan.release_unit_code,
                },
                "draft": draft.message,
                "required_fact": (
                    {"unit_code": unit.unit_code, "text": unit.text}
                    if event is not None and unit is not None
                    else None
                ),
                **microstructure,
                "recent_questions": previous_questions[-3:],
                "latest_user_text": (
                    context.latest_user_turn.content[:320]
                    if context.latest_user_turn is not None
                    else ""
                ),
                "latest_user_focus": (
                    normalize_spoken_focus(context.latest_user_turn.content)[:80]
                    if context.latest_user_turn is not None
                    else ""
                ),
                "preceding_interviewer_message": (
                    cls._clarification_source_message(context)[:320]
                    if plan.response_intent == "clarify_question"
                    else next(
                        (
                            item.content[:320]
                            for item in reversed(context.dialogue_history[:-1])
                            if item.speaker == "ai"
                        ),
                        "",
                    )
                ),
                "recent_visible_dialogue": [
                    {
                        "speaker": item.speaker,
                        "content": item.content[:320],
                    }
                    for item in context.dialogue_history[-6:]
                    if item.speaker in {"user", "ai"}
                ],
            }
        source_quote = (
            draft.reflection_source_quotes[0].model_dump(mode="json")
            if draft.reflection_source_quotes
            else None
        )
        return {
            "style_version": style_version,
            **event_intro_audit,
            "validated_plan": {
                "action": plan.action,
                "delivery_mode": plan.delivery_mode,
                "question_intent": plan.question_intent,
                "release_event_code": plan.release_event_code,
                "release_unit_code": plan.release_unit_code,
            },
            "draft": draft.message,
            "required_fact": (
                {"unit_code": unit.unit_code, "text": unit.text}
                if event is not None and unit is not None
                else None
            ),
            "source_quote": source_quote,
            "recent_questions": [
                item.content
                for item in context.dialogue_history[-4:]
                if item.speaker == "ai" and "?" in item.content.replace("？", "?")
            ][-3:],
        }

    @staticmethod
    def _v11_audit_metadata(
        renderer_input: dict[str, object],
    ) -> dict[str, object]:
        expression_audit_keys = (
            "event_intro_selector_version",
            "previous_event_intro_frame",
            "selected_event_intro_frame",
        )
        audit = {
            key: renderer_input[key]
            for key in expression_audit_keys
            if key in renderer_input
        }
        if renderer_input.get("style_version") != HUMANISTIC_INTERVIEWER_STYLE_V1_1:
            return audit
        keys = (
            "microstructure_version",
            "candidate_selector_version",
            "intent_registry_version",
            "candidate_intent_key",
            "candidate_mapping_source",
            "candidate_mapping_fields",
            "candidate_mapping_fingerprint",
            "planner_question_intent",
            "planner_target_evidence",
            "question_candidates",
            "selected_candidate_id",
            "selected_question",
            "selected_candidate_intent_key",
            "selection_reason",
            "selector_fallback_reason",
            "reflection_source_quotes",
            "reflection_side_type",
            "tentative_check",
            "interaction_bridge_version",
            "interaction_bridge_mode",
            "authority_request_kind",
            "pure_authority_request",
            "mixed_authority_request",
            "autonomy_boundary",
            "reflection_adjustment_reason",
            "compact_event_fact",
            "event_presentation_adjustment",
            "autonomy_boundary_adjustment",
            "semantic_compact_fallback",
            "selected_question_semantic_groups",
            "candidate_selection_applied",
            "renderer_bypass_reason",
        )
        audit.update(
            {key: renderer_input[key] for key in keys if key in renderer_input}
        )
        return audit

    @staticmethod
    def v11_requires_model_polish(
        renderer_input: dict[str, object],
        *,
        mode: str,
    ) -> bool:
        """Decide whether one bounded live expression call is worthwhile."""
        if mode == "always":
            return True
        if mode == "off":
            return False
        if mode == "adaptive":
            action = (renderer_input.get("validated_plan") or {}).get("action")
            return action in {
                "PROBE",
                "CHALLENGE",
                "RELEASE_EVENT",
                "CLARIFY",
                "INTEGRATE",
            }
        if mode != "complex_only":
            raise ValueError(f"unsupported v1.1 polish mode: {mode}")
        return bool(
            renderer_input.get("reflection_side_type") == "double"
            or renderer_input.get("tentative_check") is True
            or renderer_input.get("mixed_authority_request") is True
        )

    @staticmethod
    def _v11_contract_errors(
        output: InterviewerOutput,
        renderer_input: dict[str, object],
    ) -> list[str]:
        errors: list[str] = []
        plan: InterviewPlanOutput | None = None
        validated_plan = renderer_input.get("validated_plan")
        if isinstance(validated_plan, dict):
            try:
                plan = InterviewPlanOutput.model_validate(
                    {
                        **validated_plan,
                        "active_topic": "v1.1 semantic contract",
                        "reflection_basis_turn_ids": [],
                        "reason": "reconstructed from protected renderer input",
                        "budget": {
                            "used_turns": 0,
                            "remaining_turns": 0,
                            "reserved_update_turns": 0,
                            "reserved_closure_turns": 0,
                        },
                    }
                )
            except Exception:  # noqa: BLE001
                errors.append("invalid_v11_validated_plan")
            else:
                errors.extend(semantic_binding_contract_errors(plan, renderer_input))
        else:
            errors.append("missing_v11_validated_plan")
        intent_key = renderer_input.get("candidate_intent_key")
        candidate_rows = renderer_input.get("question_candidates", []) or []
        if not isinstance(intent_key, str) or not intent_key:
            errors.append("missing_candidate_intent_key")
        if not isinstance(candidate_rows, list) or any(
            not isinstance(row, dict) or row.get("intent_key") != intent_key
            for row in candidate_rows
        ):
            errors.append("candidate_intent_mismatch")
        verified_source_quotes: list[str] = []
        for source in renderer_input.get("reflection_source_quotes", []) or []:
            if not isinstance(source, dict):
                errors.append("invalid_reflection_source_quote")
                continue
            quote = source.get("quote")
            if not isinstance(quote, str) or not quote:
                errors.append("invalid_reflection_source_quote")
            else:
                verified_source_quotes.append(quote)
        visible_quotes = re.findall(r"“([^”]{1,80})”", output.message)
        quote_support = [
            *verified_source_quotes,
            str(renderer_input.get("latest_user_text") or ""),
            str(renderer_input.get("preceding_interviewer_message") or ""),
            str((renderer_input.get("required_fact") or {}).get("text") or "")
            if isinstance(renderer_input.get("required_fact"), dict)
            else "",
        ]
        if any(
            not any(visible in source for source in quote_support if source)
            for visible in visible_quotes
        ):
            errors.append("unsupported_visible_quote")
        selected_question = renderer_input.get("selected_question")
        selected_candidate_id = renderer_input.get("selected_candidate_id")
        if (
            isinstance(selected_candidate_id, str)
            and selected_candidate_id
            and selected_candidate_id
            not in {
                "v11_deterministic_safe_fallback",
                "v11_event_length_safe_fallback",
                "v11_semantic_length_safe_fallback",
                "v11_conclude_no_question",
            }
        ):
            selected_rows = [
                row
                for row in candidate_rows
                if isinstance(row, dict)
                and row.get("candidate_id") == selected_candidate_id
            ]
            if (
                len(selected_rows) != 1
                or selected_rows[0].get("text") != selected_question
                or selected_rows[0].get("intent_key") != intent_key
            ):
                errors.append("selected_candidate_audit_mismatch")
        if renderer_input.get("selected_candidate_intent_key") != intent_key:
            errors.append("selected_candidate_intent_mismatch")
        action = (renderer_input.get("validated_plan") or {}).get("action")
        if action != "CONCLUDE" and plan is not None:
            binding = resolve_intent_binding(
                plan,
                pure_authority=bool(renderer_input.get("pure_authority_request")),
                latest_user_text=str(renderer_input.get("latest_user_text") or ""),
            )
            question_match = re.search(
                r"([^.。！？!?;；\n]+[？?])\s*$",
                output.message,
            )
            if binding is None or question_match is None:
                errors.append("missing_surface_question")
            elif plan.response_intent == "clarify_question":
                preceding = renderer_input.get("preceding_interviewer_message")
                if not isinstance(
                    preceding, str
                ) or not InterviewerAgent._clarification_semantically_grounded(
                    preceding, output.message
                ):
                    errors.append("missing_clarified_question_grounding")
                if len(output.message) > 64:
                    errors.append("clarification_too_long")
                if re.search(
                    r"(?:具体说说|你(?:在)?说什么).{0,6}具体指"
                    r"|这个问题最关键的边界或限制"
                    r"|简单说[,，]?你现在(?:会)?先确认哪一点",
                    output.message,
                ):
                    errors.append("clarification_loop")
                compact_preceding = re.sub(r"\s+", "", preceding)
                if (
                    "两边" in compact_preceding
                    or (
                        "一部分参与者" in compact_preceding
                        and "另一部分" in compact_preceding
                    )
                ) and not (
                    "进度" in output.message
                    and any(term in output.message for term in ("返工", "质量"))
                ):
                    errors.append("missing_two_sided_clarification")
            elif plan.response_intent == "low_information":
                latest = renderer_input.get("latest_user_text")
                if isinstance(latest, str):
                    if InterviewerAgent._is_substantive_low_information(latest):
                        if not InterviewerAgent._has_visible_topic_overlap(
                            latest,
                            question_match.group(1),
                        ):
                            errors.append("missing_low_information_topic_grounding")
                    elif (
                        latest.strip()
                        and latest.strip(" ，,；;。！？!?“”\"'") in output.message
                    ):
                        errors.append("echoed_uncertainty_as_topic")
                errors.extend(
                    surface_question_semantic_errors(
                        question_match.group(1),
                        binding=binding,
                    )
                )
            else:
                errors.extend(
                    surface_question_semantic_errors(
                        question_match.group(1),
                        binding=binding,
                    )
                )
        required_fact = renderer_input.get("required_fact")
        if isinstance(required_fact, dict):
            fact_text = required_fact.get("text")
            if isinstance(fact_text, str):
                exact_fact = fact_text.rstrip("。！？!?")
                if exact_fact and exact_fact not in output.message:
                    errors.append("missing_required_fact_verbatim")
        errors.extend(
            InterviewerAgent._v11_focus_contract_errors(
                output.message,
                renderer_input,
            )
        )
        for phrase in _V11_REPETITIVE_STOCK_PHRASES:
            if phrase in output.message:
                errors.append("repetitive_stock_phrase")
        if re.search(
            r"(?:是|算)(?:(?:个|一个).{0,8}(?:起点|思路|选择|判断)"
            r"|(?:一个)?(?:很)?(?:好|对|合理)?的?第一步"
            r"|(?:个|一个)(?:很)?(?:直接|实际|稳妥|清晰|合理|不错|好)"
            r"的?(?:做法|办法|方式|行动))",
            output.message,
        ):
            errors.append("evaluative_acknowledgement")
        if re.search(r"在[“\"][^”\"]+[”\"]里", output.message):
            errors.append("quoted_topic_frame")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _v11_focus_contract_errors(
        message: str,
        renderer_input: dict[str, object],
    ) -> list[str]:
        """Enforce general continuity structure without surface-word overfitting."""

        validated_plan = renderer_input.get("validated_plan")
        if not isinstance(validated_plan, dict):
            return []
        action = validated_plan.get("action")
        if action != "RELEASE_EVENT":
            return []
        selected_frame = renderer_input.get("selected_event_intro_frame")
        new_information_markers = _EVENT_INTRO_FRAME_MARKERS.get(
            str(selected_frame),
            tuple(
                marker
                for markers in _EVENT_INTRO_FRAME_MARKERS.values()
                for marker in markers
            ),
        )
        introduction_reason_markers = (
            "为了",
            "为核实",
            "为比较",
            "为选择",
            "为调整",
            "为整合",
            "为继续判断",
        )
        new_information_positions = [
            message.find(marker)
            for marker in new_information_markers
            if marker in message
        ]
        introduction_reason_positions = [
            message.find(marker)
            for marker in introduction_reason_markers
            if marker in message
        ]
        if not new_information_positions or not introduction_reason_positions:
            return ["missing_event_introduction"]
        if InterviewerAgent._event_intro_frames_in_message(message) != {
            selected_frame
        }:
            return ["event_intro_frame_mismatch"]
        if min(introduction_reason_positions) > min(new_information_positions):
            return ["event_introduction_out_of_order"]
        if message.count("你想") + message.count("你会") > 1:
            return ["repetitive_event_address"]
        return []

    @staticmethod
    def _has_visible_topic_overlap(source: str, message: str) -> bool:
        def bigrams(value: str) -> set[str]:
            compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", value)
            return {
                compact[index : index + 2]
                for index in range(max(len(compact) - 1, 0))
                if compact[index : index + 2] not in _CLARIFICATION_OVERLAP_STOP_BIGRAMS
            }

        return bool(bigrams(source) & bigrams(message))

    @staticmethod
    def _clarification_semantically_grounded(source: str, message: str) -> bool:
        if InterviewerAgent._has_visible_topic_overlap(source, message):
            return True
        compact_source = re.sub(r"\s+", "", source)
        compact_message = re.sub(r"\s+", "", message)
        families = (
            (
                ("影响谁", "谁会", "直接影响"),
                ("谁",),
                ("影响", "工作", "调整"),
            ),
            (
                ("延迟", "边界", "限制"),
                ("延迟",),
                ("原因", "任务", "交接", "记录"),
            ),
            (
                ("进度", "质量", "角色冲突"),
                ("进度", "质量"),
                ("比较", "影响", "冲突"),
            ),
            (
                ("组员", "成员", "同学"),
                ("组员", "成员", "同学", "这些人"),
                ("谁", "任务"),
            ),
        )
        for source_terms, message_terms_a, message_terms_b in families:
            if (
                any(term in compact_source for term in source_terms)
                and any(term in compact_message for term in message_terms_a)
                and any(term in compact_message for term in message_terms_b)
            ):
                return True
        return False

    @staticmethod
    def _is_substantive_low_information(text: str) -> bool:
        compact = re.sub(r"\s+", "", normalize_spoken_focus(text))
        if len(compact) < 2:
            return False
        return not any(
            marker in compact
            for marker in (
                "不知道",
                "不确定",
                "不清楚",
                "没想法",
                "没想好",
                "没想清楚",
                "随便",
                "说不上来",
            )
        )

    @staticmethod
    def _clarification_source_message(context: AgentRuntimeContext) -> str:
        """Prefer the last concrete interviewer question over a failed meta-loop."""

        ai_turns = [
            item
            for item in reversed(context.dialogue_history[:-1])
            if item.speaker == "ai" and item.content.strip()
        ]
        if not ai_turns:
            return ""
        loop_markers = (
            "具体说说具体指",
            "你在说什么具体指",
            "你说什么具体指",
            "这个问题最关键的边界或限制",
            "简单说，你现在会先确认哪一点",
            "你现在最想先确认什么",
            "上一问是",
            "刚才问的是",
            "我换个更直接的问法",
            "我重新说一下刚才的问题",
        )
        concrete_turn = next(
            (
                item
                for item in ai_turns[:12]
                if item.content_type != "interview_clarification"
                and not any(marker in item.content for marker in loop_markers)
            ),
            None,
        )
        if concrete_turn is not None:
            return concrete_turn.content.strip()
        return next(
            (
                item.content.strip()
                for item in ai_turns
                if not any(marker in item.content for marker in loop_markers)
            ),
            ai_turns[0].content.strip(),
        )

    @staticmethod
    def _plain_clarification_message(preceding: str) -> str:
        return InterviewerAgent._plain_clarification_messages(preceding)[0]

    @staticmethod
    def _plain_clarification_messages(preceding: str) -> tuple[str, ...]:
        compact = re.sub(r"\s+", "", preceding)
        if (
            "两边" in compact
            or ("一部分参与者" in compact and "另一部分" in compact)
        ) and any(marker in compact for marker in ("进度", "返工", "质量")):
            return (
                "这里的“两边”，是一边想赶进度，另一边担心返工和质量。"
                "你想先比较进度收益还是质量风险？",
                "简单说，一边更看重进度，另一边更担心返工。"
                "你想先看哪方面的影响？",
                "我说的是进度和返工风险之间的取舍。"
                "你现在更想先弄清哪一边？",
            )
        if "核实" in compact and "信息" in compact:
            return (
                "我换个更直接的问法：你会先核实哪项信息？",
                "具体一点，哪条信息最需要先确认？",
                "你打算从哪项信息开始查？",
            )
        if any(marker in compact for marker in ("影响谁", "谁会", "谁的工作")):
            return (
                "我换个更直接的问法：除了你自己，谁的工作会跟着调整？",
                "这个安排一变，哪位组员需要跟着调整任务？",
                "除了你，还有谁会受到这个安排的影响？",
            )
        if "延迟" in compact:
            return (
                "我换个更直接的问法：要查延迟原因，你会先看任务进度还是交接记录？",
                "你会先看任务完成情况，还是先查交接记录？",
                "先核对哪一项，更容易找到延迟原因？",
            )
        if any(marker in compact for marker in ("进度", "质量", "角色冲突")):
            return (
                "我换个更直接的问法：进度和质量冲突时，你会先比较哪方面的影响？",
                "如果赶进度可能增加返工，你会先看什么？",
                "进度和返工风险之间，你会怎样定先后？",
            )
        if any(marker in compact for marker in ("限制", "约束", "初步决定")):
            return (
                "我换个更直接的问法：新安排、原安排和小范围试用里，你会先选哪一种？",
                "在这些限制下，你会先采用哪种安排？",
                "考虑到这些条件，你现在的初步选择是什么？",
            )
        if "边界" in compact:
            return (
                "我换个更直接的问法：这个问题的范围到哪里？",
                "你想先把这个问题限定在哪个范围？",
                "哪些情况属于这个问题，哪些不属于？",
            )
        if any(marker in compact for marker in ("组员", "成员", "同学")):
            return (
                "我换个更直接的问法：这些人里，你会先看谁的任务？",
                "这些组员里，谁的任务最需要先确认？",
                "你准备先了解哪位组员的任务？",
            )
        question = next(
            (
                item.strip()
                for item in reversed(re.split(r"[。；;！!]", preceding))
                if "？" in item or "?" in item
            ),
            "",
        )
        question = re.sub(
            r"^(?:我想问的是|刚才的问题是|简单说|具体一点)[：:，,\s]*",
            "",
            question,
        )
        if question:
            direct = question[:42]
            return (
                f"我换个更直接的问法：{direct}",
                f"具体一点，{direct}",
                "你现在最想先确认什么？",
            )
        return (
            "我换个更直接的问法：你会先看哪一点？",
            "具体一点，你现在最想确认什么？",
            "你想先从哪件事开始判断？",
        )

    @staticmethod
    def _retry_reason(exc: Exception) -> str | None:
        if isinstance(exc, TimeoutError):
            return "TimeoutError"
        text = str(exc)
        return next(
            (marker for marker in RENDER_RETRY_ERROR_MARKERS if marker in text),
            None,
        )

    @staticmethod
    def _error_text(exc: Exception) -> str:
        detail = str(exc).strip()
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

    @staticmethod
    def _message_type(action: str) -> str:
        return {
            "CLARIFY": "clarification",
            "RELEASE_EVENT": "event",
            "INTEGRATE": "integration",
            "CONCLUDE": "closing",
        }.get(action, "followup")

    @staticmethod
    def _choose_unused_question(
        candidates: tuple[str, ...],
        previous_messages: list[str],
    ) -> str:
        """Prefer a question that was not used in recent visible dialogue."""
        recent_messages = previous_messages[-8:]
        for candidate in candidates:
            if all(candidate not in message for message in recent_messages):
                return candidate

        if previous_messages:
            for candidate in candidates:
                if candidate not in previous_messages[-1]:
                    return candidate
        return candidates[0]

    @classmethod
    def _clarify_message(
        cls,
        response_intent: str,
        previous_messages: list[str],
        *,
        humanistic: bool = False,
    ) -> str:
        if response_intent == "explain_term":
            candidates = (
                "可以先按日常理解来回答。你现在最直接的判断是什么？",
                "不需要使用专业术语。你会怎样理解眼前的情况？",
            )
        else:
            candidates = (
                (
                    "简单说，你现在会先确认哪一点？",
                    "具体一点，你打算先查什么？",
                    "你想先从哪件事开始判断？",
                )
                if humanistic
                else (
                    "简单说，你现在会先确认哪一点？",
                    "具体一点，你打算先查什么？",
                    "你想先从哪件事开始判断？",
                )
            )
        return cls._choose_unused_question(candidates, previous_messages)

    @staticmethod
    def _probe_message(
        dimension: str | None,
        previous_messages: list[str] | None = None,
    ) -> str:
        if previous_messages is not None:
            return InterviewerAgent.select_probe_message(
                dimension,
                previous_questions=previous_messages,
            )
        return PROBE_MESSAGE_BANK.get(
            dimension or "",
            ("可以具体说说这个判断背后的主要依据吗？",),
        )[0]

    @staticmethod
    def select_probe_message(
        dimension: str | None,
        *,
        previous_questions: list[str],
    ) -> str:
        candidates = PROBE_MESSAGE_BANK.get(
            dimension or "",
            (
                "可以具体说说这个判断背后的主要依据吗？",
                "你会用什么事实或结果检查这个判断？",
            ),
        )
        normalized_previous = [
            InterviewQuestionValidator._normalize_question(item)  # noqa: SLF001
            for item in previous_questions
        ]
        for candidate in candidates:
            normalized = InterviewQuestionValidator._normalize_question(  # noqa: SLF001
                candidate
            )
            if any(
                normalized in previous
                or InterviewQuestionValidator._semantically_similar(  # noqa: SLF001
                    normalized, previous
                )
                for previous in normalized_previous
            ):
                continue
            return candidate
        return candidates[-1]

    @staticmethod
    def _selected_unit(event: object | None, unit_code: str | None):
        if event is None or unit_code is None:
            return None
        return next(
            (unit for unit in event.presentation_units if unit.unit_code == unit_code),
            None,
        )

    @staticmethod
    def _grounded_reflection(
        context: AgentRuntimeContext,
        delivery_mode: str,
    ) -> str:
        latest = context.latest_user_turn
        text = latest.content if latest else ""
        if "返工" in text and any(marker in text for marker in ("调整", "暂停", "停止", "恢复")):
            return "你根据返工变化调整了原来的安排"
        if any(marker in text for marker in ("两人", "两个人", "人手", "资源")) and any(
            marker in text for marker in ("收缩", "核心", "非核心", "范围")
        ):
            return "你在人员限制下重新收缩了交付范围"
        if "试用" in text and any(marker in text for marker in ("条件", "回滚", "扩大", "停止")):
            return "你为小范围试用设置了检查和停止条件"
        if "进度" in text and any(marker in text for marker in ("质量", "返工", "风险")):
            return "你把进度和质量风险放在一起权衡"
        if any(marker in text for marker in ("交叉", "来源", "样本", "历史")):
            return "你把信息来源和适用范围纳入了核实"
        if any(marker in text for marker in ("核实", "数据", "日志", "功能")):
            return "你先想到核实现状，这个切入点很具体"
        if any(marker in text for marker in ("团队", "用户", "各方", "同事", "客服")):
            return "你注意到了安排会影响不同参与者"
        if any(marker in text for marker in ("风险", "因为", "依据", "前提")):
            return "你在把判断依据和风险放在一起比较"
        if any(marker in text for marker in ("安排", "方案", "第一步", "先")):
            return "你已经提出了一个具体的推进动作"
        if (
            delivery_mode
            in {
                "reflective_probe",
                "summary_check",
                "event_link",
                "perspective_shift",
            }
            and text
        ):
            return "你已经给出了一个初步判断"
        return ""

    @staticmethod
    def _reflection_turn(
        context: AgentRuntimeContext,
        plan: InterviewPlanOutput,
    ):
        allowed_ids = set(plan.reflection_basis_turn_ids)
        return next(
            (
                item
                for item in reversed(context.dialogue_history)
                if item.speaker == "user"
                and item.turn_id is not None
                and item.turn_id in allowed_ids
            ),
            None,
        )

    @staticmethod
    def _reflection_quote(content: str) -> str:
        stripped = content.strip().replace("\n", " ")
        for marker in ("。", "；", "！", "？", "，"):
            if marker in stripped[:24]:
                candidate = stripped.split(marker, 1)[0].strip()
                if candidate:
                    return candidate[:20]
        return stripped[:20]

    @classmethod
    def _humanistic_reflection(
        cls,
        context: AgentRuntimeContext,
        plan: InterviewPlanOutput,
    ) -> str:
        if plan.delivery_mode not in {
            "reflective_probe",
            "summary_check",
            "event_link",
            "perspective_shift",
        }:
            return ""
        source = cls._reflection_turn(context, plan)
        if source is None:
            return ""
        quote = cls._reflection_quote(source.content)
        return f"你刚才提到“{quote}”" if quote else ""

    @classmethod
    def _approved_reflection(
        cls,
        context: AgentRuntimeContext,
        delivery_mode: str,
    ) -> str:
        if delivery_mode not in {
            "reflective_probe",
            "summary_check",
            "event_link",
            "perspective_shift",
        }:
            return ""
        return cls._grounded_reflection(context, delivery_mode)

    @staticmethod
    def _event_question(event_code: str) -> str:
        return {
            "evidence_uncertainty": "这会怎样影响你对这份信息的判断？",
            "stakeholder_conflict": "面对赶进度和避免返工这两个诉求，你会先怎么协调？",
            "decision_pressure": "在继续逐项检查、减少检查或只在非关键部分试用之间，你会怎么选？",
            "counter_evidence": "这会让你调整原来的哪一部分？",
            "integration": "结合这些情况，你最终会怎么安排？",
        }.get(event_code, "这会怎样影响你刚才的判断？")

    @staticmethod
    def _quality_flags() -> InterviewQualityFlags:
        return InterviewQualityFlags(
            single_focus=True,
            faithful_reflection=True,
            non_judgmental=True,
            non_leading=True,
            no_internal_terms=True,
            no_unreleased_facts=True,
        )


__all__ = [
    "BASELINE_INTERVIEWER_STYLE",
    "EVENT_INTRO_FRAME_EVIDENCE",
    "EVENT_INTRO_FRAME_SUPPLEMENT",
    "EVENT_INTRO_SELECTOR_VERSION",
    "HUMANISTIC_INTERVIEWER_PROMPT_VERSION",
    "HUMANISTIC_INTERVIEWER_STYLE_V1_1",
    "RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION",
    "RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION_V1_1",
    "RUNTIME_INTERVIEWER_PROMPT_VERSION",
    "HUMANISTIC_INTERVIEWER_STYLE",
    "INTERVIEWER_DISPLAY_NAME",
    "INTERVIEWER_PROMPT_VERSION",
    "InterviewerAgent",
    "InterviewerAgentResult",
]
