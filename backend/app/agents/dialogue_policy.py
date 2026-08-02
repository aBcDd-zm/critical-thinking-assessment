from __future__ import annotations

from dataclasses import dataclass

from app.agents.schemas import (
    AgentRuntimeContext,
    DynamicInfoContext,
    InterventionRuleContext,
    NextAction,
    StageTransitionReason,
)
from app.agents.user_turn_intent import (
    analyze_user_turn,
    classify_user_turn,
    evidence_coverage,
    has_substantive_stage_answer,
    is_scoring_analysis,
    missing_evidence,
)


# Heuristic threshold reused in mock_dialogue.py to keep behavior consistent.
LOW_INFORMATION_ANSWER_THRESHOLD = 40


FOLLOWUP_CONTENT_TYPES = {"followup_question", "dynamic_info_question"}
ADVANCE_RULE_TYPE = "advance"


@dataclass(frozen=True)
class PolicyDecision:
    next_action: NextAction
    warnings: list[str]
    selected_rule: InterventionRuleContext | None = None
    should_release_dynamic_info: bool = False
    missing_evidence: tuple[str, ...] = ()
    waiting_for_stage_choice: bool = False
    transition_reason: StageTransitionReason | None = None


class DialoguePolicy:
    def decide(self, context: AgentRuntimeContext) -> PolicyDecision:
        if context.latest_user_turn is None:
            return PolicyDecision(
                next_action="wait_user_answer",
                warnings=["latest_user_turn is missing"],
            )

        latest_analysis = context.latest_user_turn.analysis_json or analyze_user_turn(
            context, context.latest_user_turn.content
        )
        latest_intent = latest_analysis.get("intent")
        if not is_scoring_analysis(latest_analysis):
            return PolicyDecision(
                next_action="ask_followup",
                warnings=[
                    f"latest_user_intent={latest_intent}",
                    f"latest_user_relevance={latest_analysis.get('relevance')}",
                ],
            )

        followups_used = self._count_current_stage_followups(context)
        gaps = missing_evidence(context)
        criteria_configured = bool(
            context.stage.exit_criteria.get("expected_evidence")
        )
        min_user_turns = int(context.stage.exit_criteria.get("min_user_turns") or 1)
        relevant_answers = sum(
            1
            for turn in context.dialogue_history
            if turn.speaker == "user"
            and turn.stage_code == context.stage.stage_code
            and is_scoring_analysis(
                turn.analysis_json or analyze_user_turn(context, turn.content)
            )
        )
        if criteria_configured and not gaps and relevant_answers >= min_user_turns:
            return PolicyDecision(
                next_action="finish_ready" if self._has_exit_rule(context) else "advance_stage",
                warnings=[f"evidence_coverage_complete={evidence_coverage(context)}"],
                transition_reason="evidence_complete",
            )

        if (
            followups_used >= context.stage.max_followups
            and relevant_answers >= min_user_turns
        ):
            warnings = [
                f"followups_used={followups_used} reached max_followups={context.stage.max_followups}",
                f"missing_evidence={gaps}",
            ]
            return PolicyDecision(
                next_action="finish_ready" if self._has_exit_rule(context) else "advance_stage",
                warnings=warnings
                + [
                    "stage progression is bounded by the formal followup limit; "
                    "evidence gaps remain available for scoring and review"
                ],
                missing_evidence=tuple(gaps),
                transition_reason="followup_limit_reached",
            )

        if (
            followups_used >= context.stage.max_followups
            and not criteria_configured
            and has_substantive_stage_answer(context)
        ):
            return PolicyDecision(
                next_action="finish_ready" if self._has_exit_rule(context) else "advance_stage",
                warnings=[
                    f"followups_used={followups_used} reached max_followups={context.stage.max_followups}",
                    "legacy stage context without evidence criteria",
                ],
                transition_reason="followup_limit_reached",
            )

        rules = [
            rule
            for rule in context.candidate_intervention_rules
            if rule.rule_type != ADVANCE_RULE_TYPE
        ]
        should_release_dynamic_info = self._should_release_dynamic_info(context)
        if rules or should_release_dynamic_info:
            return PolicyDecision(
                next_action="ask_followup",
                warnings=[],
                selected_rule=self._select_rule(context, rules),
                should_release_dynamic_info=should_release_dynamic_info,
            )

        return PolicyDecision(
            next_action="ask_followup",
            warnings=["evidence remains incomplete; using targeted fallback"],
            missing_evidence=tuple(gaps),
        )

    @staticmethod
    def _count_current_stage_followups(context: AgentRuntimeContext) -> int:
        return sum(
            1
            for turn in context.dialogue_history
            if turn.speaker == "ai"
            and turn.stage_code == context.stage.stage_code
            and turn.content_type in FOLLOWUP_CONTENT_TYPES
        )

    @staticmethod
    def _current_stage_low_information_turns(
        context: AgentRuntimeContext,
    ) -> list[str]:
        return [
            turn.content
            for turn in context.dialogue_history
            if turn.speaker == "user"
            and turn.stage_code == context.stage.stage_code
            and classify_user_turn(turn.content) != "substantive_answer"
        ]

    @staticmethod
    def _has_exit_rule(context: AgentRuntimeContext) -> bool:
        return any(
            rule.rule_type == ADVANCE_RULE_TYPE and bool(rule.exit_prompt)
            for rule in context.candidate_intervention_rules
        )

    @staticmethod
    def _should_release_dynamic_info(context: AgentRuntimeContext) -> bool:
        if not context.candidate_dynamic_infos:
            return False

        unreleased = _unreleased_dynamic_infos(context)
        if not unreleased:
            return False

        gap = context.score_gap_summary
        if gap and (gap.missing_dimensions or gap.argument_issues):
            return True

        latest = context.latest_user_turn
        if latest is None or classify_user_turn(latest.content) != "substantive_answer":
            return False
        return any(
            _dynamic_trigger_matches(info.trigger_condition or "", latest.content)
            for info in unreleased
        )

    @staticmethod
    def _select_rule(
        context: AgentRuntimeContext,
        rules: list[InterventionRuleContext],
    ) -> InterventionRuleContext | None:
        if not rules:
            return None

        gap = context.score_gap_summary
        missing_dimensions = set(gap.missing_dimensions) if gap else set()
        if missing_dimensions:
            for rule in sorted(rules, key=lambda item: item.priority):
                if any(dimension in missing_dimensions for dimension in rule.target_dimensions):
                    return rule

        latest_content = context.latest_user_turn.content if context.latest_user_turn else ""
        for rule in sorted(rules, key=lambda item: item.priority):
            if rule.trigger_condition and _trigger_matches(
                rule.trigger_condition,
                latest_content,
            ):
                return rule

        return sorted(rules, key=lambda item: item.priority)[0]


