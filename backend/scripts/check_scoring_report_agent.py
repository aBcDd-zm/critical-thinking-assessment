from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from app.agents import ReportOutput, ScoringOutput  # noqa: E402
from app.agents.schemas import EvidenceItem  # noqa: E402
from app.agents.mock_scoring_report import (  # noqa: E402
    DIMENSION_KEYWORDS,
    build_mock_report_output,
    build_mock_scoring_output,
)
from app.agents.rag_context import (  # noqa: E402
    build_report_context_block,
    build_scoring_context_block,
)
from app.agents.report_agent import ReportAgent  # noqa: E402
from app.agents.report_prompts import build_report_messages  # noqa: E402
from app.agents.scoring_agent import ScoringAgent  # noqa: E402
from app.agents.scoring_prompts import build_scoring_messages  # noqa: E402
from app.agents.scoring_report_llm_client import CLLMResult, ScoringReportLLMClient  # noqa: E402
from app.agents.scoring_report_validators import (  # noqa: E402
    validate_report_output,
    validate_scoring_output,
)
from check_agent_fixture_cases import (  # noqa: E402
    build_context_from_dialogue_case,
    load_fixtures,
    load_seed_data,
    validate_dialogue_cases,
    validate_scoring_cases,
    validate_users,
)
from app.core.config import get_settings  # noqa: E402


EXPECTED_CASE_IDS = {
    "student_weak_scoring",
    "student_medium_scoring",
    "workplace_strong_scoring",
}
ALLOWED_EVIDENCE_TYPES = {
    "supporting_evidence",
    "weak_evidence",
    "invalid_evidence",
}


def merge_dialogue_contexts(contexts):
    base = contexts[-1]
    merged_history = []
    next_turn_id = 1
    for context in contexts:
        for turn in context.dialogue_history:
            merged_history.append(
                turn.model_copy(
                    update={
                        "turn_id": next_turn_id,
                        "turn_index": next_turn_id,
                    }
                )
            )
            next_turn_id += 1
    latest_user_turn = next(
        (turn for turn in reversed(merged_history) if turn.speaker == "user"),
        None,
    )
    return base.model_copy(
        update={
            "session": base.session.model_copy(
                update={"session_uuid": f"fixture-scoring-{base.session.session_uuid}"}
            ),
            "dialogue_history": merged_history,
            "latest_user_turn": latest_user_turn,
        }
    )


def build_scoring_context(scoring_case, users_by_id, dialogue_cases_by_id, scenario_seed, rubric_seed):
    contexts = [
        build_context_from_dialogue_case(
            dialogue_cases_by_id[dialogue_case_id],
            users_by_id,
            scenario_seed,
            rubric_seed,
        )
        for dialogue_case_id in scoring_case["dialogue_case_ids"]
    ]
    return merge_dialogue_contexts(contexts)


def assert_five_level_scoring_and_evidence_alignment(context) -> None:
    anchor_levels_by_dimension: dict[str, set[int]] = {}
    for anchor in context.rubric_anchors:
        anchor_levels_by_dimension.setdefault(
            anchor.dimension_key,
            set(),
        ).add(anchor.score_level)
    for dimension in context.rubric_dimensions:
        assert anchor_levels_by_dimension.get(dimension.dimension_key) == {
            1,
            2,
            3,
            4,
            5,
        }

    template_turn = next(
        turn
        for turn in reversed(context.dialogue_history)
        if turn.speaker == "user"
    )
    next_turn_id = max(
        (turn.turn_id or 0 for turn in context.dialogue_history),
        default=0,
    ) + 1
    next_turn_index = max(
        (turn.turn_index or 0 for turn in context.dialogue_history),
        default=0,
    ) + 1
    calibration_answers = [
        (
            "我会查看原始项目记录，核对谁记录、计划和实际口径是否一致，"
            "排除事后补录；再按任务规模和团队配置抽样，"
            "用项目日志和交付时间交叉验证，并标注仍可能存在的偏差。"
        ),
        (
            "两人分别负责记录核验和问题修复，每天中午和下班同步；"
            "优先处理核心链路，第三天仍未达标就缩减范围，"
            "暂停发布并标注不确定性。"
        ),
        (
            "若新数据与原样本可比就扩大验证；等待时间下降40%时保留方案，"
            "如果核心错误超过2个百分点就停止扩展并复查。"
        ),
    ]
    added_turns = []
    for offset, content in enumerate(calibration_answers):
        analysis = dict(template_turn.analysis_json or {})
        analysis.update(
            {
                "intent": "substantive_answer",
                "relevance": "on_topic",
                "response_category": "assess_answer",
                "resolved_response_category": "assess_answer",
            }
        )
        added_turns.append(
            template_turn.model_copy(
                update={
                    "turn_id": next_turn_id + offset,
                    "turn_index": next_turn_index + offset,
                    "speaker": "user",
                    "content": content,
                    "content_type": "scenario_answer",
                    "analysis_json": analysis,
                }
            )
        )

    calibrated_context = context.model_copy(
        update={
            "dialogue_history": [
                *context.dialogue_history,
                *added_turns,
            ],
            "latest_user_turn": added_turns[-1],
        }
    )
    output = build_mock_scoring_output(
        calibrated_context,
        snapshot_type="final",
    )
    scores = {
        item.dimension_key: item
        for item in output.scores
    }
    expected_evidence = {
        "evidence_evaluation": "原始项目记录",
        "integrative_decision": "两人分别负责",
        "dynamic_adjustment": "下降40%",
    }
    for dimension_key, expected_phrase in expected_evidence.items():
        score = scores[dimension_key]
        assert score.score is not None and score.score >= 4
        assert "档标准" in score.reason
        assert any(
            expected_phrase in evidence.text
            for evidence in score.evidence
        ), (
            f"{dimension_key} should cite its strongest relevant answer; "
            f"evidence={[item.text for item in score.evidence]}"
        )


