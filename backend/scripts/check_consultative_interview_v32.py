from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def events(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines if line.strip()]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="consultative-v32-check-") as temp_dir:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp_dir) / 'check.db'}"
        os.environ["MODEL_GATEWAY_MODE"] = "mock"
        os.environ["INTERVIEW_FLOW_VERSION"] = "progressive_v3_2"
        root = Path(__file__).resolve().parents[1]
        sys.path.extend([str(root), str(Path(__file__).resolve().parent)])

        from sqlalchemy import select

        from app.core.config import get_settings
        from app.core.database import get_engine, get_sessionmaker
        from app.models import Base
        from app.models.agent import AgentTrace
        from app.models.assessment import AssessmentSession
        from app.models.scenario import ScenarioGenerationJob
        from app.schemas.session import (
            CreateSessionRequest,
            ProfileTurnRequest,
            SubmitTurnRequest,
        )
        from app.services.interview_state_service import InterviewStateService
        from app.services.occupation_skeleton_service import (
            OccupationSkeletonService,
            SKELETON_PROTOTYPES,
        )
        from app.services.session_service import SessionService
        from seed_db import seed_database

        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        Base.metadata.create_all(get_engine())
        seed_database(root / "seeds")
        assert len(SKELETON_PROTOTYPES) == 12
        assert all(len(items) >= 2 for items in SKELETON_PROTOTYPES.values())
        student_selection = OccupationSkeletonService._select(
            "学生",
            "大学生",
            {"common_tasks": ["学习任务"], "collaborators": ["老师和同学"]},
        )
        assert student_selection.task_domain == "课程小组作业"
        assert student_selection.user_role == "大学生（参与者）"
        leader_selection = OccupationSkeletonService._select(
            "学生",
            "大学生",
            {"common_tasks": ["我是小组组长，负责安排作业"], "collaborators": ["同学"]},
        )
        assert leader_selection.user_role in {"组长", "负责人"}
        neutral_selection = OccupationSkeletonService._select(
            "待业/退休/其他", "其他", {}
        )
        assert neutral_selection.user_role == "其他（参与者）"

        with get_sessionmaker()() as db:
            service = SessionService(db)
            created = service.create_session(
                CreateSessionRequest(
                    nickname="大学生验收",
                    occupation_category="学生",
                    occupation="大学生",
                    consent_accepted=True,
                    consent_version="critical_thinking_assessment_consent_v1",
                )
            )
            assert created.flow_version == "progressive_v3_2"
            session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == created.session_uuid
                )
            ).scalar_one()
            assert db.execute(
                select(ScenarioGenerationJob).where(
                    ScenarioGenerationJob.session_id == session.id
                )
            ).scalar_one_or_none() is None

            for answer in ("学习任务和课程小组作业", "老师和同学"):
                list(
                    service.stream_profile_turn(
                        created.session_uuid, ProfileTurnRequest(content=answer)
                    )
                )
            prepared = service.get_session(created.session_uuid)
            assert prepared.phase == "opening_pending"
            assert prepared.scenario_preparation.status == "skeleton_ready"
            db.refresh(session)
            scenario = service.repo.get_scenario(session.scenario_id)
            assert scenario is not None and scenario.source_type == "progressive_skeleton"
            blueprint = InterviewStateService.blueprint(scenario)
            assert blueprint is not None
            assert blueprint.schema_version == "occupation_interview_skeleton_v3_2"
            assert blueprint.task_domain in {"课程小组作业", "校园活动协作", "实习选择"}
            assert blueprint.user_role == "大学生（参与者）"
            serialized = "\n".join(
                [blueprint.title, blueprint.core_dilemma]
                + [unit.text for event in blueprint.event_cards for unit in event.presentation_units]
            )
            for forbidden in ("平台运营", "项目协调人", "产品经理"):
                assert forbidden not in serialized

            opening_events = events(
                list(service.stream_start_interview(created.session_uuid))
            )
            opening_complete = next(
                item for item in opening_events if item["event"] == "agent_completed"
            )
            opening_text = opening_complete["ai_turn"]["content"]
            assert opening_complete["duration_ms"] < 8000
            assert "课程小组作业" in opening_text
            assert opening_text.count("？") == 1 and len(opening_text) <= 90
            replay = events(list(service.stream_start_interview(created.session_uuid)))
            replay_complete = next(
                item for item in replay if item["event"] == "agent_completed"
            )
            assert replay_complete["replayed"] is True
            assert replay_complete["ai_turn"]["content"] == opening_text

            request_id = str(uuid4())
            turn_events = events(
                list(
                    service.stream_submit_turn(
                        created.session_uuid,
                        SubmitTurnRequest(
                            client_turn_id=request_id,
                            content="我想先核实这10次记录是否可靠，再看延迟的具体原因。",
                        ),
                    )
                )
            )
            completed = next(
                item for item in turn_events if item["event"] == "agent_completed"
            )
            assert completed["duration_ms"] < 8000
            saved_text = completed["ai_turn"]["content"]
            assert len(saved_text) <= 90
            assert saved_text.count("？") + saved_text.count("?") == 1
            trigger = service.repo.get_user_turn_by_client_id(session.id, request_id)
            assert trigger is not None
            traces = {
                trace.agent_name: trace
                for trace in db.execute(
                    select(AgentTrace).where(
                        AgentTrace.session_id == session.id,
                        AgentTrace.trigger_turn_id == trigger.id,
                    )
                ).scalars()
            }
            assert set(traces) == {"consultative_turn", "interviewer_renderer"}
            core_trace = traces["consultative_turn"]
            renderer_trace = traces["interviewer_renderer"]
            assert core_trace.config_snapshot_json["task_domain"] == blueprint.task_domain
            assert renderer_trace.config_snapshot_json["parent_trace_id"] == core_trace.id
            assert renderer_trace.output_json["message"] == saved_text

            replay_turn = events(
                list(
                    service.stream_submit_turn(
                        created.session_uuid,
                        SubmitTurnRequest(
                            client_turn_id=request_id,
                            content="这次重试不应被保存。",
                        ),
                    )
                )
            )
            replay_turn_complete = next(
                item for item in replay_turn if item["event"] == "agent_completed"
            )
            assert replay_turn_complete["replayed"] is True
            assert replay_turn_complete["ai_turn"]["content"] == saved_text

    print("consultative interview v3.2 check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
