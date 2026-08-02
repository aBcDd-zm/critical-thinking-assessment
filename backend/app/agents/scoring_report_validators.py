from __future__ import annotations

import re
from difflib import get_close_matches

from app.agents.schemas import AgentRuntimeContext, ReportOutput, ScoringOutput
from app.agents.user_turn_intent import classify_user_turn, is_scoring_analysis


ALLOWED_EVIDENCE_TYPES = {
    "supporting_evidence",
    "weak_evidence",
    "invalid_evidence",
}

PROVISIONAL_REPORT_STRENGTH = (
    "本次对话已形成与该维度相关、可追溯的部分证据，"
    "但尚未达到支持能力评分的充分性门槛。"
)
PROVISIONAL_REPORT_SUGGESTION = (
    "后续可围绕尚缺的关键行为或关系补充可核实信息，"
    "以判断该维度是否达到评分所需的证据门槛。"
)
UNOBSERVED_REPORT_STRENGTH = (
    "本次对话未获得该维度的公平作答机会，"
    "现有证据不足以判断该维度表现。"
)
UNOBSERVED_REPORT_SUGGESTION = (
    "如需继续评估，可在后续相似情境中提供"
    "针对该维度的公平作答机会并继续观察。"
)

OBSERVED_BEHAVIOR_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "quantitative_threshold": (
        re.compile(r"\d+(?:\.\d+)?\s*%"),
        re.compile(r"(?:阈值|门槛|成功率|通过率)"),
    ),
    "monitoring": (
        re.compile(r"(?:监控|监测|跟踪|持续观察|继续观察)"),
        re.compile(r"(?:失败率|成功率|通过率).{0,12}(?:观察|检查|记录)"),
    ),
    "repeated_validation": (
        re.compile(r"(?:连续|重复|再次|重新)?(?:测试|复测|验证).{0,8}(?:轮|次)"),
        re.compile(r"(?:两轮|三轮|多轮).{0,8}(?:测试|复测|验证)"),
    ),
    "scope_control": (
        re.compile(r"(?:保持|先用|继续使用).{0,8}小范围"),
        re.compile(r"(?:暂停|停止|暂不|不再|不会立刻).{0,8}(?:扩大|上线|发布)"),
        re.compile(r"(?:缩小|逐步扩大|扩大).{0,8}范围"),
    ),
    "rollback": (
        re.compile(r"(?:回退|回滚|撤回|退回)"),
    ),
    "conditional_adjustment": (
        re.compile(
            r"(?:如果|若|一旦|低于|高于|达到).{0,60}"
            r"(?:暂停|停止|回退|回滚|扩大|缩小|保持|调整)"
        ),
    ),
}


CONTRADICTORY_ABSENCE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "quantitative_threshold": (
        re.compile(
            r"(?:没有|未|尚未)(?:设置|设定|提出|给出|使用|明确).{0,8}"
            r"(?:阈值|门槛|量化标准)"
        ),
        re.compile(
            r"(?:缺少|缺乏)(?:明确的|量化的|量化)?"
            r"(?:阈值|门槛)(?!的?(?:依据|理由|来源|说明))"
        ),
    ),
    "monitoring": (
        re.compile(
            r"(?:没有|未|尚未)(?:设置|建立|进行|安排|提到|说明)?.{0,6}"
            r"(?:监控|监测|跟踪|持续观察)"
        ),
        re.compile(
            r"(?:缺少|缺乏)(?:监控|监测|跟踪)"
            r"(?!的?(?:频率|责任人|时间窗|执行细节))"
        ),
    ),
    "repeated_validation": (
        re.compile(
            r"(?:没有|未|尚未)(?:安排|进行|提出|说明)?.{0,6}"
            r"(?:复测|重复测试|连续测试|多轮测试)"
        ),
    ),
    "scope_control": (
        re.compile(
            r"(?:没有|未|尚未)(?:考虑|提出|设置|说明)?.{0,6}"
            r"(?:暂停扩大|缩小范围|保持小范围|逐步扩大|范围调整)"
        ),
    ),
    "rollback": (
        re.compile(
            r"(?:没有|未|尚未)(?:考虑|提出|设置|说明)?.{0,6}"
            r"(?:回退|回滚|撤回)"
        ),
    ),
    "conditional_adjustment": (
        re.compile(
            r"(?:没有|未|尚未)(?:设置|提出|给出|说明|明确)?.{0,6}"
            r"(?:触发条件|调整条件|停止条件|回退条件)"
        ),
    ),
}


