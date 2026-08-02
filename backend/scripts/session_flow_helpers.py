from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update

from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.participant import ParticipantProfile
from app.models.scenario import Scenario, ScenarioGenerationJob, ScenarioStage
from app.schemas.session import CreateSessionRequest, ProfileTurnRequest, SessionResponse
from app.services.scenario_generation_service import ScenarioGenerationService
from app.services.session_service import SessionService


def create_ready_session(
    service: SessionService,
    *,
    nickname: str,
    occupation_category: str = "教育培训",
    occupation: str = "高中教师",
    info_collect_method: str = "ai_dialogue",
    assessment_mode: str = "standard",
    preserve_preparation_records: bool = False,
    use_seeded_scenario: bool = True,
) -> SessionResponse:
    """Create a session and synchronously complete preparation for CLI checks."""

    created = service.create_session(
        CreateSessionRequest(
            nickname=nickname,
            occupation_category=occupation_category,  # type: ignore[arg-type]
            occupation=occupation,
            info_collect_method=info_collect_method,
            assessment_mode=assessment_mode,
            consent_accepted=True,
            consent_version="critical_thinking_assessment_consent_v1",
        )
    )
    session_id = service.get_session_id(created.session_uuid)
    generator = ScenarioGenerationService(service.db)
    consultative_flow = created.flow_version in {
        "progressive_v3_2",
        "progressive_v3_3",
    }
    if not consultative_flow:
        generator.run_base_generation(session_id)

    answers = (
        "我经常安排日常任务，并根据反馈调整计划。",
        "我通常与同事或服务对象协作，熟悉在信息不完整时做判断。",
        "我会比较风险、收益和资源限制，再决定下一步。",
    )
    for answer in answers:
        state = service.get_session(created.session_uuid)
        if state.phase != "onboarding":
            break
        list(
            service.stream_profile_turn(
                created.session_uuid,
                ProfileTurnRequest(content=answer),
            )
        )
        if not consultative_flow:
            generator.finalize_if_ready(session_id)

    if not consultative_flow:
        generator.finalize_if_ready(session_id)
    ready = service.get_session(created.session_uuid)
    if consultative_flow and ready.phase == "opening_pending":
        list(service.stream_start_interview(created.session_uuid))
        ready = service.get_session(created.session_uuid)
    if ready.phase != "assessment":
        raise AssertionError(f"Scenario preparation did not finish: {ready.phase}")
    if use_seeded_scenario:
        session = service.db.get(AssessmentSession, session_id)
        seeded = service.db.execute(
            select(Scenario).where(Scenario.scenario_code == "product_launch_48h")
        ).scalar_one()
        stage = service.db.execute(
            select(ScenarioStage)
            .where(
                ScenarioStage.scenario_id == seeded.id,
                ScenarioStage.status == "active",
            )
            .order_by(ScenarioStage.stage_order)
        ).scalars().first()
        if session is None or stage is None:
            raise AssertionError("Seeded regression scenario is unavailable.")
        service.db.execute(
            update(DialogueTurn)
            .where(DialogueTurn.session_id == session_id)
            .values(source_agent_trace_id=None)
        )
        service.db.execute(
            update(AgentTrace)
            .where(AgentTrace.session_id == session_id)
            .values(trigger_turn_id=None)
        )
        service.db.execute(
            delete(DialogueTurn).where(DialogueTurn.session_id == session_id)
        )
        service.db.execute(delete(AgentTrace).where(AgentTrace.session_id == session_id))
        session.scenario_id = seeded.id
        session.current_stage_id = stage.id
        session.flow_version = "legacy_v2"
        session.interview_state_json = None
        session.state_version = 0
        session.selection_mode = "fixture"
        session.selection_reason = "legacy seeded regression fixture"
        session.status = "in_progress"
        session.started_at = datetime.utcnow()
        participant = service.repo.get_participant(session.participant_id)
        service.db.add(
            DialogueTurn(
                session_id=session_id,
                stage_id=stage.id,
                turn_index=1,
                speaker="ai",
                content=service._build_opening_message(
                    participant.nickname if participant else "受测者",
                    seeded.background,
                    stage.context,
                    stage.main_question,
                ),
                content_type="stage_question",
            )
        )
        service.db.commit()
        ready = service.get_session(created.session_uuid)
    if not preserve_preparation_records:
        # Legacy CLI checks clean sessions with a narrow table list. Remove the
        # new onboarding rows here so their existing cleanup remains valid.
        service.db.execute(
            delete(ScenarioGenerationJob).where(
                ScenarioGenerationJob.session_id == session_id
            )
        )
        service.db.execute(
            delete(ParticipantProfile).where(
                ParticipantProfile.session_id == session_id
            )
        )
        service.db.commit()
    return ready
