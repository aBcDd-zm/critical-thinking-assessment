from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents.schemas import (  # noqa: E402
    AgentRuntimeContext,
    DialogueTurnContext,
    DimensionScore,
    ParticipantContext,
    RubricDimensionContext,
    ScenarioContext,
    ScoringOutput,
    SessionContext,
    StageContext,
    StageDimensionBindingContext,
)
from app.agents.semantic_scoring import apply_semantic_evidence_guardrails  # noqa: E402


def main() -> int:
    _check_primary_stage_coverage()
    _check_cross_stage_coverage()
    _check_covered_snapshot_is_not_downgraded()
    _check_multi_perspective_covered_evidence_sets_score_floor()
    _check_no_evidence_marks_ie()

    print("Semantic scoring guardrail checks passed.")
    print(
        "primary_coverage=passed, "
        "cross_stage_coverage=passed, "
        "snapshot_merge=passed, "
        "multi_perspective_floor=passed, "
        "true_no_evidence=IE"
    )
    return 0


def _check_primary_stage_coverage() -> None:
    dimension_key = "problem_definition"
    context = _context(
        dimension_key,
        bindings=[
            _binding(
                "s1_problem_definition",
                dimension_key,
                "primary",
            ),
        ],
        turns=[
            _turn(
                1,
                "s1_problem_definition",
                [
                    _evidence("核心问题", "covered"),
                    _evidence("约束条件", "partial"),
                ],
            ),
        ],
    )

    guarded = apply_semantic_evidence_guardrails(
        context,
        _output(dimension_key),
    )
    score = guarded.scores[0]

    assert score.assessment_status == "scored"
    assert score.score == 3
    assert score.confidence is not None
    assert score.confidence < 0.8
    assert any(
        "partial coverage for problem_definition" in warning
        for warning in guarded.warnings
    )


def _check_cross_stage_coverage() -> None:
    dimension_key = "evidence_evaluation"
    context = _context(
        dimension_key,
        bindings=[
            _binding(
                "s2_evidence_verification",
                dimension_key,
                "primary",
            ),
            _binding(
                "s5_dynamic_adjustment",
                dimension_key,
                "secondary",
            ),
        ],
        turns=[
            _turn(
                1,
                "s2_evidence_verification",
                [
                    _evidence("证据来源", "missing"),
                ],
            ),
            _turn(
                2,
                "s5_dynamic_adjustment",
                [
                    _evidence("监控条件", "covered"),
                ],
            ),
        ],
    )

    guarded = apply_semantic_evidence_guardrails(
        context,
        _output(dimension_key),
    )
    score = guarded.scores[0]

    assert score.assessment_status == "scored"
    assert score.score == 3
    assert score.confidence is not None
    assert score.confidence <= 0.65
    assert any(
        warning
        == "evidence_outside_primary_stage:evidence_evaluation"
        for warning in guarded.warnings
    )


def _check_covered_snapshot_is_not_downgraded() -> None:
    dimension_key = "reasoning_argumentation"
    context = _context(
        dimension_key,
        bindings=[
            _binding(
                "s4_reasoning_decision",
                dimension_key,
                "primary",
            ),
        ],
        turns=[
            _turn(
                1,
                "s4_reasoning_decision",
                [
                    _evidence("判断依据", "covered"),
                ],
            ),
            _turn(
                2,
                "s4_reasoning_decision",
                [
                    _evidence("判断依据", "missing"),
                ],
            ),
        ],
    )

    guarded = apply_semantic_evidence_guardrails(
        context,
        _output(dimension_key),
    )
    score = guarded.scores[0]

    assert score.assessment_status == "scored"
    assert score.score == 3
    assert score.confidence == 0.8
    assert not any(
        "marked reasoning_argumentation as IE" in warning
        for warning in guarded.warnings
    )