BEHAVIOR_LABELS = {
    "quantitative_threshold": "量化阈值",
    "monitoring": "监测安排",
    "repeated_validation": "多轮验证",
    "scope_control": "范围控制",
    "rollback": "回退安排",
    "conditional_adjustment": "条件化调整",
}

NARROW_REFINEMENT_MARKERS = {
    "quantitative_threshold": (
        "阈值依据",
        "阈值的依据",
        "门槛依据",
        "门槛的依据",
        "成功率标准的依据",
    ),
    "monitoring": (
        "监控频率",
        "监测频率",
        "监控责任人",
        "监测责任人",
        "监控时间窗",
    ),
    "repeated_validation": (
        "复测标准",
        "复测的标准",
        "测试轮次的依据",
        "通过标准",
    ),
    "scope_control": (
        "范围调整的依据",
        "扩大范围的依据",
        "范围调整的责任人",
    ),
    "rollback": (
        "回退触发条件的依据",
        "回退触发条件的责任人",
        "回退执行步骤",
        "回滚执行步骤",
    ),
    "conditional_adjustment": (
        "调整条件的依据",
        "触发条件的依据",
        "触发条件的责任人",
    ),
}


_PERSPECTIVE_ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "buyer": ("买方用户", "买方", "买家", "购买者"),
    "seller": ("卖方用户", "卖方", "卖家", "供给方"),
    "platform": ("平台团队", "平台方", "平台"),
    "community": ("校园社团", "校园社区", "社团", "志愿者"),
    "engineering": ("研发团队", "技术团队", "研发"),
    "operations": ("运营团队", "运营"),
    "market": ("市场团队", "市场"),
}

_PERSPECTIVE_COMPARISON_PATTERN = re.compile(
    r"(?:目标|风险|担忧|诉求|希望|冲突|取舍|权衡|优先|依据|相比|同时|但是)"
)

CONTRADICTORY_ABSENCE_PATTERNS["multi_perspective_comparison"] = (
    re.compile(
        r"(?:没有|未能|未|缺少|缺乏).{0,12}"
        r"(?:比较|对比).{0,18}"
        r"(?:角色|立场|视角|利益相关方)"
    ),
    re.compile(
        r"(?:多元视角|多方视角|不同角色|不同立场).{0,8}"
        r"(?:不足|缺少|缺乏|未体现)"
    ),
    re.compile(
        r"(?:建议|需要|应当|可以).{0,10}"
        r"(?:纳入|考虑|比较|覆盖).{0,30}"
        r"(?:买方|卖方|平台|角色|立场|视角|利益相关方)"
    ),
)

BEHAVIOR_LABELS["multi_perspective_comparison"] = "多方角色比较与取舍"

NARROW_REFINEMENT_MARKERS["multi_perspective_comparison"] = (
    "角色优先级的依据",
    "不同立场的权重",
    "冲突排序的依据",
    "多方目标的权重",
    "角色覆盖范围",
)


def _matched_perspective_roles(text: str) -> set[str]:
    return {
        role
        for role, aliases in _PERSPECTIVE_ROLE_GROUPS.items()
        if any(alias in text for alias in aliases)
    }


def _has_multi_perspective_comparison(text: str) -> bool:
    return (
        len(_matched_perspective_roles(text)) >= 3
        and _PERSPECTIVE_COMPARISON_PATTERN.search(text) is not None
    )


def _is_multi_perspective_evidence(text: str) -> bool:
    return _has_multi_perspective_comparison(text)


