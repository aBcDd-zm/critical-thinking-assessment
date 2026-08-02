from __future__ import annotations

from app.agents.dialogue_policy import (
    DialoguePolicy,
    PolicyDecision,
    _dynamic_trigger_matches,
    _unreleased_dynamic_infos,
)
from app.agents.question_contract import (
    count_stage_followups,
    load_contract,
    probe_coverage_mock,
    resolve_probe,
)
from app.agents.schemas import (
    AgentRuntimeContext,
    DynamicInfoContext,
    FollowupOutput,
    HostOutput,
    HumanisticFollowupSteps,
    InterventionRuleContext,
)
from app.agents.user_turn_intent import (
    analyze_user_turn,
    build_clarification_response,
    build_guidance_response,
    build_missing_evidence_question,
    build_redirect_response,
    build_stage_incomplete_prompt,
    build_term_explanation,
    classify_user_turn,
    missing_evidence,
)


LOW_INFORMATION_ANSWERS = {
    "",
    "无",
    "没有",
    "不知道",
    "不清楚",
    "随便",
    "没有想法",
    "暂无",
    "没想法",
    "none",
    "no",
    "nothing",
}


STAGE_FALLBACK_QUESTIONS: dict[str, str] = {
    "s1_problem_definition": "如果现在由你负责，你会先把哪件事定下来？",
    "s2_evidence_verification": "要判断同步失败严不严重，你第一步会查什么？",
    "s3_stakeholder_perspectives": "四方意见不一样时，你会先处理谁的担忧？",
    "s4_reasoning_decision": "现在由你拍板，你会怎么安排这次上线？",
    "s5_dynamic_adjustment": "看到这条新信息后，你会改变刚才的安排吗？",
    "s6_integrated_plan": "综合目前所有信息，你最终会如何安排此次上线呢？",
}


def is_low_information_answer(answer: str) -> bool:
    return classify_user_turn(answer) in {"low_information", "irrelevant"}


def build_stage_fallback_question(context: AgentRuntimeContext) -> str | None:
    latest = context.latest_user_turn
    if latest is None or not is_low_information_answer(latest.content):
        return None
    stage_code = context.stage.stage_code
    stage_question = STAGE_FALLBACK_QUESTIONS.get(stage_code)
    if stage_question is None:
        stage_question = "你能进一步说明你的判断依据吗？"
    return f"我们先从一个具体判断开始。{stage_question}"


class MockHostAgent:
    def generate(self, context: AgentRuntimeContext) -> HostOutput:
        nickname = context.participant.nickname or "受测者"
        stage = context.stage
        message = (
            f"{nickname}，我们进入“{stage.title}”。\n\n"
            f"{stage.context}\n\n"
            f"{stage.main_question}"
        )
        return HostOutput(
            stage_code=stage.stage_code,
            message=message,
            content_type="stage_question",
            generation_mode=stage.context_generation_mode,
            ai_generation_weight=stage.context_ai_weight,
            reason=f"mock mode generated stage question from {stage.stage_code}",
            next_action="wait_user_answer",
        )