def assert_score_ranges(case_id: str, scoring_case, scoring_output: ScoringOutput) -> None:
    scores_by_key = {item.dimension_key: item for item in scoring_output.scores}
    for expected in scoring_case["expected_dimension_ranges"]:
        dimension_key = expected["dimension_key"]
        if dimension_key not in scores_by_key:
            raise AssertionError(f"{case_id}: missing score for {dimension_key}")
        score = scores_by_key[dimension_key].score
        if score is None:
            if (
                case_id == "student_weak_scoring"
                and scores_by_key[dimension_key].assessment_status == "insufficient_evidence"
            ):
                continue
            raise AssertionError(f"{case_id}: {dimension_key} unexpectedly has no score")
        if not expected["min_score"] <= score <= expected["max_score"]:
            raise AssertionError(
                f"{case_id}: {dimension_key} score {score} outside "
                f"{expected['min_score']}..{expected['max_score']}"
            )


def assert_all_dimensions_scored(case_id: str, context, scoring_output: ScoringOutput) -> None:
    expected_keys = {item.dimension_key for item in context.rubric_dimensions}
    actual_keys = {item.dimension_key for item in scoring_output.scores}
    if actual_keys != expected_keys:
        raise AssertionError(
            f"{case_id}: scoring dimensions mismatch, "
            f"missing={sorted(expected_keys - actual_keys)}, extra={sorted(actual_keys - expected_keys)}"
        )


def assert_evidence_traceable(case_id: str, context, scoring_output: ScoringOutput) -> None:
    history_texts = {turn.content for turn in context.dialogue_history}
    history_by_id = {turn.turn_id: turn.content for turn in context.dialogue_history if turn.turn_id}
    for score in scoring_output.scores:
        for evidence in score.evidence:
            if evidence.evidence_type not in ALLOWED_EVIDENCE_TYPES:
                raise AssertionError(
                    f"{case_id}: invalid evidence type {evidence.evidence_type}"
                )
            if evidence.text not in history_texts:
                raise AssertionError(
                    f"{case_id}: evidence is not from dialogue history: {evidence.text}"
                )
            if evidence.dialogue_turn_id is None:
                raise AssertionError(f"{case_id}: evidence should keep dialogue_turn_id")
            if history_by_id.get(evidence.dialogue_turn_id) != evidence.text:
                raise AssertionError(
                    f"{case_id}: evidence dialogue_turn_id does not match evidence text"
                )
            if evidence.evidence_type != "invalid_evidence":
                keywords = DIMENSION_KEYWORDS.get(score.dimension_key, [])
                if not any(keyword in evidence.text for keyword in keywords):
                    raise AssertionError(
                        f"{case_id}: {score.dimension_key} evidence does not support "
                        f"the dimension: {evidence.text}"
                    )