def _best_multi_perspective_turn(
    user_turns: list[str],
) -> str | None:
    candidates = [
        text
        for text in user_turns
        if _has_multi_perspective_comparison(text)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda text: (
            len(_matched_perspective_roles(text)),
            len(text),
        ),
    )


def _collect_observed_behaviors(user_turns: list[str]) -> set[str]:
    corpus = "\n".join(user_turns)
    observed = {
        behavior
        for behavior, patterns in OBSERVED_BEHAVIOR_PATTERNS.items()
        if any(pattern.search(corpus) for pattern in patterns)
    }
    if _best_multi_perspective_turn(user_turns) is not None:
        observed.add("multi_perspective_comparison")
    return observed


def _find_contradicted_behaviors(
    text: str | None,
    observed_behaviors: set[str],
) -> set[str]:
    if not text:
        return set()

    conflicts: set[str] = set()
    for behavior in observed_behaviors:
        refinement_markers = NARROW_REFINEMENT_MARKERS.get(behavior, ())
        if any(marker in text for marker in refinement_markers):
            continue

        if any(
            pattern.search(text)
            for pattern in CONTRADICTORY_ABSENCE_PATTERNS.get(behavior, ())
        ):
            conflicts.add(behavior)

    return conflicts


def _behavior_label_text(behaviors: set[str]) -> str:
    labels = [
        BEHAVIOR_LABELS[behavior]
        for behavior in sorted(behaviors)
        if behavior in BEHAVIOR_LABELS
    ]
    return "、".join(labels) if labels else "相关做法"


def _safe_refinement_suggestion(behaviors: set[str]) -> str:
    labels = _behavior_label_text(behaviors)
    return (
        f"你已经呈现了{labels}。后续可以在现有做法基础上，"
        "进一步说明其依据、适用范围、责任人与复核方式。"
    )


def _filter_contradictory_items(
    items: list[str],
    observed_behaviors: set[str],
    field_name: str,
    warnings: list[str],
) -> list[str]:
    kept: list[str] = []
    for index, text in enumerate(items):
        conflicts = _find_contradicted_behaviors(text, observed_behaviors)
        if conflicts:
            warnings.append(
                "report contradiction removed: "
                f"field={field_name}[{index}] "
                f"behaviors={','.join(sorted(conflicts))}"
            )
            continue
        kept.append(text)
    return kept