class MockFollowupAgent:
    def __init__(self, policy: DialoguePolicy | None = None) -> None:
        self.policy = policy or DialoguePolicy()

    def generate(self, context: AgentRuntimeContext) -> FollowupOutput:
        latest = context.latest_user_turn
        analysis = (
            latest.analysis_json or analyze_user_turn(context, latest.content)
            if latest
            else {"intent": "irrelevant", "relevance": "not_applicable"}
        )
        intent = analysis.get("intent", "irrelevant")
        if intent == "clarification_request":
            question = build_clarification_response(context)
            return FollowupOutput(
                question=question,
                content_type="clarification_response",
                question_type="clarify",
                resolved_response_category="clarify_question",
                reason="participant requested the scenario or question to be restated",
                trigger_reason="latest_user_intent=clarification_request",
                reflection_summary="用户正在请求重新说明题面，尚未表达决策判断。",
                evidence_gap="需要先确保用户理解当前情境和问题。",
                next_action="ask_followup",
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=1.0,
                warnings=["clarification_request"],
            )
        if intent == "term_definition_request":
            return FollowupOutput(
                question=build_term_explanation(context, analysis.get("term")),
                content_type="term_explanation",
                question_type="clarify",
                resolved_response_category="explain_term",
                reason="participant requested a term definition",
                trigger_reason="latest_user_intent=term_definition_request",
                reflection_summary="用户需要先理解题目中的概念。",
                evidence_gap="当前输入不作为正式回答。",
                next_action="ask_followup",
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=1.0,
                warnings=["term_definition_request"],
            )
        if intent == "substantive_answer" and analysis.get("relevance") == "off_topic":
            return FollowupOutput(
                question=build_redirect_response(context),
                content_type="redirect_response",
                question_type="clarify",
                resolved_response_category="redirect",
                reason="answer is unrelated to the current stage",
                trigger_reason="latest_user_relevance=off_topic",
                reflection_summary="用户的回答没有对应当前问题。",
                evidence_gap="需要回到当前阶段的核心问题。",
                next_action="ask_followup",
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=0.9,
                warnings=["off_topic_answer"],
            )
        if intent in {"low_information", "irrelevant"}:
            question = build_guidance_response(context)
            return FollowupOutput(
                question=question,
                content_type="guidance_response",
                question_type="clarify",
                resolved_response_category="encourage_answer",
                reason=f"latest_user_intent={intent}; provided a non-scoring guidance response",
                trigger_reason=f"latest_user_intent={intent}",
                reflection_summary="用户尚未给出可用于测评的具体判断。",
                evidence_gap="需要用户先给出一个具体判断。",
                next_action="ask_followup",
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=0.95,
                warnings=[f"latest_user_intent={intent}"],
            )

        s1_single_focus = self._s1_single_focus_followup(context)
        if s1_single_focus is not None:
            return s1_single_focus

        decision = self.policy.decide(context)
        if decision.next_action == "advance_stage":
            return FollowupOutput(
                question="当前阶段的信息已经足够，我们进入下一个测评阶段。",
                content_type="advance_prompt",
                question_type="advance",
                resolved_response_category="assess_answer",
                reason="current stage followup limit reached",
                next_action="advance_stage",
                transition_reason=decision.transition_reason,
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=0.8,
                warnings=decision.warnings,
            )
        if decision.next_action == "finish_ready":
            exit_prompt = self._find_exit_prompt(context)
            return FollowupOutput(
                question=exit_prompt
                or "我已经记录你的最终方案，接下来将基于完整对话生成测评报告。",
                content_type="advance_prompt",
                question_type="advance",
                resolved_response_category="assess_answer",
                reason="final stage is ready for scoring and report generation",
                next_action="finish_ready",
                transition_reason=decision.transition_reason,
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=0.85,
                warnings=decision.warnings,
            )
        if decision.next_action == "ask_followup":
            if decision.waiting_for_stage_choice:
                return FollowupOutput(
                    question=build_stage_incomplete_prompt(list(decision.missing_evidence)),
                    content_type="stage_incomplete_prompt",
                    question_type="clarify",
                    resolved_response_category="assess_answer",
                    reason="formal followup soft limit reached with missing evidence",
                    evidence_gap="、".join(decision.missing_evidence),
                    next_action="ask_followup",
                    generation_mode="fixed_question",
                    ai_generation_weight=0,
                    confidence=1.0,
                    warnings=decision.warnings,
                )
            fallback_question = build_stage_fallback_question(context)
            if fallback_question is not None:
                return FollowupOutput(
                    question=fallback_question,
                    content_type="followup_question",
                    question_type="clarify",
                    resolved_response_category="encourage_answer",
                    reason="latest user answer is empty or too low-information; using stage-specific fallback",
                    trigger_reason="latest user answer is empty or too low-information",
                    reflection_summary="用户暂未给出可用于测评的具体判断。",
                    evidence_gap="需要用户先给出一个具体判断或依据。",
                    humanistic_steps=HumanisticFollowupSteps(
                        listening_acknowledgement="接住用户暂时没有展开的状态。",
                        reflective_clarification="当前还缺少具体判断。",
                        safety_prompt="保持中性、低压力，不在追问中重复开场理念。",
                        evidence_probe=fallback_question,
                    ),
                    next_action="ask_followup",
                    generation_mode="fixed_question",
                    ai_generation_weight=0,
                    confidence=0.9,
                    warnings=["low_information_answer"],
                )
            gaps = list(decision.missing_evidence) or missing_evidence(context)
            if context.session.language_mode == "plain" and gaps:
                question = build_missing_evidence_question(context.stage.stage_code, gaps[0])
                return FollowupOutput(
                    question=question,
                    content_type="followup_question",
                    question_type="open_followup",
                    resolved_response_category="assess_answer",
                    reason="plain language targeted evidence followup",
                    evidence_gap=gaps[0],
                    next_action="ask_followup",
                    generation_mode="fixed_question",
                    ai_generation_weight=0,
                    confidence=0.9,
                    warnings=decision.warnings,
                )
            return self._generate_followup(context, decision)

        return self._fallback(context, decision, "policy returned wait_user_answer")

    @staticmethod
    def _s1_single_focus_followup(
        context: AgentRuntimeContext,
    ) -> FollowupOutput | None:
        """Mock-side entry into the shared question-contract probe engine."""
        contract = load_contract(context.stage)
        updates = resolve_probe(
            contract,
            probe_coverage_mock(context),
            expected_evidence=list(
                context.stage.exit_criteria.get("expected_evidence") or []
            ),
            followups_used=count_stage_followups(context),
            max_followups=context.stage.max_followups,
        )
        if not updates:
            return None
        return FollowupOutput(
            question=str(updates.get("question") or ""),
            content_type="followup_question",
            question_type=str(updates.get("question_type") or "open_followup"),
            resolved_response_category="assess_answer",
            target_dimensions=list(updates.get("target_dimensions") or []),
            trigger_reason="V2.3 S1 mock single-focus constraint collection",
            reflection_summary="用户已经提出核心判断，继续逐项观察限制条件。",
            evidence_gap=str(updates.get("evidence_gap") or "") or None,
            reason="mock mode follows the V2.3 S1 fixed turn contract",
            next_action="ask_followup",
            generation_mode="fixed_question",
            ai_generation_weight=0,
            confidence=1.0,
        )

    def _generate_followup(
        self,
        context: AgentRuntimeContext,
        decision: PolicyDecision,
    ) -> FollowupOutput:
        warnings = list(decision.warnings)
        if decision.should_release_dynamic_info:
            info = self._select_dynamic_info(context)
            if info:
                return self._dynamic_info_question(context, info, warnings)
            warnings.append("dynamic info was requested but no unreleased item exists")

        if decision.selected_rule:
            return self._rule_question(decision.selected_rule, warnings)

        return self._fallback(context, decision, "no rule selected")

    @staticmethod
    def _rule_question(
        rule: InterventionRuleContext,
        warnings: list[str],
    ) -> FollowupOutput:
        base_question = rule.sample_question or rule.fallback_question
        fallback_used = False
        if not base_question:
            base_question = f"可以围绕这个方向再展开说说吗：{rule.strategy_direction}"
            fallback_used = True
            warnings.append(f"rule {rule.rule_code} has no sample or fallback question")
        question = _humanize_followup_question(base_question)

        return FollowupOutput(
            question=question,
            content_type="followup_question",
            question_type=rule.rule_type,
            resolved_response_category="assess_answer",
            selected_rule_code=rule.rule_code,
            target_dimensions=rule.target_dimensions,
            trigger_reason=rule.trigger_condition,
            reflection_summary="用户已经给出初步判断，但仍需要补充更具体的理由或证据。",
            evidence_gap=rule.strategy_direction,
            humanistic_steps=HumanisticFollowupSteps(
                listening_acknowledgement="接住用户已有的判断方向。",
                reflective_clarification="当前回答还需要进一步展开判断依据。",
                safety_prompt="保持中性、低压力，不在追问中重复开场理念。",
                evidence_probe=base_question,
            ),
            generation_mode=rule.question_generation_mode,
            ai_generation_weight=rule.question_ai_weight,
            reason=f"selected intervention rule {rule.rule_code}",
            next_action="ask_followup",
            confidence=0.75 if not fallback_used else 0.5,
            fallback_used=fallback_used,
            warnings=warnings,
        )

    def _dynamic_info_question(
        self,
        context: AgentRuntimeContext,
        info: DynamicInfoContext,
        warnings: list[str],
    ) -> FollowupOutput:
        dynamic_rule = next(
            (
                rule
                for rule in context.candidate_intervention_rules
                if rule.rule_type == "dynamic_update"
            ),
            None,
        )
        question = (
            "这批反馈可能没有覆盖足够多的低端设备和弱网用户。你会先补查哪类数据？"
            if info.info_code == "sample_bias_warning"
            else "这条新信息可能影响原来的判断。你会先调整哪一部分？"
        )
        return FollowupOutput(
            question=question,
            content_type="dynamic_info_question",
            question_type="dynamic_update",
            resolved_response_category="assess_answer",
            selected_rule_code=dynamic_rule.rule_code if dynamic_rule else None,
            selected_dynamic_info_code=info.info_code,
            released_dynamic_info_text=info.content,
            target_dimensions=info.target_dimensions,
            trigger_reason=info.trigger_condition,
            reflection_summary="用户前序判断需要在新信息下继续检验。",
            evidence_gap="需要观察用户如何根据新证据更新判断。",
            humanistic_steps=HumanisticFollowupSteps(
                listening_acknowledgement="承接用户前序判断。",
                reflective_clarification="新信息可能影响原有判断。",
                safety_prompt="不要求立刻改变答案，重点是观察处理新证据的过程。",
                evidence_probe="基于这条信息，你会先核实什么，再决定是否调整原方案？",
            ),
            generation_mode=(
                dynamic_rule.question_generation_mode if dynamic_rule else "template_guided"
            ),
            ai_generation_weight=(
                dynamic_rule.question_ai_weight if dynamic_rule else 35
            ),
            reason=f"released dynamic info {info.info_code}",
            next_action="ask_followup",
            confidence=0.7,
            warnings=warnings,
        )

    @staticmethod
    def _fallback(
        context: AgentRuntimeContext,
        decision: PolicyDecision,
        reason: str,
    ) -> FollowupOutput:
        fallback_question = next(
            (
                rule.fallback_question
                for rule in context.candidate_intervention_rules
                if rule.fallback_question
            ),
            None,
        )
        return FollowupOutput(
            question=_humanize_followup_question(
                fallback_question or "你这样判断，最主要的理由是什么？"
            ),
            content_type="followup_question",
            question_type="clarify",
            resolved_response_category="assess_answer",
            reason=reason,
            trigger_reason=reason,
            reflection_summary="用户回答还需要补充可评分的依据。",
            evidence_gap="需要补充判断背后的主要依据。",
            humanistic_steps=HumanisticFollowupSteps(
                listening_acknowledgement="接住用户已有回答。",
                reflective_clarification="当前回答还缺少明确依据。",
                safety_prompt="保持中性、低压力，不在追问中重复开场理念。",
                evidence_probe=fallback_question
                or "你能进一步说明这个判断背后的主要依据吗？",
            ),
            next_action="ask_followup",
            generation_mode="fixed_question",
            ai_generation_weight=0,
            confidence=0.5,
            fallback_used=True,
            warnings=decision.warnings,
        )

    @staticmethod
    def _select_dynamic_info(context: AgentRuntimeContext) -> DynamicInfoContext | None:
        candidates = _unreleased_dynamic_infos(context)
        if not candidates:
            return None

        gap = context.score_gap_summary
        missing_dimensions = set(gap.missing_dimensions) if gap else set()
        if missing_dimensions:
            for info in sorted(candidates, key=lambda item: item.priority):
                if any(dimension in missing_dimensions for dimension in info.target_dimensions):
                    return info

        latest_content = context.latest_user_turn.content if context.latest_user_turn else ""
        matching = [
            info
            for info in candidates
            if _dynamic_trigger_matches(info.trigger_condition or "", latest_content)
        ]
        if matching:
            return sorted(matching, key=lambda item: item.priority)[0]

        return sorted(candidates, key=lambda item: item.priority)[0]

    @staticmethod
    def _find_exit_prompt(context: AgentRuntimeContext) -> str | None:
        return next(
            (
                rule.exit_prompt
                for rule in context.candidate_intervention_rules
                if rule.rule_type == "advance" and rule.exit_prompt
            ),
            None,
        )


def _humanize_followup_question(question: str) -> str:
    leads = (
        "明白了。那我再问一个具体点：",
        "顺着你的想法，我再确认一点：",
        "我听懂你的方向了。再往下看一步：",
        "好，我们只看下一点：",
    )
    checksum = sum(ord(character) for character in question)
    return f"{leads[checksum % len(leads)]}{question}"


__all__ = ["MockFollowupAgent", "MockHostAgent"]
