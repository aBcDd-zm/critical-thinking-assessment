from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from app.agents.interview_blueprint import GeneratedScenarioBlueprint
from app.agents.humanistic_interviewer_v11 import V11_OUTPUT_MARKER
from app.agents.interview_planner_agent import InterviewPlannerAgent
from app.agents.interview_question_validator import InterviewQuestionValidator
from app.agents.runtime_interviewer_agent import (
    INTERVIEWER_DISPLAY_NAME,
    InterviewerAgent,
)
from app.agents.progressive_schemas import (
    ConsultativeTurnOutput,
    InterviewPlanOutput,
    InterviewQualityFlags,
    InterviewerOutput,
    InterviewState,
    ReflectionSourceQuote,
)
from app.agents.schemas import AgentRuntimeContext
from app.agents.user_turn_intent import (
    analyze_user_turn,
    build_clarification_response,
    build_term_explanation,
    classify_consultative_control_intent,
    classify_user_turn,
)

CONSULTATIVE_TURN_PROMPT_VERSION = "consultative_turn_v3_3"
CONSULTATIVE_TURN_PROMPT_VARIANT = "deterministic_measurement_core_v1"


@dataclass
class ConsultativeTurnAgentResult:
    output: ConsultativeTurnOutput
    raw_output: str | None
    model_name: str | None
    duration_ms: int
    status: str = "ok"
    error_code: str | None = None
    fallback_type: str | None = None
    validation_errors: list[str] | None = None
    model_attempt_count: int = 0
    retry_reason: str | None = None