def _find_containing_turn(text: str, user_turns: list[tuple[int | None, str]]) -> tuple[int | None, str] | None:
    """Return (turn_id, full_content) for the user turn that contains the quote.

    Falls back to difflib close matching if no direct substring match is found.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # 1. Exact substring match (prefer the shortest containing turn).
    candidates: list[tuple[int | None, str]] = []
    for turn_id, content in user_turns:
        if stripped in content:
            candidates.append((turn_id, content))
    if candidates:
        return min(candidates, key=lambda item: len(item[1]))

    # 2. Fuzzy close match against full user turn contents.
    contents = [content for _, content in user_turns]
    close = get_close_matches(stripped, contents, n=1, cutoff=0.75)
    if close:
        matched_content = close[0]
        for turn_id, content in user_turns:
            if content == matched_content:
                return turn_id, content

    return None


def _find_expected_evidence_turn(
    evidence,
    user_turns: list[tuple[int | None, str]],
) -> tuple[int | None, str] | None:
    eligible_turns = user_turns
    if evidence.dialogue_turn_id is not None:
        eligible_turns = [
            item for item in user_turns if item[0] == evidence.dialogue_turn_id
        ]
    return _find_containing_turn(evidence.text, eligible_turns)


def validate_scoring_output(
    context: AgentRuntimeContext,
    output: ScoringOutput,
) -> ScoringOutput:
    expected_keys = {item.dimension_key for item in context.rubric_dimensions}
    actual_keys = {item.dimension_key for item in output.scores}
    if actual_keys != expected_keys:
        raise ValueError(
            f"Scoring dimensions mismatch: missing={sorted(expected_keys - actual_keys)}, "
            f"extra={sorted(actual_keys - expected_keys)}"
        )

    user_turns = [
        (turn.turn_id, turn.content)
        for turn in context.dialogue_history
        if turn.speaker == "user"
        and turn.turn_id is not None
        and (
            is_scoring_analysis(turn.analysis_json, text=turn.content)
            if turn.analysis_json
            else classify_user_turn(turn.content) == "substantive_answer"
        )
    ]

    for score in output.scores:
        if score.assessment_status == "insufficient_evidence":
            if score.score is not None:
                raise ValueError(
                    f"Insufficient-evidence score must be null: {score.dimension_key}"
                )
            if score.evidence:
                raise ValueError(
                    f"Insufficient-evidence dimension must not cite evidence: {score.dimension_key}"
                )
            continue
        if score.score is None or not 1 <= score.score <= 5:
            raise ValueError(f"Score out of range for {score.dimension_key}: {score.score}")
        if score.confidence is not None and not 0 <= score.confidence <= 1:
            raise ValueError(f"Confidence out of range for {score.dimension_key}")
        if not any(
            evidence.evidence_type in {"supporting_evidence", "weak_evidence"}
            for evidence in score.evidence
        ):
            raise ValueError(
                f"Scored dimension lacks usable evidence: {score.dimension_key}"
            )
        for evidence in score.evidence:
            if evidence.evidence_type not in ALLOWED_EVIDENCE_TYPES:
                raise ValueError(f"Invalid evidence type: {evidence.evidence_type}")

            matched = _find_containing_turn(evidence.text, user_turns)
            if matched is None:
                raise ValueError(f"Evidence text is not traceable: {evidence.text}")
            turn_id, full_content = matched
            evidence.text = full_content
            evidence.dialogue_turn_id = turn_id
    return output


def validate_report_output(
    context: AgentRuntimeContext,
    scoring_output: ScoringOutput,
    output: ReportOutput,
) -> ReportOutput:
    expected_keys = {item.dimension_key for item in scoring_output.scores}
    actual_keys = {item.dimension_key for item in output.dimension_reports}
    if actual_keys != expected_keys:
        raise ValueError(
            f"Report dimensions mismatch: missing={sorted(expected_keys - actual_keys)}, "
            f"extra={sorted(actual_keys - expected_keys)}"
        )
    if not output.disclaimer:
        raise ValueError("Report missing disclaimer")
    if not output.summary:
        raise ValueError("Report missing summary")

    user_turns = [
        (turn.turn_id, turn.content)
        for turn in context.dialogue_history
        if turn.speaker == "user"
        and (
            is_scoring_analysis(turn.analysis_json, text=turn.content)
            if turn.analysis_json
            else classify_user_turn(turn.content) == "substantive_answer"
        )
    ]

    observed_behaviors = _collect_observed_behaviors(
        [content for _, content in user_turns]
    )
    semantic_warnings = list(output.warnings)
    scoring_by_key = {item.dimension_key: item for item in scoring_output.scores}

    for item in output.dimension_reports:
        expected = scoring_by_key[item.dimension_key]
        if item.assessment_status != expected.assessment_status or item.score != expected.score:
            raise ValueError(f"Report score mismatch: {item.dimension_key}")

        if item.assessment_status == "insufficient_evidence":
            if item.score is not None:
                raise ValueError(
                    f"Invalid insufficient-evidence report: {item.dimension_key}"
                )

            if item.weakness:
                semantic_warnings.append(
                    "insufficient-evidence weakness neutralized: "
                    f"dimension={item.dimension_key}"
                )

            item.level_label = "暂不评分"
            item.weakness = None
            if expected.score_kind == "provisional":
                allowed_quotes: list[str] = []
                for evidence in expected.evidence:
                    if evidence.evidence_type == "invalid_evidence":
                        continue
                    matched = _find_expected_evidence_turn(evidence, user_turns)
                    if matched is None:
                        raise ValueError(
                            "Provisional evidence is no longer eligible for report use: "
                            f"{evidence.text}"
                        )
                    _, full_content = matched
                    if full_content not in allowed_quotes:
                        allowed_quotes.append(full_content)
                allowed_quotes = allowed_quotes[:2]
                if item.evidence_quotes != allowed_quotes:
                    semantic_warnings.append(
                        "provisional report evidence isolated to ScoringOutput: "
                        f"dimension={item.dimension_key}"
                    )
                item.evidence_quotes = allowed_quotes
                item.strength = PROVISIONAL_REPORT_STRENGTH
                item.suggestion = PROVISIONAL_REPORT_SUGGESTION
            else:
                if item.evidence_quotes:
                    raise ValueError(
                        "Invalid unobserved report evidence: "
                        f"{item.dimension_key}"
                    )
                item.evidence_quotes = []
                item.strength = UNOBSERVED_REPORT_STRENGTH
                item.suggestion = UNOBSERVED_REPORT_SUGGESTION
            continue

        if item.score is None or not 1 <= item.score <= 5:
            raise ValueError(f"Report score out of range: {item.dimension_key}")

        allowed_quotes: list[str] = []
        for evidence in expected.evidence:
            if evidence.evidence_type == "invalid_evidence":
                continue
            matched = _find_expected_evidence_turn(evidence, user_turns)
            if matched is None:
                raise ValueError(
                    "Scoring evidence is no longer eligible for report use: "
                    f"{evidence.text}"
                )
            _, full_content = matched
            if full_content not in allowed_quotes:
                allowed_quotes.append(full_content)
        if item.evidence_quotes != allowed_quotes:
            item.evidence_quotes = allowed_quotes
            semantic_warnings.append(
                "report evidence isolated to ScoringOutput: "
                f"dimension={item.dimension_key}"
            )

        strength_conflicts = _find_contradicted_behaviors(
            item.strength,
            observed_behaviors,
        )
        if strength_conflicts:
            item.strength = (
                "本维度反馈以评分输出中的可追溯证据为依据，"
                "具体表现请结合下方原话理解。"
            )
            semantic_warnings.append(
                "report contradiction neutralized: "
                f"field={item.dimension_key}.strength "
                f"behaviors={','.join(sorted(strength_conflicts))}"
            )

        weakness_conflicts = _find_contradicted_behaviors(
            item.weakness,
            observed_behaviors,
        )
        if weakness_conflicts:
            item.weakness = None
            semantic_warnings.append(
                "report contradiction neutralized: "
                f"field={item.dimension_key}.weakness "
                f"behaviors={','.join(sorted(weakness_conflicts))}"
            )

        suggestion_conflicts = _find_contradicted_behaviors(
            item.suggestion,
            observed_behaviors,
        )
        if suggestion_conflicts:
            item.suggestion = _safe_refinement_suggestion(
                suggestion_conflicts
            )
            semantic_warnings.append(
                "report contradiction neutralized: "
                f"field={item.dimension_key}.suggestion "
                f"behaviors={','.join(sorted(suggestion_conflicts))}"
            )

    summary_conflicts = _find_contradicted_behaviors(
        output.summary,
        observed_behaviors,
    )
    if summary_conflicts:
        output.summary = (
            "本报告依据本次情境对话和评分结果整理，"
            "具体表现与发展建议见各维度反馈。"
        )
        semantic_warnings.append(
            "report contradiction neutralized: "
            f"field=summary behaviors={','.join(sorted(summary_conflicts))}"
        )

    output.advantages = _filter_contradictory_items(
        output.advantages,
        observed_behaviors,
        "advantages",
        semantic_warnings,
    )
    output.improvement_suggestions = _filter_contradictory_items(
        output.improvement_suggestions,
        observed_behaviors,
        "improvement_suggestions",
        semantic_warnings,
    )
    output.development_plan = _filter_contradictory_items(
        output.development_plan,
        observed_behaviors,
        "development_plan",
        semantic_warnings,
    )
    output.warnings = list(dict.fromkeys(semantic_warnings))

    return output
