from __future__ import annotations

import asyncio
import json
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
from app.agents.progressive_schemas import (
    InterviewPlanOutput,
    InterviewQualityFlags,
    InterviewerOutput,
    ReflectionSourceQuote,
)
from app.agents.schemas import AgentRuntimeContext
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService


INTERVIEWER_PROMPT_VERSION = "progressive_interviewer_v3_1"
HUMANISTIC_INTERVIEWER_PROMPT_VERSION = "humanistic_interviewer_v1"
BASELINE_INTERVIEWER_STYLE = "baseline_v1"
HUMANISTIC_INTERVIEWER_STYLE = "humanistic_v1"
CANDIDATE_GENERATION_MODE = "frozen_candidate_v1"


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


class InterviewerAgent:
    def __init__(self) -> None:
        self.validator = InterviewQuestionValidator()

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
        allow_model_call: bool = True,
        renderer_input: dict[str, object] | None = None,
    ) -> InterviewerAgentResult:
        started = perf_counter()
        settings = get_settings()
        humanistic = style_version == HUMANISTIC_INTERVIEWER_STYLE
        if settings.MODEL_GATEWAY_MODE.lower() == "mock":
            output = self._fallback(
                plan,
                blueprint,
                context,
                style_version=style_version,
            )
            return InterviewerAgentResult(
                output=output,
                raw_output=output.model_dump_json(),
                model_name="mock",
                duration_ms=int((perf_counter() - started) * 1000),
            )
        if not allow_model_call or (timeout_seconds is not None and timeout_seconds < 1):
            output = self._fallback(
                plan,
                blueprint,
                context,
                style_version=style_version,
            ).model_copy(
                update={
                    "fallback_used": True,
                    "warnings": [
                        "renderer latency budget exhausted; deterministic fallback used"
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
            )

        raw = ""
        errors: list[str] = []
        renderer_input = renderer_input or self.renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=style_version,
        )
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
        try:
            raw, model = self._call(
                renderer_input,
                template_content,
                style_version=style_version,
                timeout_seconds=timeout_seconds,
                repair=None,
            )
            output = self._parse(
                raw,
                plan=plan,
                unit=unit,
            )
            valid, errors = (
                self.validator.validate(
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
                if output
                else (False, ["invalid_json"])
            )
            if not valid and not humanistic:
                raw, model = self._call(
                    renderer_input,
                    template_content,
                    style_version=style_version,
                    timeout_seconds=timeout_seconds,
                    repair=f"修复这些质量问题：{errors}。只返回 JSON。",
                )
                output = self._parse(
                    raw,
                    plan=plan,
                    unit=unit,
                )
                valid, errors = (
                    self.validator.validate(
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
                    if output
                    else (False, ["invalid_json"])
                )
            if not output or not valid:
                raise ValueError(f"interviewer output invalid: {errors}")
            return InterviewerAgentResult(
                output=output,
                raw_output=raw,
                model_name=model,
                duration_ms=int((perf_counter() - started) * 1000),
                validation_errors=list(dict.fromkeys(errors)),
            )
        except Exception as exc:  # noqa: BLE001
            output = self._fallback(
                plan,
                blueprint,
                context,
                style_version=style_version,
            ).model_copy(
                update={
                    "fallback_used": True,
                    "warnings": [
                        "interviewer model unavailable or unsafe; deterministic renderer used"
                    ],
                }
            )
            return InterviewerAgentResult(
                output=output,
                raw_output=raw or str(exc),
                model_name=None,
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
                validation_errors=list(
                    dict.fromkeys([*errors, "renderer_exception"])
                ),
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
            if style_version == HUMANISTIC_INTERVIEWER_STYLE
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
        humanistic = style_version == HUMANISTIC_INTERVIEWER_STYLE
        reflection = (
            self._humanistic_reflection(context, plan)
            if humanistic
            else self._grounded_reflection(context, plan.delivery_mode)
        )
        previous_messages = [
            item.content
            for item in context.dialogue_history
            if item.speaker == "ai"
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
            if humanistic:
                lead = (
                    f"{reflection}；再补充一条情况：{fact}。"
                    if reflection
                    else f"再补充一条情况：{fact}。"
                )
            else:
                lead = (
                    f"{reflection}；据了解，{fact}。"
                    if reflection
                    else f"据了解，{fact}。"
                )
            message = f"{lead}{self._event_question(event.event_code)}"
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
        return InterviewerOutput(
            message=message,
            message_type=message_type,
            question_count=question_count,
            introduced_fact_codes=[unit.unit_code] if unit else [],
            reflection_turn_ids=(
                [reflection_turn.turn_id]
                if reflection_turn and reflection_turn.turn_id is not None
                else []
            ),
            reflection_source_quotes=reflection_sources,
            quality_flags=self._quality_flags(),
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
        humanistic = style_version == HUMANISTIC_INTERVIEWER_STYLE
        candidate_generation = (
            renderer_input.get("generation_mode")
            == CANDIDATE_GENERATION_MODE
        )
        validated_plan = renderer_input.get("validated_plan")
        plan_action = (
            validated_plan.get("action")
            if isinstance(validated_plan, dict)
            else None
        )
        turn_shape_instruction = (
            "这是结束轮：只做有来源支持的中性总结，不得提出问题。"
            if plan_action == "CONCLUDE"
            else (
                "这是事件轮：反映从句不得以句末标点结束，必须使用分号"
                "连接原样事件事实和一个问题。"
                if candidate_generation and plan_action == "RELEASE_EVENT"
                else "先用用户原话支持的中性反映承接，再提出一个开放或聚焦问题。"
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
        payload = {
            "instruction": (
                f"{template_content or ''}\n"
                + candidate_reliability_instruction
                + "只把计划写成一条自然、非诱导的中文访谈消息。"
                + turn_shape_instruction
                + "一轮一个信息目标，最多两句、90个汉字和一个问号。"
                "不得猜测情绪、人格或动机，不得使用评价性表扬。"
                "反映可以自然转述，但必须在 reflection_source_quotes 中逐字引用依据原话。"
                "事件轮必须原样包含已选 presentation_unit.text，不得改写事实。"
                "当 validated_plan.action 为 RELEASE_EVENT 时，"
                "introduced_fact_codes 必须且只能包含 release_unit_code；"
                "把事件事实和问题合并在同一句中，使整条 message 最多只有两个句末标点。"
                "严格按 validated_plan.action 填写结构元数据："
                "CONCLUDE 不得含问号，question_count=0，message_type=closing；"
                "PROBE 或 CHALLENGE 必须只有一个问号，question_count=1，message_type=followup；"
                "RELEASE_EVENT 必须只有一个问号，question_count=1，message_type=event；"
                "CLARIFY 必须只有一个问号，question_count=1，message_type=clarification；"
                "INTEGRATE 必须只有一个问号，question_count=1，message_type=integration。"
                "非 RELEASE_EVENT 时 introduced_fact_codes 必须为空列表。"
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
                + INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION
            ),
            **renderer_input,
            "repair": repair,
        }
        request = ModelChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是审辩式思维测评的统一访谈员。不得评分、暗示答案、"
                        "暴露内部阶段或编造未释放事实。"
                        + (
                            "你不是心理咨询师，不建立私人关系，不推断隐藏心理。"
                            if humanistic
                            else ""
                        )
                        + "只输出 InterviewerOutput JSON。"
                    ),
                ),
                ChatMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
            ],
            temperature=0.2,
            max_tokens=700,
            json_mode=True,
            thinking_enabled=False,
            reasoning_effort="low",
            timeout_seconds=timeout_seconds,
        )
        response = asyncio.run(ModelGatewayService(settings).chat(request))
        return response.content, response.model

    @classmethod
    def _parse(
        cls,
        raw: str,
        *,
        plan: InterviewPlanOutput,
        unit: object | None,
    ) -> InterviewerOutput | None:
        try:
            payload = json.loads(raw.strip())
            if isinstance(payload, dict) and isinstance(
                payload.get("InterviewerOutput"), dict
            ):
                payload = payload["InterviewerOutput"]
            try:
                return InterviewerOutput.model_validate(payload)
            except Exception:  # noqa: BLE001
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("message"), str
                ):
                    return None
                message = payload["message"].strip()
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
                    reflection_turn_ids=(
                        []
                    ),
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
        return {
            "style_version": style_version,
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
                    "我可能没有理解准确，你愿意换一种说法说明刚才的意思吗？",
                    "刚才是我没承接好。你愿意从最确定的一点重新说说吗？",
                    "我们只看眼前这项任务，你愿意具体说说刚才的想法吗？",
                )
                if humanistic
                else (
                    "我可能没有问清楚。你能换一种说法说明刚才的意思吗？",
                    "我换个问法：你此刻最确定的判断是什么？",
                    "请结合眼前这项任务，具体说明你刚才的想法好吗？",
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
            (
                unit
                for unit in event.presentation_units
                if unit.unit_code == unit_code
            ),
            None,
        )

    @staticmethod
    def _grounded_reflection(
        context: AgentRuntimeContext,
        delivery_mode: str,
    ) -> str:
        latest = context.latest_user_turn
        text = latest.content if latest else ""
        if any(marker in text for marker in ("来源", "样本", "核实", "数据", "日志", "功能")):
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
    "HUMANISTIC_INTERVIEWER_PROMPT_VERSION",
    "HUMANISTIC_INTERVIEWER_STYLE",
    "INTERVIEWER_PROMPT_VERSION",
    "InterviewerAgent",
    "InterviewerAgentResult",
]