def assert_report(case_id: str, scoring_case, report_output: ReportOutput) -> None:
    serialized = str(report_output.model_dump(mode="json"))
    for point in scoring_case["expected_report_points"]:
        if point not in serialized:
            raise AssertionError(f"{case_id}: report missing expected point: {point}")
    if scoring_case.get("must_have_disclaimer") and not report_output.disclaimer:
        raise AssertionError(f"{case_id}: report missing disclaimer")
    if scoring_case.get("must_have_disclaimer") and "不作" not in report_output.disclaimer:
        raise AssertionError(f"{case_id}: disclaimer must state limitation")


def assert_low_information_handling(case_id: str, scoring_output: ScoringOutput) -> None:
    if case_id != "student_weak_scoring":
        return
    insufficient_scores = [
        score
        for score in scoring_output.scores
        if score.assessment_status == "insufficient_evidence"
        and score.score is None
        and score.confidence is None
        and not score.evidence
    ]
    if not insufficient_scores:
        raise AssertionError(f"{case_id}: expected unscored dimensions for missing evidence")
    if not scoring_output.detected_score_gaps:
        raise AssertionError(f"{case_id}: expected detected score gaps")


def assert_report_contradiction_guardrail(context, scoring_output: ScoringOutput) -> None:
    template_turn = next(
        turn for turn in context.dialogue_history if turn.speaker == "user"
    )
    next_turn_id = max(
        (turn.turn_id or 0 for turn in context.dialogue_history),
        default=0,
    ) + 1

    behavior_text = (
        "我会先在低端设备和弱网环境连续测试两轮；"
        "同步成功率达到99%后再逐步扩大范围，并继续监控失败率。"
        "如果指标下降或再次影响核心任务，就暂停扩大并回退。"
    )

    evidence_turn = template_turn.model_copy(
        update={
            "turn_id": next_turn_id,
            "turn_index": next_turn_id,
            "stage_code": "s6_integrated_plan",
            "speaker": "user",
            "content": behavior_text,
            "content_type": "scenario_answer",
            "analysis_json": {
                "intent": "substantive_answer",
                "relevance": "relevant",
                "resolved_response_category": "assess_answer",
            },
        }
    )

    guarded_context = context.model_copy(
        update={
            "dialogue_history": [
                *context.dialogue_history,
                evidence_turn,
            ],
            "latest_user_turn": evidence_turn,
        }
    )

    target_score = next(
        (
            score
            for score in scoring_output.scores
            if score.dimension_key == "dynamic_adjustment"
            and score.assessment_status == "scored"
        ),
        next(
            score
            for score in scoring_output.scores
            if score.assessment_status == "scored"
        ),
    )

    dimension_names = {
        dimension.dimension_key: dimension.name
        for dimension in context.rubric_dimensions
    }

    dimension_reports = []
    for score in scoring_output.scores:
        insufficient = score.assessment_status == "insufficient_evidence"
        is_target = score.dimension_key == target_score.dimension_key

        dimension_reports.append(
            {
                "dimension_key": score.dimension_key,
                "dimension_name": dimension_names[score.dimension_key],
                "score": score.score,
                "assessment_status": score.assessment_status,
                "level_label": "暂不评分" if insufficient else "medium",
                "strength": (
                    "现有证据不足以判断。"
                    if insufficient
                    else "能够根据结果调整安排。"
                ),
                "weakness": (
                    None
                    if insufficient or not is_target
                    else (
                        "没有设置量化阈值，也没有持续监控、进行重复测试、"
                        "暂停扩大或回退安排。"
                    )
                ),
                "evidence_quotes": (
                    [behavior_text]
                    if is_target and not insufficient
                    else []
                ),
                "suggestion": (
                    "建议继续补充可判断的信息。"
                    if insufficient
                    else (
                        "建议设置量化阈值、增加持续监控、进行重复测试，"
                        "并考虑暂停扩大和回退。"
                        if is_target
                        else "继续说明判断依据。"
                    )
                ),
            }
        )

    contradictory_report = ReportOutput.model_validate(
        {
            "summary": "受测者没有设置量化阈值，也没有持续监控和回退安排。",
            "overall_level": "测试",
            "dimension_reports": dimension_reports,
            "advantages": [],
            "improvement_suggestions": [
                "建议设置量化阈值并增加持续监控。",
                "建议考虑暂停扩大和回退。",
            ],
            "development_plan": [
                "进行重复测试，并在风险出现时暂停扩大。",
            ],
            "disclaimer": "本报告仅用于回归测试，不作为选拔结论。",
        }
    )

    guarded = validate_report_output(
        guarded_context,
        scoring_output,
        contradictory_report,
    )

    serialized = str(guarded.model_dump(mode="json"))
    forbidden_claims = [
        "没有设置量化阈值",
        "没有持续监控",
        "没有进行重复测试",
        "没有暂停扩大",
        "没有回退安排",
    ]
    for claim in forbidden_claims:
        if claim in serialized:
            raise AssertionError(
                f"report contradiction was not neutralized: {claim}"
            )

    guarded_target = next(
        item
        for item in guarded.dimension_reports
        if item.dimension_key == target_score.dimension_key
    )
    if guarded_target.weakness is not None:
        raise AssertionError(
            "contradictory weakness should be neutralized"
        )
    if not any(
        "contradiction" in warning.lower()
        for warning in guarded.warnings
    ):
        raise AssertionError(
            "report contradiction guardrail should leave an audit warning"
        )

    narrow_suggestion = "可以进一步说明99%阈值的依据和监控频率。"
    narrow_reports = [
        item.model_copy(
            update={
                "weakness": None,
                "suggestion": (
                    narrow_suggestion
                    if item.dimension_key == target_score.dimension_key
                    else item.suggestion
                ),
            }
        )
        for item in guarded.dimension_reports
    ]
    narrow_report = guarded.model_copy(
        update={
            "summary": "报告基于本次对话中的可追溯证据生成。",
            "dimension_reports": narrow_reports,
            "advantages": [],
            "improvement_suggestions": [narrow_suggestion],
            "development_plan": [],
            "warnings": [],
        }
    )

    narrow_guarded = validate_report_output(
        guarded_context,
        scoring_output,
        narrow_report,
    )
    narrow_target = next(
        item
        for item in narrow_guarded.dimension_reports
        if item.dimension_key == target_score.dimension_key
    )
    if narrow_target.suggestion != narrow_suggestion:
        raise AssertionError(
            "valid refinement suggestion should be preserved"
        )
    if narrow_suggestion not in narrow_guarded.improvement_suggestions:
        raise AssertionError(
            "valid aggregate refinement suggestion should be preserved"
        )

