from __future__ import annotations

import unittest
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.runtime_interviewer_agent import (
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
)
from app.models import Base
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.participant import Participant
from app.models.scenario import Scenario
from app.services.admin_session_review_service import AdminSessionReviewService


def _reference_kind(key: str) -> str | None:
    lowered = key.lower()
    if lowered in {"trace_id", "trace_ids"} or lowered.endswith(
        ("_trace_id", "_trace_ids")
    ):
        return "trace"
    if lowered in {"turn_id", "turn_ids"} or lowered.endswith(
        ("_turn_id", "_turn_ids")
    ):
        return "turn"
    return None


def _collect_semantic_references(
    value: Any,
    *,
    collected: dict[str, list[Any]],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            kind = _reference_kind(key)
            if kind is not None:
                if isinstance(item, list):
                    collected[kind].extend(item)
                else:
                    collected[kind].append(item)
            _collect_semantic_references(item, collected=collected)
    elif isinstance(value, list):
        for item in value:
            _collect_semantic_references(item, collected=collected)


class HumanisticV11AnonymousExportIdTests(unittest.TestCase):
    def test_nested_trace_references_use_export_opaque_ids(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        with SessionLocal() as db:
            participant = Participant(
                nickname="匿名审计测试",
                info_collect_method="ai_dialogue",
                source="self_assessment",
                status="active",
            )
            scenario = Scenario(
                scenario_code=f"v11-export-{uuid4().hex[:8]}",
                title="v1.1 匿名审计",
                background="验证嵌套来源指针。",
                target_audience="general",
                scenario_type="test",
                difficulty_level="medium",
                estimated_minutes=5,
                rotation_weight=0,
                is_default=False,
                version="v1",
                status="active",
                source_type="test",
                is_immutable=True,
            )
            db.add_all([participant, scenario])
            db.flush()

            assessment = AssessmentSession(
                session_uuid=str(uuid4()),
                participant_id=participant.id,
                scenario_id=scenario.id,
                selection_mode="test",
                status="completed",
                assessment_mode="mock",
                flow_version="legacy_v2",
                interviewer_style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                state_version=2,
            )
            db.add(assessment)
            db.flush()

            preceding_ai = DialogueTurn(
                session_id=assessment.id,
                turn_index=1,
                speaker="ai",
                content="你会先核实哪一类信息？",
                content_type="interview_followup",
            )
            user_turn = DialogueTurn(
                session_id=assessment.id,
                turn_index=2,
                speaker="user",
                content="我会先核实日志来源。",
                content_type="interview_answer",
            )
            db.add_all([preceding_ai, user_turn])
            db.flush()

            provenance = {
                "source_turn_id": user_turn.id,
                "preceding_ai_turn_id": preceding_ai.id,
                "quote": "日志来源",
            }
            planner_trace = AgentTrace(
                session_id=assessment.id,
                trigger_turn_id=user_turn.id,
                agent_name="consultative_turn",
                generation_mode="deterministic",
                ai_generation_weight=0,
                config_snapshot_json={
                    "interviewer_style_version": (
                        HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    ),
                    "evidence_source_turn_id": user_turn.id,
                    "preceding_ai_turn_id": preceding_ai.id,
                    "evidence_provenance": [provenance],
                    "reflection_basis_turn_ids": [user_turn.id],
                },
                input_json={
                    "trigger_turn_id": user_turn.id,
                    "audited_observations": [provenance],
                },
                output_json={
                    "plan": {
                        "reflection_basis_turn_ids": [user_turn.id],
                    }
                },
                status="success",
                model_name="deterministic-planner",
                duration_ms=1,
            )
            db.add(planner_trace)
            db.flush()

            renderer_trace = AgentTrace(
                session_id=assessment.id,
                trigger_turn_id=user_turn.id,
                agent_name="interviewer_renderer",
                generation_mode="deterministic",
                ai_generation_weight=0,
                config_snapshot_json={
                    "parent_trace_id": planner_trace.id,
                    "interviewer_style_version": (
                        HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    ),
                    "humanistic_v1_1_audit": {
                        "reflection_source_quotes": [
                            {
                                "turn_id": user_turn.id,
                                "quote": "我会先核实日志来源",
                            }
                        ]
                    },
                },
                input_json={
                    "validated_plan": {
                        "reflection_basis_turn_ids": [user_turn.id],
                    },
                    "source_agent_trace_id": planner_trace.id,
                    "reflection_source_quotes": [
                        {
                            "turn_id": user_turn.id,
                            "quote": "我会先核实日志来源",
                        }
                    ],
                },
                output_json={
                    "reflection_source_quotes": [
                        {
                            "turn_id": user_turn.id,
                            "quote": "我会先核实日志来源",
                        }
                    ]
                },
                status="success",
                model_name="deterministic-renderer",
                duration_ms=1,
            )
            db.add(renderer_trace)
            db.flush()

            visible_ai = DialogueTurn(
                session_id=assessment.id,
                turn_index=3,
                speaker="ai",
                content="你提到“我会先核实日志来源”；还会怎样判断？",
                content_type="interview_followup",
                source_agent_trace_id=renderer_trace.id,
            )
            db.add(visible_ai)
            db.commit()

            exported = AdminSessionReviewService(db).build_export(
                status_value=None,
                scenario_code=scenario.scenario_code,
                search=None,
                review_status=None,
                low_confidence=False,
                confidence_threshold=0.5,
            )

            turns_by_index = {
                item["turn_index"]: item for item in exported["turns"]
            }
            traces_by_name = {
                item["agent_name"]: item for item in exported["agent_traces"]
            }
            opaque_turn_ids = {
                item["turn_id"] for item in exported["turns"]
            }
            opaque_trace_ids = {
                item["trace_id"] for item in exported["agent_traces"]
            }
            exported_planner = traces_by_name["consultative_turn"]
            exported_renderer = traces_by_name["interviewer_renderer"]

            planner_config = exported_planner["config_snapshot_json"]
            self.assertEqual(
                planner_config["evidence_source_turn_id"],
                turns_by_index[2]["turn_id"],
            )
            self.assertEqual(
                planner_config["preceding_ai_turn_id"],
                turns_by_index[1]["turn_id"],
            )
            self.assertEqual(
                planner_config["evidence_provenance"][0]["source_turn_id"],
                turns_by_index[2]["turn_id"],
            )
            self.assertEqual(
                exported_renderer["config_snapshot_json"]["parent_trace_id"],
                exported_planner["trace_id"],
            )
            self.assertEqual(
                exported_renderer["input_json"]["source_agent_trace_id"],
                exported_planner["trace_id"],
            )
            self.assertEqual(
                exported_renderer["config_snapshot_json"][
                    "humanistic_v1_1_audit"
                ]["reflection_source_quotes"][0]["turn_id"],
                turns_by_index[2]["turn_id"],
            )
            self.assertEqual(
                turns_by_index[3]["source_agent_trace_id"],
                exported_renderer["trace_id"],
            )

            collected: dict[str, list[Any]] = {"turn": [], "trace": []}
            for trace in exported["agent_traces"]:
                for field in (
                    "config_snapshot_json",
                    "input_json",
                    "output_json",
                ):
                    _collect_semantic_references(
                        trace[field],
                        collected=collected,
                    )

            self.assertTrue(collected["turn"])
            self.assertTrue(collected["trace"])
            self.assertTrue(
                all(
                    item is None or item in opaque_turn_ids
                    for item in collected["turn"]
                )
            )
            self.assertTrue(
                all(
                    item is None or item in opaque_trace_ids
                    for item in collected["trace"]
                )
            )
            self.assertFalse(
                any(
                    isinstance(item, int)
                    for items in collected.values()
                    for item in items
                )
            )


if __name__ == "__main__":
    unittest.main()
