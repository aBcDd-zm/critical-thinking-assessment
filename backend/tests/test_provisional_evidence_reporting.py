from __future__ import annotations

import unittest
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.mock_scoring_report import build_mock_report_output
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    DimensionScore,
    EvidenceItem,
    ParticipantContext,
    RubricDimensionContext,
    ScenarioContext,
    ScoringOutput,
    SessionContext,
    StageContext,
)
from app.agents.scoring_report_validators import validate_report_output
from app.models import Base
from app.models.assessment import AssessmentSession, DialogueTurn
from app.services.admin_session_review_service import _persisted_score_kind
from app.services.evidence_sufficiency_service import EvidenceSufficiencyService


DIMENSION_KEY = "problem_definition"
FORMAL_ANALYSIS = {
    "analysis_source": "consultative_turn_v3_3",
    "response_intent": "assess_answer",
    "formal_answer": True,
    "excluded_from_scoring": False,
}


def _session(state: dict) -> AssessmentSession:
    return AssessmentSession(
        session_uuid="provisional-evidence-session",
        participant_id=101,
        scenario_id=201,
        selection_mode="fixed",
        status="completed",
        assessment_mode="mock",
        language_mode="standard",
        flow_version="progressive_v3_3",
        interview_state_json=state,
        state_version=1,
    )


def _turn(
    session_id: int,
    turn_index: int,
    content: str,
    *,
    speaker: str = "user",
) -> DialogueTurn:
    return DialogueTurn(
        session_id=session_id,
        turn_index=turn_index,
        speaker=speaker,
        content=content,
        content_type="scenario_answer" if speaker == "user" else "interview_followup",
        analysis_json=dict(FORMAL_ANALYSIS) if speaker == "user" else None,
    )


def _context(turns: list[DialogueTurn]) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        session=SessionContext(
            session_id=turns[0].session_id,
            session_uuid="provisional-evidence-session",
        ),
        participant=ParticipantContext(participant_id=101),
        scenario=ScenarioContext(
            scenario_id=201,
            scenario_code="test",
            title="测试情境",
            background="测试背景",
        ),
        stage=StageContext(
            stage_id=None,
            stage_code="s1_problem_definition",
            stage_order=1,
            title="问题界定",
            stage_goal="观察问题界定",
            context="测试上下文",
            main_question="你会先判断什么？",
        ),
        dialogue_history=[
            DialogueTurnContext(
                turn_id=turn.id,
                turn_index=turn.turn_index,
                speaker=turn.speaker,
                content=turn.content,
                content_type=turn.content_type,
                analysis_json=turn.analysis_json,
            )
            for turn in turns
            if turn.speaker == "user"
        ],
        rubric_dimensions=[
            RubricDimensionContext(
                dimension_key=DIMENSION_KEY,
                name="问题界定",
                definition="识别核心问题、约束和边界。",
            )
        ],
    )


class ProvisionalEvidenceReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_high_esi_provisional_stays_unscored_and_keeps_two_auditable_quotes(
        self,
    ) -> None:
        with self.session_factory() as db:
            assessment = _session({})
            db.add(assessment)
            db.flush()
            turns = [
                _turn(
                    assessment.id,
                    1,
                    "我会先确认五天内必须交付的核心范围和现有资源约束。",
                ),
                _turn(
                    assessment.id,
                    3,
                    "关键矛盾是交付进度与质量验收之间的取舍。",
                ),
                _turn(
                    assessment.id,
                    5,
                    "我会把最低验收标准作为范围边界，再排优先顺序。",
                ),
            ]
            cross_session_turn = _turn(
                assessment.id + 999,
                1,
                "这是另一个会话的用户回答，不得被引用。",
            )
            ai_turn = _turn(
                assessment.id,
                7,
                "这是 AI 回合，不得被引用。",
                speaker="ai",
            )
            db.add_all([*turns, cross_session_turn, ai_turn])
            db.flush()

            tracked_ids = [
                *(turn.id for turn in turns),
                cross_session_turn.id,
                ai_turn.id,
            ]
            state = {
                "released_event_codes": [
                    "opening_context",
                    "evidence_uncertainty",
                    "stakeholder_conflict",
                    "decision_pressure",
                    "counter_evidence",
                    "integration",
                ],
                "dimension_slots": {
                    DIMENSION_KEY: {
                        "status": "partial",
                        "evidence_turn_ids": tracked_ids,
                        "conflicting_evidence_turn_ids": [],
                    }
                },
                "dimension_opportunity_counts": {DIMENSION_KEY: 2},
                "dimension_opportunity_quality": {DIMENSION_KEY: 25},
                "weak_evidence_turn_ids": {DIMENSION_KEY: []},
                "technical_fallback_count": 0,
                "evidence_timeline": [
                    {
                        "turn_id": turn.id,
                        "observations": [
                            {
                                "dimension_key": DIMENSION_KEY,
                                "behavior_key": f"behavior_{index}",
                                "validity": "valid",
                                "source_turn_id": turn.id,
                                "response_origin": "elicited_evidence",
                                "introduced_by_ai": False,
                                "disposition": "accepted",
                            }
                        ],
                    }
                    for index, turn in enumerate(turns, start=1)
                ],
            }
            assessment.interview_state_json = state
            db.flush()

            scoring = ScoringOutput(
                snapshot_type="final",
                summary="模型原始评分",
                scores=[
                    DimensionScore(
                        dimension_key=DIMENSION_KEY,
                        score=4,
                        assessment_status="scored",
                        confidence=0.9,
                        reason="模型原始理由",
                        evidence=[
                            EvidenceItem(
                                text=turns[2].content,
                                evidence_type="supporting_evidence",
                                dialogue_turn_id=turns[2].id,
                            )
                        ],
                    )
                ],
            )

            adjusted, _quality = EvidenceSufficiencyService(db).apply_scoring(
                assessment,
                scoring,
            )

            result = adjusted.scores[0]
            self.assertIsNone(result.score)
            self.assertEqual(result.assessment_status, "insufficient_evidence")
            self.assertEqual(result.score_kind, "provisional")
            self.assertEqual(result.evidence_sufficiency_index, 90)
            self.assertEqual(result.evidence_sufficiency_level, "high")
            self.assertEqual(len(result.evidence), 2)
            self.assertEqual(
                [item.dialogue_turn_id for item in result.evidence],
                [turns[2].id, turns[0].id],
            )
            self.assertTrue(
                all(item.evidence_type == "weak_evidence" for item in result.evidence)
            )
            self.assertNotIn(
                cross_session_turn.id,
                [item.dialogue_turn_id for item in result.evidence],
            )
            self.assertNotIn(
                ai_turn.id,
                [item.dialogue_turn_id for item in result.evidence],
            )

            context = _context(turns)
            report = build_mock_report_output(context, adjusted)
            dimension_report = report.dimension_reports[0]
            self.assertEqual(
                dimension_report.evidence_quotes,
                [item.text for item in result.evidence],
            )
            self.assertIn("部分证据", dimension_report.strength)
            self.assertIn("证据门槛", dimension_report.suggestion)

            dimension_report.evidence_quotes = ["不是当前会话的伪造引用"]
            validated = validate_report_output(context, adjusted, report)
            self.assertEqual(
                validated.dimension_reports[0].evidence_quotes,
                [item.text for item in result.evidence],
            )

    def test_unobserved_stays_without_evidence_or_report_quotes(self) -> None:
        with self.session_factory() as db:
            assessment = _session({})
            db.add(assessment)
            db.flush()
            turn = _turn(
                assessment.id,
                1,
                "我会先核对交付范围和时间限制。",
            )
            db.add(turn)
            db.flush()
            assessment.interview_state_json = {
                "dimension_slots": {
                    DIMENSION_KEY: {
                        "status": "not_started",
                        "evidence_turn_ids": [turn.id],
                        "conflicting_evidence_turn_ids": [],
                    }
                },
                "dimension_opportunity_counts": {DIMENSION_KEY: 0},
                "dimension_opportunity_quality": {DIMENSION_KEY: 0},
                "weak_evidence_turn_ids": {DIMENSION_KEY: []},
                "evidence_timeline": [],
            }
            db.flush()
            scoring = ScoringOutput(
                snapshot_type="final",
                summary="模型原始评分",
                scores=[
                    DimensionScore(
                        dimension_key=DIMENSION_KEY,
                        score=3,
                        assessment_status="scored",
                        reason="模型原始理由",
                        evidence=[
                            EvidenceItem(
                                text=turn.content,
                                evidence_type="supporting_evidence",
                                dialogue_turn_id=turn.id,
                            )
                        ],
                    )
                ],
            )

            adjusted, _quality = EvidenceSufficiencyService(db).apply_scoring(
                assessment,
                scoring,
            )

            result = adjusted.scores[0]
            self.assertIsNone(result.score)
            self.assertEqual(result.score_kind, "unobserved")
            self.assertEqual(result.evidence, [])

            context = _context([turn])
            report = build_mock_report_output(context, adjusted)
            dimension_report = report.dimension_reports[0]
            self.assertEqual(dimension_report.evidence_quotes, [])
            self.assertIn("公平作答机会", dimension_report.strength)
            dimension_report.evidence_quotes = [turn.content]
            with self.assertRaisesRegex(ValueError, "unobserved"):
                validate_report_output(context, adjusted, report)

    def test_admin_score_kind_uses_dimension_state_with_legacy_fallback(self) -> None:
        provisional_session = _session(
            {
                "dimension_slots": {
                    DIMENSION_KEY: {
                        "status": "partial",
                        "conflicting_evidence_turn_ids": [],
                    }
                },
                "dimension_opportunity_counts": {DIMENSION_KEY: 1},
                "dimension_opportunity_quality": {DIMENSION_KEY: 25},
                "weak_evidence_turn_ids": {DIMENSION_KEY: []},
                "evidence_timeline": [
                    {
                        "turn_id": 1,
                        "observations": [
                            {
                                "dimension_key": DIMENSION_KEY,
                                "behavior_key": "core_problem",
                                "validity": "valid",
                            }
                        ],
                    }
                ],
            }
        )
        unscored_result = SimpleNamespace(score=None)

        self.assertEqual(
            _persisted_score_kind(
                provisional_session,
                unscored_result,
                DIMENSION_KEY,
            ),
            "provisional",
        )
        self.assertEqual(
            _persisted_score_kind(
                _session({}),
                unscored_result,
                DIMENSION_KEY,
            ),
            "unobserved",
        )


if __name__ == "__main__":
    unittest.main()