class ConsultativeTurnAgent:
    """One-call analyzer, planner and visible-message renderer for v3.2."""

    def route_repair(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
        *,
        include_low_information: bool = False,
    ) -> ConsultativeTurnAgentResult | None:
        """Handle context questions and conversation repair without an LLM call."""
        latest = context.latest_user_turn
        text = latest.content.strip() if latest else ""
        intent = self._repair_intent(
            text,
            include_low_information=include_low_information,
        )
        if intent is None:
            return None
        started = perf_counter()
        turn_analysis = (
            analyze_user_turn(context, text)
            if intent in {"clarify_question", "explain_term"}
            else {}
        )
        prior_plan = state.last_plan or {}
        plan_topic = prior_plan.get("active_topic")
        plan_target = prior_plan.get("target_dimension")
        plan_evidence = prior_plan.get("target_evidence")
        if intent == "low_information":
            plan_target = None
            plan_evidence = None
        repair_question_intent = ""
        repair_question = ""
        if intent == "conversation_repair":
            (
                repair_target,
                repair_question_intent,
                repair_question,
            ) = self._repair_followup(state)
            plan_target = repair_target
            plan_evidence = "从未重复的观察角度补充一项可判断信息"

        base_plan = InterviewPlannerAgent().build_deterministic_plan(
            context,
            state,
            blueprint,
        )
        plan = base_plan.model_copy(
            update={
                "response_intent": (
                    "redirect" if intent == "boundary_redirect" else intent
                ),
                "action": "CLARIFY",
                "active_topic": plan_topic or base_plan.active_topic,
                "delivery_mode": "clarification",
                "target_dimension": plan_target,
                "target_evidence": plan_evidence,
                "release_event_code": None,
                "release_unit_code": None,
                "question_intent": {
                    "request_context": "直接补全用户询问的情境信息",
                    "clarify_question": "澄清并重述当前问题",
                    "explain_term": "解释用户询问的术语后重述当前问题",
                    "low_information": "降低回答负担并提出一个具体小问题",
                    "boundary_redirect": "说明非临床角色边界并回到测评任务",
                }.get(intent, repair_question_intent),
                "evidence_observations": [],
                "memory_update": state.memory.model_copy(deep=True),
                "reason": "v3.3 deterministic context and repair router",
            }
        )
        if intent == "request_context":
            message = self._context_message(text, blueprint, state, context)
            sources: list[ReflectionSourceQuote] = []
            source_ids: list[int] = []
        elif intent == "clarify_question":
            message = build_clarification_response(context)
            sources = []
            source_ids = []
        elif intent == "explain_term":
            message = build_term_explanation(
                context,
                turn_analysis.get("term"),
            )
            sources = []
            source_ids = []
        elif intent == "low_information":
            message = "可以先不下结论。眼前这件事里，你想先弄清哪一点？"
            sources = []
            source_ids = []
        elif intent == "boundary_redirect":
            previous_questions = [
                item.content
                for item in context.dialogue_history[:-1]
                if item.speaker == "ai" and "?" in item.content.replace("？", "?")
            ]
            question = InterviewerAgent.select_probe_message(
                plan_target,
                previous_questions=previous_questions,
            )
            message = (
                f"我是{INTERVIEWER_DISPLAY_NAME}，负责本次测评对话；"
                f"我不替代亲友或心理咨询师，也不会替你做决定。{question}"
            )
            sources = []
            source_ids = []
        else:
            prior = next(
                (
                    item
                    for item in reversed(context.dialogue_history[:-1])
                    if item.speaker == "user"
                    and (item.analysis_json or {}).get("formal_answer") is True
                    and item.turn_id is not None
                ),
                None,
            )
            if prior:
                source_quote = prior.content.strip().rstrip("。！？!?")
                display_quote = self._repair_display_quote(source_quote)
                if display_quote:
                    message = (
                        f"你之前已经提出“{display_quote}”，我不会再重复问这一点。" f"{repair_question}"
                    )
                else:
                    message = "你刚才已经说明了自己的判断和下一步，我不会再重复问这一点。" f"{repair_question}"
                sources = [
                    ReflectionSourceQuote(turn_id=prior.turn_id, quote=source_quote)
                ]
                source_ids = [prior.turn_id]
            else:
                message = "抱歉，刚才的问题没有承接好，我换一个更具体的问法。" f"{repair_question}"
                sources = []
                source_ids = []
        output = ConsultativeTurnOutput(
            plan=plan.model_copy(update={"reflection_basis_turn_ids": source_ids}),
            interviewer=InterviewerOutput(
                message=message,
                message_type="clarification",
                question_count=1,
                reflection_turn_ids=source_ids,
                reflection_source_quotes=sources,
                quality_flags=self._quality_flags(),
            ),
        )
        return ConsultativeTurnAgentResult(
            output=output,
            raw_output=output.model_dump_json(),
            model_name="deterministic-v3.3-router",
            duration_ms=int((perf_counter() - started) * 1000),
            model_attempt_count=0,
        )

    @staticmethod
    def _repair_intent(
        text: str,
        *,
        include_low_information: bool = False,
    ) -> str | None:
        intent = classify_consultative_control_intent(text)
        if intent is not None:
            return intent
        if include_low_information and classify_user_turn(text) == "low_information":
            return "low_information"
        return None

    @staticmethod
    def _repair_display_quote(source_quote: str) -> str | None:
        """Return a complete source clause that fits the visible repair message."""
        if len(source_quote) <= 45:
            return source_quote

        clipped = source_quote[:45]
        boundary = max(clipped.rfind(marker) for marker in "，；、：")
        if boundary >= 8:
            return clipped[:boundary]
        return None

    @staticmethod
    def _repair_followup(
        state: InterviewState,
    ) -> tuple[str, str, str]:
        """Choose a new assessable question after the user reports repetition."""
        followups = {
            "problem_definition": ("先不谈具体步骤，你认为眼下最需要判断的核心问题是什么？"),
            "evidence_evaluation": ("为了减少不确定性，你还需要核实哪一条信息？"),
            "reasoning_argumentation": ("哪条依据最能支持你现在的选择，为什么？"),
            "multiple_perspectives": ("从其他参与者的角度看，他们最担心什么后果？"),
            "integrative_decision": ("如果现在必须落地，你会先保留哪一步、调整哪一步？"),
            "dynamic_adjustment": ("出现什么新信息时，你会改变当前安排？"),
        }
        status_priority = {
            "not_started": 0,
            "partial": 1,
            "blocked": 2,
            "sufficient": 3,
        }
        prior_target = (state.last_plan or {}).get("target_dimension")
        used_targets: set[str] = set()

        for key in state.asked_intent_keys:
            parts = key.split("|", 3)
            if (
                len(parts) == 4
                and parts[0] == "CLARIFY"
                and parts[2].startswith("承接纠错，改从")
            ):
                used_targets.add(parts[1])

        candidates = [
            dimension
            for dimension, slot in state.dimension_slots.items()
            if slot.status != "not_available" and dimension in followups
        ]
        if not candidates:
            target = "integrative_decision"
        else:
            candidates.sort(
                key=lambda dimension: (
                    dimension in used_targets,
                    dimension == prior_target,
                    int(state.dimension_opportunity_counts.get(dimension, 0) or 0),
                    status_priority.get(
                        state.dimension_slots[dimension].status,
                        9,
                    ),
                )
            )
            target = candidates[0]

        question_intent = f"承接纠错，改从{target}角度提出未重复问题"
        return target, question_intent, followups[target]

    @staticmethod
    def _context_message(
        text: str,
        blueprint: GeneratedScenarioBlueprint,
        state: InterviewState,
        context: AgentRuntimeContext,
    ) -> str:
        """Recap only facts that have already been released to the participant."""

        asks_arrangements = any(
            marker in text for marker in ("新安排", "原安排", "新旧")
        )
        if (
            asks_arrangements
            and blueprint.new_arrangement
            and blueprint.current_arrangement
        ):
            # The v3.3 arrangement envelope is static scenario background that a
            # participant may explicitly ask to review.  Dynamic event cards
            # (stakeholder positions, trial outcomes, counter-evidence) remain
            # protected by released_unit_codes below.
            new_arrangement = (
                blueprint.new_arrangement.split("，", 1)[0]
                .rstrip("。！？!?")
                .replace("检查步骤", "检查")
            )
            current_arrangement = blueprint.current_arrangement.split(
                "，", 1
            )[0].rstrip("。！？!?")
            return (
                "这是题目里已给出的方案背景："
                f"{new_arrangement}，{current_arrangement}。"
                "你想先比较进度收益还是质量风险？"
            )

        released_codes = set(state.released_unit_codes)
        released_facts = [
            unit.text.rstrip("。！？!?")
            for event in blueprint.event_cards
            for unit in event.presentation_units
            if unit.unit_code in released_codes
        ]
        if not released_facts and blueprint.event_cards:
            # The opening is visible before the first formal answer.  Imported test
            # states may not yet contain its unit code, so use only this already
            # visible public premise as the conservative fallback.
            released_facts = [
                blueprint.event_cards[0].presentation_units[0].text.rstrip(
                    "。！？!?"
                )
            ]
        recap = "；".join(released_facts[-3:])
        previous_user = next(
            (
                item.content
                for item in reversed(context.dialogue_history[:-1])
                if item.speaker == "user" and item.content.strip()
            ),
            "",
        )
        focus = f"{previous_user}{text}"
        asks_people_detail = any(
            marker in focus
            for marker in (
                "分工",
                "负责什么",
                "谁的任务",
                "每个人",
                "所有人",
                "组员",
                "参与者",
            )
        )
        requested_hidden_topic = None
        if asks_arrangements:
            requested_hidden_topic = "具体的新旧安排"
        elif any(marker in text for marker in ("诉求", "两方", "两个")):
            requested_hidden_topic = "各方的具体顾虑"
        elif asks_people_detail:
            requested_hidden_topic = "每个人的具体分工和任务记录"

        unavailable = ""
        followup = "这些信息里，你还想了解哪类背景？"
        if requested_hidden_topic:
            unavailable = f"；目前已给信息还没有列出{requested_hidden_topic}"
            if asks_people_detail:
                followup = "现有信息还不足以列出分工，你会先查哪类任务记录？"
            elif requested_hidden_topic == "具体的新旧安排":
                followup = "具体安排还没有给出，你想先了解哪一种方案？"
            else:
                followup = "各方的具体顾虑还没有给出，你想先了解哪一方？"
        return (
            f"目前已经知道的是：{recap}{unavailable}。{followup}"
        )

    def generate(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
        *,
        opening: bool = False,
        nickname: str = "你",
    ) -> ConsultativeTurnAgentResult:
        """Build the auditable measurement core without a network dependency.

        The language model is deliberately limited to the independent
        InterviewerAgent renderer.  Intent, evidence extraction, event release,
        state transition and the deterministic safety message therefore remain
        available even when the renderer transport fails.
        """
        started = perf_counter()
        output = self.fallback(
            context,
            state,
            blueprint,
            opening=opening,
            nickname=nickname,
        )
        return ConsultativeTurnAgentResult(
            output=output,
            raw_output=output.model_dump_json(),
            model_name=(
                "deterministic-opening-plan-v1"
                if opening
                else "deterministic-measurement-core-v1"
            ),
            duration_ms=int((perf_counter() - started) * 1000),
            model_attempt_count=0,
        )

    def fallback(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
        *,
        opening: bool = False,
        nickname: str = "你",
        plan: InterviewPlanOutput | None = None,
    ) -> ConsultativeTurnOutput:
        renderer = InterviewerAgent()
        if opening:
            unit = blueprint.event_cards[0].presentation_units[0]
            question = (
                "你会先确认哪件事？"
                if blueprint.schema_version == "occupation_interview_skeleton_v3_3"
                else "你最想先理清哪一点？"
            )
            message = f"{nickname}，{unit.text}{question}"
            return ConsultativeTurnOutput(
                plan=None,
                interviewer=InterviewerOutput(
                    message=message,
                    message_type="opening",
                    question_count=1,
                    introduced_fact_codes=[unit.unit_code],
                    quality_flags=self._quality_flags(),
                ),
            )
        selected_plan = plan or InterviewPlannerAgent().build_deterministic_plan(
            context,
            state,
            blueprint,
        )
        return ConsultativeTurnOutput(
            plan=selected_plan,
            interviewer=renderer._fallback(
                selected_plan, blueprint, context
            ),  # noqa: SLF001
        )

    def rerender_after_plan_enforcement(
        self,
        context: AgentRuntimeContext,
        state: InterviewState,
        blueprint: GeneratedScenarioBlueprint,
        plan: InterviewPlanOutput,
        *,
        fallback_used: bool,
    ) -> InterviewerOutput:
        """Render the visible message again after deterministic plan enforcement."""
        rendered = self.fallback(
            context,
            state,
            blueprint,
            plan=plan,
        ).interviewer
        return rendered.model_copy(
            update={
                "fallback_used": fallback_used,
                "warnings": [
                    *rendered.warnings,
                    "planner action enforced; deterministic rerender applied",
                ],
            }
        )

    def validate_opening(
        self,
        output: InterviewerOutput,
        blueprint: GeneratedScenarioBlueprint,
        *,
        participant_nickname: str | None = None,
        enforce_humanistic_safety: bool = False,
    ) -> list[str]:
        message = output.message.strip()
        authored_message = self._opening_assistant_authored_text(
            message,
            participant_nickname,
        )
        unit = blueprint.event_cards[0].presentation_units[0]
        errors = InterviewQuestionValidator.message_errors(
            authored_message,
            enforce_humanistic_safety=enforce_humanistic_safety,
        )
        if len(message) > 90:
            errors.append("too_long")
        if authored_message.count("？") + authored_message.count("?") != 1:
            errors.append("question_count")
        if not InterviewQuestionValidator.fact_is_supported(
            authored_message,
            unit.text,
        ):
            errors.append("missing_selected_fact")
        if output.introduced_fact_codes != [unit.unit_code]:
            errors.append("fact_code")
        errors.extend(self._identity_errors(authored_message, blueprint))
        return list(dict.fromkeys(errors))

    @staticmethod
    def _opening_assistant_authored_text(
        message: str,
        participant_nickname: str | None,
    ) -> str:
        """Exclude only the exact user-supplied nickname prefix from speaker checks."""
        if not participant_nickname:
            return message
        prefix = f"{participant_nickname}，"
        if not message.startswith(prefix):
            return message
        return message[len(prefix) :].lstrip()

    def validate_turn(
        self,
        output: InterviewerOutput,
        *,
        plan: InterviewPlanOutput,
        blueprint: GeneratedScenarioBlueprint,
        context: AgentRuntimeContext,
        previous_questions: list[str],
        state: InterviewState | None = None,
        enforce_humanistic_safety: bool = False,
    ) -> list[str]:
        event = next(
            (
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            ),
            None,
        )
        unit = InterviewerAgent._selected_unit(
            event, plan.release_unit_code
        )  # noqa: SLF001
        valid, errors = InterviewQuestionValidator().validate(
            output,
            plan=plan,
            allowed_fact_codes={plan.release_unit_code}
            if plan.release_unit_code
            else set(),
            previous_questions=previous_questions,
            allowed_source_turn_ids=set(plan.reflection_basis_turn_ids),
            source_turn_texts={
                item.turn_id: item.content
                for item in context.dialogue_history
                if item.turn_id is not None
            },
            allowed_fact_text=unit.text if unit else None,
            enforce_humanistic_safety=enforce_humanistic_safety,
        )
        del valid
        if (
            V11_OUTPUT_MARKER in output.warnings
            and "reflection_omitted_for_length" in output.warnings
        ):
            errors = [item for item in errors if item != "missing_reflection"]
        if getattr(plan, "response_intent", None) == "request_context":
            # A context recap is a user-requested, non-scoring control turn. Its
            # closing invitation may be semantically close to an earlier prompt;
            # replacing the whole recap with an anti-repeat probe would lose the
            # information the user explicitly asked for.
            errors = [
                item
                for item in errors
                if item not in {"duplicate_question", "semantic_duplicate_question"}
            ]
        errors.extend(self._identity_errors(output.message, blueprint))
        if state is not None:
            allowed_units = set(state.released_unit_codes)
            if plan.release_unit_code:
                allowed_units.add(plan.release_unit_code)
            for candidate_event in blueprint.event_cards:
                for candidate in candidate_event.presentation_units:
                    fact = candidate.text.rstrip("。！？!?")
                    if (
                        candidate.unit_code not in allowed_units
                        and fact
                        and fact in output.message
                    ):
                        errors.append(f"unreleased_fact_text:{candidate.unit_code}")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _identity_errors(
        message: str, blueprint: GeneratedScenarioBlueprint
    ) -> list[str]:
        constraints = blueprint.identity_constraints
        if constraints is None:
            return []
        return [
            f"forbidden_inferred_role:{role}"
            for role in constraints.forbidden_inferred_roles
            if role in message
        ]

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
    "CONSULTATIVE_TURN_PROMPT_VERSION",
    "ConsultativeTurnAgent",
    "ConsultativeTurnAgentResult",
]