def assert_multi_perspective_report_guardrail(
    context,
    scoring_output: ScoringOutput,
) -> None:
    perspective_text = (
        "买方希望更快找到需要的教材，风险是搜索不准导致放弃交易；"
        "卖方希望获得有效曝光并尽快售出，风险是长尾书长期无人看到；"
        "平台团队希望控制开发成本，校园社团希望提高参与度；"
        "研发团队关注实现质量，运营团队关注反馈承载，"
        "市场团队关注用户增长。这些目标存在冲突，"
        "我会优先做小范围搜索优化，取舍依据是它能同时覆盖买卖双方，"
        "并且可以由社团参与灰度验证。"
    )
    unrelated_text = "我会先整理时间表并确认执行顺序。"

    template_turn = next(
        turn
        for turn in reversed(context.dialogue_history)
        if turn.speaker == "user"
    )
    next_turn_id = max(
        (turn.turn_id or 0 for turn in context.dialogue_history),
        default=0,
    ) + 1
    next_turn_index = max(
        (turn.turn_index or 0 for turn in context.dialogue_history),
        default=0,
    ) + 1

    def eligible_turn(
        turn_id: int,
        turn_index: int,
        content: str,
    ):
        analysis = dict(template_turn.analysis_json or {})
        analysis.update(
            {
                "intent": "substantive_answer",
                "relevance": "on_topic",
                "response_category": "assess_answer",
                "resolved_response_category": "assess_answer",
            }
        )
        return template_turn.model_copy(
            update={
                "turn_id": turn_id,
                "turn_index": turn_index,
                "stage_code": "s3_stakeholder_perspectives",
                "speaker": "user",
                "content": content,
                "content_type": "scenario_answer",
                "analysis_json": analysis,
            }
        )

    added_turns = [
        eligible_turn(
            next_turn_id,
            next_turn_index,
            perspective_text,
        ),
        eligible_turn(
            next_turn_id + 1,
            next_turn_index + 1,
            unrelated_text,
        ),
    ]
    perspective_context = context.model_copy(
        update={
            "dialogue_history": [
                *context.dialogue_history,
                *added_turns,
            ],
            "latest_user_turn": added_turns[-1],
        }
    )
    perspective_scoring_output = scoring_output.model_copy(
        update={
            "scores": [
                item.model_copy(
                    update={
                        "evidence": [
                            EvidenceItem(
                                text=perspective_text,
                                evidence_type="supporting_evidence",
                                explanation="比较多方目标、风险与取舍依据。",
                                dialogue_turn_id=next_turn_id,
                            )
                        ]
                    }
                )
                if item.dimension_key == "multiple_perspectives"
                else item
                for item in scoring_output.scores
            ]
        }
    )

    def report_with_suggestion(suggestion: str) -> ReportOutput:
        report = build_mock_report_output(
            perspective_context,
            perspective_scoring_output,
        )
        reports = [
            item.model_copy(
                update={
                    "weakness": (
                        "没有比较不同角色的目标和风险，多元视角不足。"
                    ),
                    "suggestion": suggestion,
                    "evidence_quotes": [unrelated_text],
                }
            )
            if item.dimension_key == "multiple_perspectives"
            else item
            for item in report.dimension_reports
        ]
        return report.model_copy(
            update={
                "dimension_reports": reports,
                "warnings": [],
            }
        )

    contradictory_report = report_with_suggestion(
        "建议纳入买方、卖方和平台等不同立场。"
    )
    validated = validate_report_output(
        perspective_context,
        perspective_scoring_output,
        contradictory_report,
    )
    target = next(
        item
        for item in validated.dimension_reports
        if item.dimension_key == "multiple_perspectives"
    )

    assert target.weakness is None, (
        "multi-perspective weakness should be neutralized"
    )
    assert "建议纳入" not in target.suggestion, (
        "multi-perspective suggestion should extend observed behavior"
    )
    assert target.evidence_quotes == [perspective_text], (
        "multiple-perspectives evidence should be realigned "
        "to the relevant user turn"
    )
    assert any(
        "multi_perspective_comparison" in warning
        for warning in validated.warnings
    )
    assert any(
        "report evidence isolated to ScoringOutput: "
        "dimension=multiple_perspectives" in warning
        for warning in validated.warnings
    )

    narrow_suggestion = "可以进一步说明不同角色冲突排序的依据。"
    narrow_report = report_with_suggestion(narrow_suggestion)
    narrow_validated = validate_report_output(
        perspective_context,
        perspective_scoring_output,
        narrow_report,
    )
    narrow_target = next(
        item
        for item in narrow_validated.dimension_reports
        if item.dimension_key == "multiple_perspectives"
    )
    assert narrow_target.suggestion == narrow_suggestion, (
        "narrow multi-perspective refinement should be preserved"
    )