def _trigger_matches(trigger_condition: str, user_content: str) -> bool:
    if not trigger_condition or not user_content:
        return False

    keywords: list[str] = []
    current: list[str] = []
    for char in trigger_condition:
        if "\u4e00" <= char <= "\u9fff":
            current.append(char)
            continue
        if len(current) >= 2:
            keywords.append("".join(current))
        current = []
    if len(current) >= 2:
        keywords.append("".join(current))

    return any(keyword in user_content for keyword in dict.fromkeys(keywords))


def _dynamic_trigger_matches(trigger_condition: str, user_content: str) -> bool:
    if not trigger_condition or not user_content:
        return False
    if "样本" in trigger_condition:
        mentions_feedback_quantity = any(
            marker in user_content
            for marker in (
                "反馈",
                "数量",
                "19",
                "86",
                "很多",
                "严重",
                "不多",
                "不严重",
            )
        )
        considers_representation = any(
            marker in user_content for marker in ("样本", "代表", "机型", "弱网", "网络环境")
        )
        return mentions_feedback_quantity and not considers_representation
    if "按时上线" in trigger_condition or "上线" in trigger_condition:
        return "上线" in user_content and not any(
            marker in user_content for marker in ("风险", "验证", "回滚", "灰度", "延期")
        )
    return _trigger_matches(trigger_condition, user_content)


def _released_dynamic_info_identifiers(
    context: AgentRuntimeContext,
) -> tuple[set[int], set[str]]:
    released_ids: set[int] = set()
    released_codes: set[str] = set()
    for turn in context.dialogue_history:
        if turn.speaker != "ai":
            continue
        if turn.dynamic_info_id is not None:
            released_ids.add(turn.dynamic_info_id)
        if turn.selected_dynamic_info_code:
            released_codes.add(turn.selected_dynamic_info_code)
        if turn.content_type in {"dynamic_info", "dynamic_info_question"}:
            for info in context.candidate_dynamic_infos:
                if info.content in turn.content or info.title in turn.content:
                    if info.dynamic_info_id is not None:
                        released_ids.add(info.dynamic_info_id)
                    released_codes.add(info.info_code)
    return released_ids, released_codes


def _unreleased_dynamic_infos(
    context: AgentRuntimeContext,
) -> list[DynamicInfoContext]:
    released_ids, released_codes = _released_dynamic_info_identifiers(context)
    return [
        info
        for info in context.candidate_dynamic_infos
        if info.dynamic_info_id not in released_ids
        and info.info_code not in released_codes
    ]


__all__ = ["DialoguePolicy", "PolicyDecision", "_unreleased_dynamic_infos"]