def _check_multi_perspective_covered_evidence_sets_score_floor() -> None:
    dimension_key = "multiple_perspectives"
    answer = (
        "买方希望更快找到教材，卖方希望获得有效曝光，平台团队需要控制开发成本，"
        "校园社团希望提高参与度。主要冲突是扩大宣传可能增加流量，却不能解决搜索无点击；"
        "我会优先做最小范围的搜索优化，因为问卷和点击数据表明影响范围更广。"
    )
    context = _context(
        dimension_key,
        bindings=[
            _binding(
                "s3_stakeholder_perspectives",
                dimension_key,
                "primary",
            ),
        ],
        turns=[
            _turn(
                1,
                "s3_stakeholder_perspectives",
                [
                    _evidence("利益相关方", "covered"),
                    _evidence("视角冲突", "covered"),
                    _evidence("取舍依据", "covered"),
                ],
                content=answer,
            ),
        ],
    )
    raw = ScoringOutput(
        summary="评分回归",
        scores=[
            DimensionScore(
                dimension_key=dimension_key,
                score=2,
                confidence=0.8,
                reason="只是列举角色，未比较不同立场的冲突和取舍。",
            ),
        ],
    )

    guarded = apply_semantic_evidence_guardrails(context, raw)
    score = guarded.scores[0]

    assert score.assessment_status == "scored"
    assert score.score is not None and score.score >= 3
    assert "未比较" not in score.reason
    assert (
        "semantic_score_floor_applied:multiple_perspectives"
        in guarded.warnings
    )


def _check_no_evidence_marks_ie() -> None:
    dimension_key = "multiple_perspectives"
    context = _context(
        dimension_key,
        bindings=[
            _binding(
                "s3_stakeholder_perspectives",
                dimension_key,
                "primary",
            ),
        ],
        turns=[
            _turn(
                1,
                "s3_stakeholder_perspectives",
                [
                    _evidence("视角冲突", "missing"),
                    _evidence("取舍依据", "missing"),
                ],
            ),
        ],
    )

    guarded = apply_semantic_evidence_guardrails(
        context,
        _output(dimension_key),
    )
    score = guarded.scores[0]

    assert score.assessment_status == "insufficient_evidence"
    assert score.score is None
    assert score.confidence is None
    assert score.evidence == []
    assert any(
        "marked multiple_perspectives as IE" in warning
        for warning in guarded.warnings
    )
    assert any(
        "本次对话尚未充分呈现" in gap
        for gap in guarded.detected_score_gaps
    )


def _context(
    dimension_key: str,
    *,
    bindings: list[StageDimensionBindingContext],
    turns: list[DialogueTurnContext],
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        session=SessionContext(
            session_uuid=f"semantic-scoring-{dimension_key}",
        ),
        participant=ParticipantContext(nickname="评分回归"),
        scenario=ScenarioContext(
            scenario_code="product_launch_48h",
            title="产品上线前 48 小时",
            background="测试背景",
        ),
        stage=StageContext(
            stage_code="s6_integrated_plan",
            stage_order=6,
            title="综合方案",
            stage_goal="形成综合方案",
            context="测试上下文",
            main_question="最终如何安排？",
        ),
        rubric_dimensions=[
            RubricDimensionContext(
                dimension_key=dimension_key,
                name=dimension_key,
                definition="回归测试维度",
            ),
        ],
        stage_dimension_bindings=bindings,
        dialogue_history=turns,
    )


def _output(dimension_key: str) -> ScoringOutput:
    return ScoringOutput(
        summary="评分回归",
        scores=[
            DimensionScore(
                dimension_key=dimension_key,
                score=3,
                confidence=0.8,
                reason="原始模型评分",
            ),
        ],
    )


def _binding(
    stage_code: str,
    dimension_key: str,
    observe_role: str,
) -> StageDimensionBindingContext:
    return StageDimensionBindingContext(
        stage_code=stage_code,
        dimension_key=dimension_key,
        observe_role=observe_role,
        weight=1,
    )


def _turn(
    turn_id: int,
    stage_code: str,
    snapshot: list[dict],
    *,
    content: str = "测试回答",
) -> DialogueTurnContext:
    return DialogueTurnContext(
        turn_id=turn_id,
        turn_index=turn_id,
        stage_code=stage_code,
        speaker="user",
        content=content,
        content_type="scenario_answer",
        analysis_json={
            "intent": "substantive_answer",
            "relevance": "relevant",
            "resolved_response_category": "assess_answer",
            "resolved_evidence_snapshot": snapshot,
        },
    )


def _evidence(
    key: str,
    coverage: str,
) -> dict:
    return {
        "evidence_key": key,
        "coverage": coverage,
        "supporting_turn_indexes": [],
        "reason": "回归夹具",
        "confidence": 0.8,
    }


if __name__ == "__main__":
    raise SystemExit(main())