class _BadScoringClient:
    def call_scoring(self, context):
        return CLLMResult(
            success=False,
            output=None,
            raw_output="{bad",
            error_code="INVALID_JSON",
            error_reason="bad json",
            model_name="fake",
        )


class _BadReportClient:
    def call_report(self, context, scoring_output):
        return CLLMResult(
            success=False,
            output=None,
            raw_output="{bad",
            error_code="INVALID_JSON",
            error_reason="bad json",
            model_name="fake",
        )


class _RetryableReportClient:
    def __init__(self) -> None:
        self.call_count = 0

    def call_report(self, context, scoring_output):
        self.call_count += 1
        output = build_mock_report_output(context, scoring_output)

        if self.call_count == 1:
            reports = list(output.dimension_reports)
            target_index = next(
                (
                    index
                    for index, report in enumerate(reports)
                    if report.score is not None
                ),
                None,
            )
            if target_index is None:
                raise AssertionError(
                    "retry fixture requires at least one scored dimension"
                )

            target = reports[target_index]
            invalid_score = 1 if target.score != 1 else 2
            reports[target_index] = target.model_copy(
                update={"score": invalid_score}
            )
            output = output.model_copy(
                update={"dimension_reports": reports}
            )

        return CLLMResult(
            success=True,
            output=output,
            raw_output="{}",
            error_code=None,
            error_reason=None,
            model_name="fake",
        )


def main() -> int:
    users, dialogue_cases, scoring_cases = load_fixtures()
    scenario_seed, rubric_seed = load_seed_data()
    users_by_id = validate_users(users)
    dialogue_cases_by_id = validate_dialogue_cases(
        dialogue_cases,
        users_by_id,
        scenario_seed,
        rubric_seed,
    )
    validate_scoring_cases(scoring_cases, users_by_id, dialogue_cases_by_id, rubric_seed)

    cases_by_id = {case["case_id"]: case for case in scoring_cases}
    missing_cases = EXPECTED_CASE_IDS - set(cases_by_id)
    if missing_cases:
        raise AssertionError(f"Missing expected scoring cases: {sorted(missing_cases)}")

    scoring_agent = ScoringAgent()
    report_agent = ReportAgent()
    settings = get_settings()
    original_mode = settings.MODEL_GATEWAY_MODE
    # Fixture assertions must stay deterministic even when the developer's
    # local .env points at a real provider. The fake-client checks below
    # temporarily exercise the real-mode retry/fallback branches.
    settings.MODEL_GATEWAY_MODE = "mock"
    for case_id in sorted(EXPECTED_CASE_IDS):
        scoring_case = cases_by_id[case_id]
        context = build_scoring_context(
            scoring_case,
            users_by_id,
            dialogue_cases_by_id,
            scenario_seed,
            rubric_seed,
        )
        scoring_context_block = build_scoring_context_block(context)
        if "Rubric dimensions:" not in scoring_context_block:
            raise AssertionError(f"{case_id}: scoring context missing rubric dimensions")
        if "Dialogue:" not in scoring_context_block:
            raise AssertionError(f"{case_id}: scoring context missing dialogue")
        report_context_block = build_report_context_block(context)
        if "Report boundaries:" not in report_context_block:
            raise AssertionError(f"{case_id}: report context missing boundaries")
        scoring_messages = build_scoring_messages(context)
        if not scoring_messages or scoring_messages[0]["role"] != "system":
            raise AssertionError(f"{case_id}: scoring prompt messages missing system prompt")
        client = ScoringReportLLMClient()
        if client.temperature > 1:
            raise AssertionError(f"{case_id}: scoring/report client misconfigured")

        scoring_output = ScoringOutput.model_validate(
            scoring_agent.generate(context, snapshot_type="final").model_dump()
        )
        scoring_output = validate_scoring_output(context, scoring_output)
        if case_id == "workplace_strong_scoring":
            assert_five_level_scoring_and_evidence_alignment(context)
            assert_report_contradiction_guardrail(
                context,
                scoring_output,
            )
            assert_multi_perspective_report_guardrail(
                context,
                scoring_output,
            )

        report_messages = build_report_messages(context, scoring_output)
        if not report_messages or report_messages[0]["role"] != "system":
            raise AssertionError(f"{case_id}: report prompt messages missing system prompt")

        settings.MODEL_GATEWAY_MODE = "real"
        try:
            fallback_output = ScoringAgent(llm_client=_BadScoringClient()).generate(
                context,
                snapshot_type="final",
            )
            if not fallback_output.fallback_used:
                raise AssertionError(f"{case_id}: expected scoring fallback_used=true")

            fallback_report = ReportAgent(llm_client=_BadReportClient()).generate(
                context,
                scoring_output,
            )
            if not fallback_report.fallback_used:
                raise AssertionError(f"{case_id}: expected report fallback_used=true")

            if case_id == "workplace_strong_scoring":
                retry_client = _RetryableReportClient()
                retry_report = ReportAgent(
                    llm_client=retry_client
                ).generate(
                    context,
                    scoring_output,
                )
                if retry_client.call_count != 2:
                    raise AssertionError(
                        f"{case_id}: expected exactly 2 report calls, "
                        f"got {retry_client.call_count}"
                    )
                if retry_report.fallback_used:
                    raise AssertionError(
                        f"{case_id}: validation retry should recover "
                        "without fallback"
                    )
        finally:
            settings.MODEL_GATEWAY_MODE = "mock"

        assert_all_dimensions_scored(case_id, context, scoring_output)
        assert_score_ranges(case_id, scoring_case, scoring_output)
        assert_evidence_traceable(case_id, context, scoring_output)
        assert_low_information_handling(case_id, scoring_output)

        report_output = ReportOutput.model_validate(
            report_agent.generate(context, scoring_output).model_dump()
        )
        report_output = validate_report_output(context, scoring_output, report_output)
        assert_report(case_id, scoring_case, report_output)

        print(
            f"[OK] {case_id}: scores={len(scoring_output.scores)}, "
            f"dimension_reports={len(report_output.dimension_reports)}"
        )

    settings.MODEL_GATEWAY_MODE = original_mode
    print(f"Scoring/report agent checks passed: cases={len(EXPECTED_CASE_IDS)}, dimensions=6")
    return 0


if __name__ == "__main__":
    sys.exit(main())
