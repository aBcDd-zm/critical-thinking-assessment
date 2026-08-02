import json
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import get_sessionmaker
from app.agents.user_turn_intent import (
    analyze_humanistic_authority_request,
    analyze_user_turn,
    build_missing_evidence_question,
    classify_consultative_control_intent,
    is_stage_skip_request,
    is_scoring_analysis,
)
from app.agents.profile_agent import (
    HUMANISTIC_V11_PROFILE_OPENING,
    MAX_PROFILE_QUESTIONS,
    OPENING_PROFILE_QUESTION,
    PROFILE_PROMPT_VERSION,
    ProfileAgent,
)
from app.agents.interview_planner_agent import (
    InterviewPlannerAgent,
    PLANNER_PROMPT_VERSION,
)
from app.agents.humanistic_interviewer_v11 import (
    compose_v11_correction_acknowledgement,
)
from app.agents.runtime_interviewer_agent import (
    BASELINE_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
    INTERVIEWER_RENDER_FAST_RETRY_LIMIT,
    INTERVIEWER_RENDER_PROMPT_VARIANT,
    RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION,
    RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION_V1_1,
    RUNTIME_INTERVIEWER_PROMPT_VERSION,
    InterviewerAgent,
    InterviewerAgentResult,
)
from app.core.runtime_interview_config import get_runtime_interview_settings
from app.agents.consultative_turn_agent import (
    CONSULTATIVE_TURN_PROMPT_VARIANT,
    CONSULTATIVE_TURN_PROMPT_VERSION,
    ConsultativeTurnAgent,
    ConsultativeTurnAgentResult,
)
from app.agents.progressive_schemas import (
    EvidenceObservation,
    InterviewPlanOutput,
    InterviewState,
    InterviewerOutput,
    ReflectionSourceQuote,
)
from app.agents.schemas import DimensionScore
from app.agents import (
    AgentRuntimeContext,
    DialogueTurnContext,
    DynamicInfoContext,
    FollowupAgent,
    FollowupOutput,
    HostAgent,
    HostOutput,
    InterventionRuleContext,
    ParticipantContext,
    ReportAgent,
    RubricAnchorContext,
    RubricDimensionContext,
    ScoringAgent,
    ScenarioContext,
    SessionContext,
    StageDimensionBindingContext,
    StageContext,
)
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.feedback import SessionFeedback
from app.models.participant import ConsentRecord, Participant, ParticipantProfile
from app.models.prompt import PromptTemplate
from app.models.scenario import ScenarioGenerationJob
from app.models.scenario import ScenarioStage
from app.repositories.session_repository import SessionRepository
from app.services.report_pdf_service import ReportPdfService
from app.services.report_service import ReportService
from app.services.runtime_reliability_config import (
    humanistic_renderer_timeout_seconds,
)
from app.services.scoring_service import ScoringService
from app.schemas.session import (
    ContinueStageResponse,
    CreateSessionRequest,
    DialogueTurnItem,
    FinishSessionResponse,
    FeedbackResponse,
    FeedbackStateResponse,
    LanguageModeResponse,
    OnboardingState,
    InterviewProgress,
    PreparationResponse,
    ProfileTurnRequest,
    ReportResponse,
    ReportGenerationResponse,
    ScenarioSummary,
    ScenarioPreparationState,
    SessionResponse,
    SessionProgress,
    SkipStageResponse,
    StageProgressItem,
    StageSummary,
    SubmitFeedbackRequest,
    SubmitTurnRequest,
    SubmitTurnResponse,
    UpdateLanguageModeRequest,
)
from app.services.scenario_generation_service import ScenarioGenerationService
from app.services.scenario_materialization_service import ScenarioMaterializationService
from app.services.evidence_tracker_service import (
    EVIDENCE_POLICY_VERSION,
    EvidenceTrackerService,
)
from app.services.interview_state_service import InterviewStateService
from app.services.occupation_skeleton_service import OccupationSkeletonService
from app.services.evidence_sufficiency_service import EvidenceSufficiencyService


ACTIVE_SESSION_STATUSES = {"created", "in_progress"}
GENERATING_SESSION_STATUS = "generating"
COMPLETED_SESSION_STATUS = "completed"
REPORT_GENERATION_STATE_KEY = "report_generation"
REPORT_GENERATION_MAX_ATTEMPTS = 3
REPORT_GENERATION_ACTIVE_LEASE_SECONDS = 180
logger = logging.getLogger(__name__)

EVENT_PRIMARY_OPPORTUNITY_DIMENSION = {
    "opening_context": "problem_definition",
    "evidence_uncertainty": "evidence_evaluation",
    "stakeholder_conflict": "multiple_perspectives",
    "decision_pressure": "integrative_decision",
    "counter_evidence": "dynamic_adjustment",
    "integration": "integrative_decision",
}
SUPPORTED_INTERVIEWER_STYLES = {
    BASELINE_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
}


def _is_humanistic_interviewer_style(style_version: str) -> bool:
    return style_version in {
        HUMANISTIC_INTERVIEWER_STYLE,
        HUMANISTIC_INTERVIEWER_STYLE_V1_1,
    }


def _is_progressive_flow(flow_version: str) -> bool:
    return flow_version in {"progressive_v3", "progressive_v3_2", "progressive_v3_3"}


def _is_consultative_flow(flow_version: str) -> bool:
    return flow_version in {"progressive_v3_2", "progressive_v3_3"}


def _default_interviewer_style(
    flow_version: str = "progressive_v3_3",
) -> str:
    settings = get_settings()
    configured = settings.INTERVIEWER_STYLE_DEFAULT.strip().lower()
    if (
        flow_version == "progressive_v3_3"
        and settings.INTERVIEWER_STYLE_ENABLED
        and configured in SUPPORTED_INTERVIEWER_STYLES
    ):
        return configured
    return BASELINE_INTERVIEWER_STYLE


def _applied_interviewer_style(session: AssessmentSession) -> str:
    configured = (
        (session.interviewer_style_version or BASELINE_INTERVIEWER_STYLE)
        .strip()
        .lower()
    )
    if (
        session.flow_version == "progressive_v3_3"
        and get_settings().INTERVIEWER_STYLE_ENABLED
        and configured
        in {
            HUMANISTIC_INTERVIEWER_STYLE,
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        }
    ):
        return configured
    return BASELINE_INTERVIEWER_STYLE


class SessionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SessionRepository(db)

    def create_session(self, payload: CreateSessionRequest) -> SessionResponse:
        scenario = ScenarioMaterializationService(self.db).ensure_fallback()
        settings = get_settings()
        flow_version = (
            settings.INTERVIEW_FLOW_VERSION
            if settings.INTERVIEW_FLOW_VERSION
            in {
                "legacy_v2",
                "progressive_v3",
                "progressive_v3_2",
                "progressive_v3_3",
            }
            else "progressive_v3_3"
        )

        participant = Participant(
            nickname=payload.nickname,
            industry=payload.occupation_category,
            career_direction=payload.occupation,
            info_collect_method=payload.info_collect_method,
            raw_basic_info={
                "occupation_category": payload.occupation_category,
                "occupation": payload.occupation,
            },
            source="self_assessment",
            status="active",
        )
        self.db.add(participant)
        self.db.flush()

        session = AssessmentSession(
            session_uuid=str(uuid4()),
            participant_id=participant.id,
            scenario_id=scenario.id,
            current_stage_id=None,
            selection_mode="pending_occupation_adaptation",
            selection_reason="collecting non-scored occupation profile",
            status="onboarding",
            flow_version=flow_version,
            interviewer_style_version=_default_interviewer_style(flow_version),
            assessment_mode=settings.MODEL_GATEWAY_MODE.lower(),
            started_at=None,
        )
        self.db.add(session)
        self.db.flush()

        self.db.add(
            ConsentRecord(
                session_id=session.id,
                consent_status="accepted",
                consent_version=payload.consent_version,
                scope_json={
                    "ai_processing": True,
                    "authorized_expert_review": True,
                    "deidentified_research": True,
                    "psychological_diagnosis": False,
                    "audio_storage": False,
                },
            )
        )

        self.db.add(
            ParticipantProfile(
                session_id=session.id,
                raw_background_answers={"answers": []},
                ai_profile_json={"completed": False},
                population_type=payload.occupation_category,
                adaptation_tags=[payload.occupation],
                profile_version=PROFILE_PROMPT_VERSION,
            )
        )
        if not _is_consultative_flow(session.flow_version):
            ScenarioGenerationService(self.db).create_job(
                session=session,
                category=payload.occupation_category,
                occupation=payload.occupation,
            )

        opening = DialogueTurn(
            session_id=session.id,
            stage_id=None,
            turn_index=1,
            speaker="ai",
            content=(
                f"{participant.nickname}，{HUMANISTIC_V11_PROFILE_OPENING}"
                if _applied_interviewer_style(session)
                == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                else f"{participant.nickname}，你好。{OPENING_PROFILE_QUESTION}"
            ),
            content_type="profile_question",
        )
        self.db.add(opening)
        self.db.commit()
        self.db.refresh(session)

        return self.get_session(session.session_uuid)

    def get_session(self, session_uuid: str) -> SessionResponse:
        session = self._get_session_or_404(session_uuid)
        participant = self.repo.get_participant(session.participant_id)
        scenario = self.repo.get_scenario(session.scenario_id)
        stage = self.repo.get_stage(session.current_stage_id)
        turns = self.repo.list_turns(session.id)
        stages = self.repo.list_active_stages(session.scenario_id)
        onboarding, preparation, phase = self._preparation_states(session, turns)

        if participant is None or scenario is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Session references missing participant or scenario.",
            )

        return SessionResponse(
            session_uuid=session.session_uuid,
            status=session.status,
            flow_version=session.flow_version,
            interviewer_style_version=session.interviewer_style_version,
            language_mode=session.language_mode,
            participant_nickname=participant.nickname,
            scenario=ScenarioSummary(
                scenario_code=scenario.scenario_code,
                title=scenario.title,
                background=scenario.background,
                estimated_minutes=scenario.estimated_minutes,
                version=scenario.version,
                source_type=scenario.source_type,
            ),
            current_stage=(
                StageSummary(
                    stage_code=stage.stage_code,
                    title=stage.title,
                    stage_order=stage.stage_order,
                    context=stage.context,
                    main_question=stage.main_question,
                    max_followups=stage.max_followups,
                )
                if stage and not _is_progressive_flow(session.flow_version)
                else None
            ),
            turns=[
                DialogueTurnItem(
                    turn_index=turn.turn_index,
                    speaker=turn.speaker,
                    content=turn.content,
                    content_type=turn.content_type,
                    created_at=turn.created_at,
                    analysis=(
                        None
                        if _is_progressive_flow(session.flow_version)
                        else turn.analysis_json
                    ),
                )
                for turn in turns
            ],
            progress=(
                self._build_session_progress(
                    session, scenario.estimated_minutes, stage, stages, turns
                )
                if phase in {"assessment", "completed"}
                and not _is_progressive_flow(session.flow_version)
                else None
            ),
            interview_progress=(
                self._build_interview_progress(session)
                if phase in {"assessment", "completed"}
                and _is_progressive_flow(session.flow_version)
                else None
            ),
            phase=phase,
            onboarding=onboarding,
            scenario_preparation=preparation,
        )

    def get_preparation(self, session_uuid: str) -> PreparationResponse:
        session = self._get_session_or_404(session_uuid)
        onboarding, preparation, phase = self._preparation_states(
            session, self.repo.list_turns(session.id)
        )
        return PreparationResponse(
            session_uuid=session_uuid,
            phase=phase,
            onboarding=onboarding,
            scenario_preparation=preparation,
            assessment_ready=phase in {"assessment", "completed"},
        )

    def get_session_id(self, session_uuid: str) -> int:
        return self._get_session_or_404(session_uuid).id

    def stream_profile_turn(
        self,
        session_uuid: str,
        payload: ProfileTurnRequest,
    ) -> Iterator[str]:
        session = self._get_session_or_404(session_uuid)
        if session.status not in {"onboarding", "scenario_preparing"}:
            yield _stream_event(
                "error",
                {"message": "背景访谈已结束。", "detail": "PROFILE_ALREADY_COMPLETED"},
            )
            return
        profile = self.db.execute(
            select(ParticipantProfile).where(
                ParticipantProfile.session_id == session.id
            )
        ).scalar_one_or_none()
        participant = self.repo.get_participant(session.participant_id)
        job = self.db.execute(
            select(ScenarioGenerationJob).where(
                ScenarioGenerationJob.session_id == session.id
            )
        ).scalar_one_or_none()
        if (
            profile is None
            or participant is None
            or (not _is_consultative_flow(session.flow_version) and job is None)
        ):
            yield _stream_event(
                "error",
                {"message": "背景访谈状态不可用。", "detail": "PROFILE_STATE_MISSING"},
            )
            return
        if (profile.ai_profile_json or {}).get("completed"):
            yield _stream_event("profile_completed", {"message": "背景访谈已完成。"})
            return

        user_turn = DialogueTurn(
            session_id=session.id,
            stage_id=None,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="user",
            content=payload.content,
            content_type="profile_answer",
        )
        self.db.add(user_turn)
        self.db.commit()
        self.db.refresh(user_turn)
        yield _stream_event(
            "profile_answer_saved",
            {
                "session_uuid": session_uuid,
                "saved_turn_index": user_turn.turn_index,
                "message": "背景信息已保存。",
            },
        )

        turns = self.repo.list_turns(session.id)
        answers = [
            turn.content for turn in turns if turn.content_type == "profile_answer"
        ]
        question_count = sum(
            1 for turn in turns if turn.content_type == "profile_question"
        )
        yield _stream_event("profile_agent_started", {"message": "正在理解你熟悉的任务与协作方式。"})
        prompt = (
            self.db.execute(
                select(PromptTemplate)
                .where(
                    PromptTemplate.agent_name == "profile",
                    PromptTemplate.status == "active",
                )
                .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
            )
            .scalars()
            .first()
        )
        started = perf_counter()
        result = ProfileAgent().respond(
            occupation_category=participant.industry or "待业/退休/其他",
            occupation=participant.career_direction or "当前身份",
            answers=answers,
            question_count=question_count,
            template_content=prompt.content if prompt else None,
            style_version=_applied_interviewer_style(session),
        )
        duration_ms = int((perf_counter() - started) * 1000)
        if job is not None:
            job.profile_call_count = min(
                job.profile_call_count + 1, MAX_PROFILE_QUESTIONS
            )
        profile.raw_background_answers = {"answers": answers}
        profile.ai_profile_json = {
            **result.output.profile.model_dump(),
            "completed": result.output.next_action == "complete",
        }
        trace = AgentTrace(
            session_id=session.id,
            stage_id=None,
            trigger_turn_id=user_turn.id,
            prompt_template_id=prompt.id if prompt else None,
            agent_name="profile",
            generation_mode=get_settings().MODEL_GATEWAY_MODE.lower(),
            ai_generation_weight=100,
            config_snapshot_json={
                "prompt_version": PROFILE_PROMPT_VERSION,
                "max_questions": MAX_PROFILE_QUESTIONS,
                "excluded_from_scoring": True,
            },
            input_json={
                "occupation_category": participant.industry,
                "occupation": participant.career_direction,
                "answer_count": len(answers),
            },
            output_json=result.output.model_dump(),
            raw_output=result.raw_output or result.error_reason,
            status="success" if result.success else "fallback",
            error_code=result.error_code,
            model_name=result.model_name,
            duration_ms=max(duration_ms, 0),
        )
        self.db.add(trace)
        self.db.flush()
        ai_turn = DialogueTurn(
            session_id=session.id,
            stage_id=None,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="ai",
            content=result.output.message,
            content_type=(
                "profile_completed"
                if result.output.next_action == "complete"
                else "profile_question"
            ),
            source_agent_trace_id=trace.id,
        )
        self.db.add(ai_turn)
        if result.output.next_action == "complete":
            if _is_consultative_flow(session.flow_version):
                OccupationSkeletonService(self.db).prepare(
                    session, participant, profile
                )
            else:
                session.status = "scenario_preparing"
        self.db.commit()
        self.db.refresh(ai_turn)
        event_name = (
            "profile_completed"
            if result.output.next_action == "complete"
            else "profile_agent_completed"
        )
        yield _stream_event(
            event_name,
            {
                "session_uuid": session_uuid,
                "message": result.output.message,
                "ai_turn": {
                    "turn_index": ai_turn.turn_index,
                    "speaker": ai_turn.speaker,
                    "content": ai_turn.content,
                    "content_type": ai_turn.content_type,
                    "created_at": ai_turn.created_at.isoformat(),
                },
            },
        )

    def stream_start_interview(self, session_uuid: str) -> Iterator[str]:
        session = self._get_session_or_404(session_uuid)
        if not _is_consultative_flow(session.flow_version):
            yield _stream_event(
                "error",
                {"message": "当前会话不使用 v3.2 开场流。", "detail": "FLOW_NOT_V32"},
            )
            return
        existing = next(
            (
                turn
                for turn in self.repo.list_turns(session.id)
                if turn.speaker == "ai" and turn.content_type == "interview_opening"
            ),
            None,
        )
        if existing is not None:
            yield _stream_event(
                "agent_delta", {"delta": existing.content, "replayed": True}
            )
            yield _stream_event(
                "agent_completed",
                {
                    "session_uuid": session_uuid,
                    "duration_ms": 0,
                    "replayed": True,
                    "next_action": "wait_user_answer",
                    "ai_turn": self._turn_payload(existing),
                },
            )
            return
        if not self.repo.try_mark_opening_generating(session_uuid):
            self.db.rollback()
            yield _stream_event(
                "error",
                {
                    "message": "开场正在生成或当前状态不允许开始。",
                    "detail": "OPENING_NOT_READY",
                },
            )
            return
        self.db.commit()
        yield _stream_event(
            "agent_started",
            {"message": "罗杰斯教授正在开始第一轮对话。"},
        )
        try:
            session = self._get_session_or_404(session_uuid)
            participant = self.repo.get_participant(session.participant_id)
            scenario = self.repo.get_scenario(session.scenario_id)
            if participant is None or scenario is None:
                raise ValueError("v3.2 opening context is incomplete")
            blueprint = InterviewStateService.blueprint(scenario)
            if blueprint is None:
                raise ValueError("v3.2 skeleton is missing")
            state = InterviewStateService.load(session, scenario)
            context = self._build_agent_context(session, None)
            style_version = _applied_interviewer_style(session)
            consultative_agent = ConsultativeTurnAgent()
            if _is_humanistic_interviewer_style(style_version):
                opening_output = consultative_agent.fallback(
                    context,
                    state,
                    blueprint,
                    opening=True,
                    nickname=participant.nickname,
                )
                result = ConsultativeTurnAgentResult(
                    output=opening_output,
                    raw_output=opening_output.model_dump_json(),
                    model_name="deterministic-opening-plan-v1",
                    duration_ms=0,
                    model_attempt_count=0,
                )
                model_call_status = "not_called"
            else:
                result = consultative_agent.generate(
                    context,
                    state,
                    blueprint,
                    opening=True,
                    nickname=participant.nickname,
                )
                model_call_status = (
                    "not_called"
                    if result.model_attempt_count == 0
                    else "success"
                    if result.status == "ok"
                    else "failed"
                )
            errors = ConsultativeTurnAgent().validate_opening(
                result.output.interviewer,
                blueprint,
                participant_nickname=participant.nickname,
            )
            opening_repair_applied = False
            if errors:
                fallback = ConsultativeTurnAgent().fallback(
                    context,
                    state,
                    blueprint,
                    opening=True,
                    nickname=participant.nickname,
                )
                result.output = fallback.model_copy(
                    update={
                        "interviewer": fallback.interviewer.model_copy(
                            update={
                                "fallback_used": model_call_status == "failed",
                                "warnings": [
                                    (
                                        f"quality gate: {','.join(errors)}"
                                        if model_call_status == "failed"
                                        else "opening contract repaired with deterministic renderer"
                                    )
                                ],
                            }
                        )
                    }
                )
                result.validation_errors = errors
                if model_call_status == "success":
                    opening_repair_applied = True
                else:
                    result.status = "failed"
                    result.error_code = "OPENING_QUALITY_FALLBACK"
                    result.fallback_type = "deterministic_opening"
            unit = blueprint.event_cards[0].presentation_units[0]
            trace = AgentTrace(
                session_id=session.id,
                stage_id=session.current_stage_id,
                trigger_turn_id=None,
                agent_name="consultative_turn",
                generation_mode=(
                    get_settings().MODEL_GATEWAY_MODE.lower()
                    if result.model_attempt_count
                    else "deterministic"
                ),
                ai_generation_weight=100 if result.model_attempt_count else 0,
                config_snapshot_json={
                    "prompt_version": CONSULTATIVE_TURN_PROMPT_VERSION,
                    "prompt_variant": CONSULTATIVE_TURN_PROMPT_VARIANT,
                    "measurement_scope": "opening",
                    "measurement_source": "deterministic_measurement_core_v1",
                    "evidence_source": "not_applicable",
                    "measurement_core_status": (
                        "failed" if result.status != "ok" else "success"
                    ),
                    "flow_version": session.flow_version,
                    "interviewer_style_version": style_version,
                    "configured_style_version": session.interviewer_style_version,
                    "action": "OPENING",
                    "task_domain": blueprint.task_domain,
                    "identity_constraints": state.identity_constraints,
                    "fact_code": unit.unit_code,
                    "validation_errors": result.validation_errors or errors,
                    "model_call_status": model_call_status,
                    "model_attempt_count": result.model_attempt_count,
                    "transport_retry_limit": 0,
                    "transport_retry_reason": result.retry_reason,
                    "opening_repair_applied": opening_repair_applied,
                    "visible_renderer": (
                        "independent_humanistic_renderer"
                        if _is_humanistic_interviewer_style(style_version)
                        else "deterministic_opening_repair"
                        if opening_repair_applied
                        else "deterministic_opening"
                        if result.model_attempt_count == 0
                        else "model"
                        if result.status == "ok"
                        else "deterministic_fallback"
                    ),
                    "timeout_ms": state.turn_latency_budget_ms,
                },
                input_json={
                    "state": state.model_dump(mode="json"),
                    "opening": True,
                },
                output_json=result.output.model_dump(mode="json"),
                raw_output=result.raw_output,
                status="success" if result.status == "ok" else "fallback",
                error_code=result.error_code,
                fallback_type=result.fallback_type,
                model_name=result.model_name,
                duration_ms=max(result.duration_ms, 0),
            )
            self.db.add(trace)
            self.db.flush()
            visible_output = result.output.interviewer
            visible_trace = trace
            if _is_humanistic_interviewer_style(style_version):
                visible_output = InterviewerAgent().render_opening(
                    blueprint,
                    participant.nickname,
                    style_version=style_version,
                )
                renderer_errors = consultative_agent.validate_opening(
                    visible_output,
                    blueprint,
                    participant_nickname=participant.nickname,
                    enforce_humanistic_safety=True,
                )
                if renderer_errors:
                    raise ValueError(
                        "Humanistic opening renderer failed closed: "
                        + ",".join(renderer_errors)
                    )
                renderer_trace = AgentTrace(
                    session_id=session.id,
                    stage_id=session.current_stage_id,
                    trigger_turn_id=None,
                    prompt_template_id=None,
                    agent_name="interviewer_renderer",
                    generation_mode="deterministic",
                    ai_generation_weight=0,
                    config_snapshot_json={
                        "prompt_version": (
                            "deterministic_humanistic_opening_v1_1"
                            if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                            else "deterministic_humanistic_opening_v1"
                        ),
                        "flow_version": session.flow_version,
                        "parent_trace_id": trace.id,
                        "configured_style_version": session.interviewer_style_version,
                        "interviewer_style_version": style_version,
                        "action": "OPENING",
                        "release_event_code": "opening_context",
                        "release_unit_code": unit.unit_code,
                        "validation_codes": [],
                        "fallback_reason": None,
                        "timeout_ms": 0,
                        "model_attempt_count": 0,
                        "model_call_status": "not_called",
                        "single_model_attempt": True,
                    },
                    input_json={
                        "validated_plan": {
                            "action": "OPENING",
                            "release_event_code": "opening_context",
                            "release_unit_code": unit.unit_code,
                            "question_intent": "邀请用户先说明最想确认的一点",
                        },
                        "allowed_facts": [
                            {"unit_code": unit.unit_code, "text": unit.text}
                        ],
                        "specified_user_turn": None,
                        "recent_visible_messages": [],
                        "reflection_source_turn_ids": [],
                    },
                    output_json=visible_output.model_dump(mode="json"),
                    raw_output=None,
                    status="success",
                    error_code=None,
                    fallback_type=None,
                    model_name="deterministic-humanistic-opening-v1",
                    duration_ms=0,
                )
                self.db.add(renderer_trace)
                self.db.flush()
                visible_trace = renderer_trace
            ai_turn = DialogueTurn(
                session_id=session.id,
                stage_id=session.current_stage_id,
                turn_index=self.repo.next_turn_index(session.id),
                speaker="ai",
                content=visible_output.message,
                content_type="interview_opening",
                source_agent_trace_id=visible_trace.id,
            )
            self.db.add(ai_turn)
            state.opening_status = "saved"
            if "opening_context" not in state.released_event_codes:
                state.released_event_codes.append("opening_context")
            if unit.unit_code not in state.released_unit_codes:
                state.released_unit_codes.append(unit.unit_code)
            InterviewStateService.save(session, state)
            session.status = "in_progress"
            session.started_at = session.started_at or datetime.utcnow()
            self.db.commit()
            self.db.refresh(ai_turn)
            yield _stream_event(
                "agent_delta", {"delta": ai_turn.content, "replayed": False}
            )
            yield _stream_event(
                "agent_completed",
                {
                    "session_uuid": session_uuid,
                    "duration_ms": result.duration_ms,
                    "replayed": False,
                    "next_action": "wait_user_answer",
                    "ai_turn": self._turn_payload(ai_turn),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            session = self.repo.get_session_by_uuid(session_uuid)
            if session is not None and session.status == GENERATING_SESSION_STATUS:
                session.status = "opening_pending"
                self.db.commit()
            yield _stream_event(
                "error",
                {"message": "开场生成失败，请重试。", "detail": str(exc)},
            )

    def submit_turn(
        self,
        session_uuid: str,
        payload: SubmitTurnRequest,
    ) -> SubmitTurnResponse:
        existing_session = self._get_session_or_404(session_uuid)
        duplicate = self.repo.get_user_turn_by_client_id(
            existing_session.id, str(payload.client_turn_id)
        )
        if duplicate is not None:
            completed_turn = self.repo.get_interviewer_turn_for_trigger(
                existing_session.id, duplicate.id
            )
            if completed_turn is not None:
                return SubmitTurnResponse(
                    session_uuid=session_uuid,
                    saved_turn_index=duplicate.turn_index,
                    next_action=(
                        "generate_report"
                        if existing_session.status == COMPLETED_SESSION_STATUS
                        else "wait_user_answer"
                    ),
                    message="Previously saved result replayed.",
                    replayed=True,
                )
        session = self._begin_turn_generation_or_409(session_uuid)
        try:
            turn = duplicate or DialogueTurn(
                session_id=session.id,
                stage_id=session.current_stage_id,
                turn_index=self.repo.next_turn_index(session.id),
                speaker="user",
                content=payload.content,
                content_type=payload.content_type,
                client_turn_id=str(payload.client_turn_id),
                answer_duration_ms=payload.answer_duration_ms,
            )
            if duplicate is None:
                self.db.add(turn)
                self.db.flush()

            if _is_progressive_flow(session.flow_version):
                if duplicate is None:
                    self.db.commit()
                    self.db.refresh(turn)
                    session = self._get_session_or_404(session_uuid)
                processor = (
                    self._process_consultative_turn
                    if _is_consultative_flow(session.flow_version)
                    else self._process_progressive_turn
                )
                _, next_action, _ = processor(session, turn)
                self._release_generation_status(session)
                self.db.commit()
                self.db.refresh(turn)
                return SubmitTurnResponse(
                    session_uuid=session.session_uuid,
                    saved_turn_index=turn.turn_index,
                    next_action=next_action,
                    message="User turn and progressive interview response saved.",
                    replayed=duplicate is not None,
                )

            skip_result = self._handle_typed_stage_skip(session, turn)
            if skip_result is not None:
                next_action, _ = skip_result
                self._release_generation_status(session)
                self.db.commit()
                self.db.refresh(turn)
                return SubmitTurnResponse(
                    session_uuid=session.session_uuid,
                    saved_turn_index=turn.turn_index,
                    next_action=next_action,
                    message="User requested a stage transition.",
                )

            context = self._prepare_user_turn_context(session, turn)
            followup_output, trace = self._generate_followup(session, turn, context)
            next_action = self._persist_followup_result(
                session=session,
                trigger_turn=turn,
                output=followup_output,
                trace=trace,
            )
            self._release_generation_status(session)

            self.db.commit()
            self.db.refresh(turn)
        except Exception:
            self.db.rollback()
            self._reset_generation_status(session_uuid)
            raise

        return SubmitTurnResponse(
            session_uuid=session.session_uuid,
            saved_turn_index=turn.turn_index,
            next_action=next_action,
            message="User turn saved and dialogue agent response generated.",
        )

    def stream_submit_turn(
        self,
        session_uuid: str,
        payload: SubmitTurnRequest,
    ) -> Iterator[str]:
        generation_started = False
        try:
            existing_session = self._get_session_or_404(session_uuid)
            duplicate = self.repo.get_user_turn_by_client_id(
                existing_session.id, str(payload.client_turn_id)
            )
            if duplicate is not None:
                completed_turn = self.repo.get_interviewer_turn_for_trigger(
                    existing_session.id, duplicate.id
                )
                if completed_turn is not None:
                    yield _stream_event(
                        "user_turn_saved",
                        {
                            "session_uuid": session_uuid,
                            "saved_turn_index": duplicate.turn_index,
                            "message": "已识别重复提交，正在重放已保存结果。",
                            "replayed": True,
                        },
                    )
                    yield _stream_event(
                        "agent_delta",
                        {"delta": completed_turn.content, "replayed": True},
                    )
                    yield _stream_event(
                        "agent_completed",
                        {
                            "session_uuid": session_uuid,
                            "saved_turn_index": duplicate.turn_index,
                            "next_action": (
                                "generate_report"
                                if existing_session.status == COMPLETED_SESSION_STATUS
                                else "wait_user_answer"
                            ),
                            "duration_ms": 0,
                            "replayed": True,
                            "ai_turn": self._turn_payload(completed_turn),
                        },
                    )
                    return
            session = self._begin_turn_generation_or_409(session_uuid)
            generation_started = True

            turn = duplicate or DialogueTurn(
                session_id=session.id,
                stage_id=session.current_stage_id,
                turn_index=self.repo.next_turn_index(session.id),
                speaker="user",
                content=payload.content,
                content_type=payload.content_type,
                client_turn_id=str(payload.client_turn_id),
                answer_duration_ms=payload.answer_duration_ms,
            )
            if duplicate is None:
                self.db.add(turn)
            self.db.commit()
            self.db.refresh(turn)

            yield _stream_event(
                "user_turn_saved",
                {
                    "session_uuid": session.session_uuid,
                    "saved_turn_index": turn.turn_index,
                    "message": "回答已保存，正在生成下一轮追问。",
                    "replayed": duplicate is not None,
                },
            )

            session = self._get_session_or_404(session_uuid)
            if _is_progressive_flow(session.flow_version):
                yield _stream_event(
                    "agent_started",
                    {"message": "罗杰斯教授正在结合你的回答决定下一步。"},
                )
                processor = (
                    self._process_consultative_turn
                    if _is_consultative_flow(session.flow_version)
                    else self._process_progressive_turn
                )
                ai_turn, next_action, duration_ms = processor(session, turn)
                self._release_generation_status(session)
                self.db.commit()
                yield _stream_event(
                    "agent_delta",
                    {"delta": ai_turn.content, "replayed": duplicate is not None},
                )
                yield _stream_event(
                    "agent_completed",
                    {
                        "session_uuid": session.session_uuid,
                        "saved_turn_index": turn.turn_index,
                        "next_action": next_action,
                        "duration_ms": duration_ms,
                        "replayed": duplicate is not None,
                        "ai_turn": self._turn_payload(ai_turn),
                    },
                )
                return
            skip_result = self._handle_typed_stage_skip(session, turn)
            if skip_result is not None:
                next_action, message = skip_result
                self._release_generation_status(session)
                self.db.commit()
                latest_ai_turn = next(
                    (
                        item
                        for item in reversed(self.repo.list_turns(session.id))
                        if item.speaker == "ai"
                    ),
                    None,
                )
                yield _stream_event(
                    "agent_completed",
                    {
                        "session_uuid": session.session_uuid,
                        "saved_turn_index": turn.turn_index,
                        "next_action": next_action,
                        "duration_ms": 0,
                        "message": message,
                        "ai_turn": (
                            {
                                "turn_index": latest_ai_turn.turn_index,
                                "speaker": latest_ai_turn.speaker,
                                "content": latest_ai_turn.content,
                                "content_type": latest_ai_turn.content_type,
                                "created_at": latest_ai_turn.created_at.isoformat(),
                            }
                            if latest_ai_turn
                            else None
                        ),
                    },
                )
                return

            context = self._prepare_user_turn_context(session, turn)
            yield _stream_event(
                "agent_started",
                {"message": "罗杰斯教授正在结合情境和历史对话准备下一问。"},
            )

            output, status_value, error_code, raw_output, duration_ms = yield from (
                self._stream_followup_agent(context)
            )
            self._apply_model_resolution(session, turn, output)

            trace = self._save_agent_trace(
                session=session,
                stage_id=session.current_stage_id,
                trigger_turn_id=turn.id,
                agent_name="followup",
                input_json=context.model_dump(mode="json"),
                output_json=output.model_dump(mode="json"),
                generation_mode=output.generation_mode,
                ai_generation_weight=output.ai_generation_weight,
                status_value=status_value,
                error_code=error_code,
                raw_output=raw_output,
                duration_ms=duration_ms,
                selected_rule_code=output.selected_rule_code,
                selected_dynamic_info_code=output.selected_dynamic_info_code,
            )
            next_action = self._persist_followup_result(
                session=session,
                trigger_turn=turn,
                output=output,
                trace=trace,
            )
            self._release_generation_status(session)
            self.db.commit()

            latest_ai_turn = next(
                (
                    item
                    for item in reversed(self.repo.list_turns(session.id))
                    if item.speaker == "ai"
                ),
                None,
            )
            yield _stream_event(
                "agent_completed",
                {
                    "session_uuid": session.session_uuid,
                    "saved_turn_index": turn.turn_index,
                    "next_action": next_action,
                    "duration_ms": duration_ms,
                    "ai_turn": (
                        {
                            "turn_index": latest_ai_turn.turn_index,
                            "speaker": latest_ai_turn.speaker,
                            "content": latest_ai_turn.content,
                            "content_type": latest_ai_turn.content_type,
                            "created_at": latest_ai_turn.created_at.isoformat(),
                        }
                        if latest_ai_turn
                        else None
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            if generation_started:
                self._reset_generation_status(session_uuid)
            yield _stream_event(
                "error",
                {
                    "message": "回答提交或下一问生成失败。",
                    "detail": str(exc),
                },
            )

    def finish_session(self, session_uuid: str) -> FinishSessionResponse:
        session = self._get_session_or_404(session_uuid)
        if session.status == GENERATING_SESSION_STATUS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="AI response is still being generated. Please wait before finishing.",
            )
        if session.status == COMPLETED_SESSION_STATUS:
            completed_at = session.completed_at or datetime.utcnow()
            return FinishSessionResponse(
                session_uuid=session.session_uuid,
                status=session.status,
                completed_at=completed_at,
            )
        if session.status not in ACTIVE_SESSION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Formal assessment has not started yet.",
            )

        if _is_progressive_flow(session.flow_version):
            self._block_unresolved_progressive_slots(
                session, reason="user_ended_interview_before_sufficient_evidence"
            )
        self._mark_session_completed(session)

        self.db.commit()

        return FinishSessionResponse(
            session_uuid=session.session_uuid,
            status=session.status,
            completed_at=session.completed_at or datetime.utcnow(),
        )

    def skip_current_stage(self, session_uuid: str) -> SkipStageResponse:
        session = self._get_session_or_404(session_uuid)
        if session.status == GENERATING_SESSION_STATUS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="AI response is still being generated. Please wait before skipping.",
            )
        if session.status not in ACTIVE_SESSION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Session status does not allow stage skipping: {session.status}",
            )
        stage = self.repo.get_stage(session.current_stage_id)
        if stage is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Current stage is not available.",
            )
        stage_turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if turn.stage_id == stage.id
        ]
        if not self._stage_can_skip(stage, stage_turns):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The current stage can be skipped after two clarifications or when evidence remains incomplete at the follow-up limit.",
            )
        if any(turn.content_type == "stage_skipped" for turn in stage_turns):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The current stage has already been skipped.",
            )

        next_action, message = self._perform_stage_skip(
            session,
            stage,
            source="stage_action",
        )
        self.db.commit()
        return SkipStageResponse(
            session_uuid=session.session_uuid,
            next_action=next_action,
            message=message,
        )

    @staticmethod
    def _stage_can_skip(
        stage: ScenarioStage,
        stage_turns: list[DialogueTurn],
    ) -> bool:
        formal_followups = sum(
            1
            for turn in stage_turns
            if turn.speaker == "ai"
            and turn.content_type in {"followup_question", "dynamic_info_question"}
        )
        clarification_count = sum(
            1
            for turn in stage_turns
            if turn.speaker == "ai" and turn.content_type == "clarification_response"
        )
        waiting_for_choice = bool(
            stage_turns and stage_turns[-1].content_type == "stage_incomplete_prompt"
        )
        coverage = SessionService._stage_evidence_coverage(stage, stage_turns)
        has_missing_evidence = any(value != "complete" for value in coverage.values())
        return has_missing_evidence and (
            formal_followups >= stage.max_followups
            or clarification_count >= 2
            or waiting_for_choice
        )

    def _perform_stage_skip(
        self,
        session: AssessmentSession,
        stage: ScenarioStage,
        *,
        source: str,
    ) -> tuple[str, str]:
        stage_turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if turn.stage_id == stage.id
        ]
        coverage = self._stage_evidence_coverage(stage, stage_turns)
        missing = [key for key, value in coverage.items() if value != "complete"]
        skip_turn = DialogueTurn(
            session_id=session.id,
            stage_id=stage.id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="system",
            content="用户选择进入下一阶段。",
            content_type="stage_skipped",
            analysis_json={
                "stage_transition": {
                    "reason": "user_navigation",
                    "source": source,
                    "evidence_coverage": coverage,
                    "missing_evidence": missing,
                }
            },
        )
        self.db.add(skip_turn)
        self.db.flush()

        next_stage = self._advance_to_next_stage(session, skip_turn)
        if next_stage is None:
            self._mark_session_completed(session)
            return "generate_report", "已进入下一步，测评结束并开始生成报告。"
        return (
            "wait_user_answer",
            f"进入“{next_stage.title}”。",
        )

    def _handle_typed_stage_skip(
        self,
        session: AssessmentSession,
        trigger_turn: DialogueTurn,
    ) -> tuple[str, str] | None:
        if not is_stage_skip_request(trigger_turn.content):
            return None
        stage = self.repo.get_stage(session.current_stage_id)
        if stage is None:
            return None
        stage_turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if turn.stage_id == stage.id
        ]
        if not self._stage_can_skip(stage, stage_turns):
            return None
        coverage = self._stage_evidence_coverage(stage, stage_turns)
        missing = [key for key, value in coverage.items() if value != "complete"]
        trigger_turn.analysis_json = {
            "intent": "stage_navigation",
            "response_category": "redirect",
            "navigation_intent": "skip_stage",
            "navigation_executed": True,
            "stage_transition": {
                "reason": "user_navigation",
                "source": "typed_navigation",
                "evidence_coverage": coverage,
                "missing_evidence": missing,
            },
        }
        self.db.flush()
        return self._perform_stage_skip(
            session,
            stage,
            source="typed_navigation",
        )

    def continue_current_stage(self, session_uuid: str) -> ContinueStageResponse:
        session = self._get_session_or_404(session_uuid)
        if session.status not in ACTIVE_SESSION_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Session is not ready for continued input.",
            )
        stage = self.repo.get_stage(session.current_stage_id)
        if stage is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Current stage is not available.",
            )
        stage_turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if turn.stage_id == stage.id
        ]
        if not self._stage_can_skip(stage, stage_turns):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The stage is not waiting for a continue-or-skip choice.",
            )

        coverage = self._stage_evidence_coverage(stage, stage_turns)
        gaps = [key for key, value in coverage.items() if value != "complete"]
        if not gaps:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The stage has no missing evidence.",
            )
        audit_turn = DialogueTurn(
            session_id=session.id,
            stage_id=stage.id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="system",
            content="用户选择继续补充当前阶段。",
            content_type="stage_continue",
        )
        self.db.add(audit_turn)
        self.db.flush()
        question_turn = DialogueTurn(
            session_id=session.id,
            stage_id=stage.id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="ai",
            content=build_missing_evidence_question(stage.stage_code, gaps[0]),
            content_type="supplement_question",
        )
        self.db.add(question_turn)
        self.db.commit()
        return ContinueStageResponse(
            session_uuid=session.session_uuid,
            next_action="wait_user_answer",
            message=f"请继续补充：{gaps[0]}",
        )

    def update_language_mode(
        self, session_uuid: str, payload: UpdateLanguageModeRequest
    ) -> LanguageModeResponse:
        session = self._get_session_or_404(session_uuid)
        session.language_mode = payload.language_mode
        self.db.commit()
        return LanguageModeResponse(
            session_uuid=session.session_uuid,
            language_mode=session.language_mode,
        )

    @staticmethod
    def _opportunity_target_for_answer(
        state: InterviewState,
    ) -> str | None:
        """Return the target of the question already shown to the user."""
        prior_plan = state.last_plan or {}
        candidates = (
            EVENT_PRIMARY_OPPORTUNITY_DIMENSION.get(
                prior_plan.get("release_event_code")
            ),
            prior_plan.get("target_dimension"),
        )
        for candidate in candidates:
            if candidate in state.dimension_slots:
                return candidate
        return None

    def _process_consultative_turn(
        self,
        session: AssessmentSession,
        turn: DialogueTurn,
    ) -> tuple[DialogueTurn, str, int]:
        scenario = self.repo.get_scenario(session.scenario_id)
        participant = self.repo.get_participant(session.participant_id)
        if scenario is None or participant is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="v3.2 consultative context is missing.",
            )
        blueprint = InterviewStateService.blueprint(scenario)
        expected_schema = (
            "occupation_interview_skeleton_v3_3"
            if session.flow_version == "progressive_v3_3"
            else "occupation_interview_skeleton_v3_2"
        )
        if blueprint is None or blueprint.schema_version != expected_schema:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="v3.2 interview skeleton is missing.",
            )
        state = InterviewStateService.load(session, scenario)
        context = self._build_agent_context(session, turn)
        style_version = _applied_interviewer_style(session)
        session_turns = self.repo.list_turns(session.id)
        preceding_ai_turn = next(
            (
                item
                for item in reversed(session_turns)
                if item.speaker == "ai" and item.turn_index < turn.turn_index
            ),
            None,
        )
        earlier_user_turns = [
            item
            for item in session_turns
            if item.speaker == "user" and item.turn_index < turn.turn_index
        ]
        authority_request = (
            analyze_humanistic_authority_request(turn.content)
            if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
            else None
        )
        mixed_authority_request = bool(
            authority_request is not None
            and authority_request.kind == "mixed"
            and authority_request.substantive_text
        )
        pure_authority_request = bool(
            authority_request is not None and authority_request.kind == "pure"
        )
        authority_substantive_text = (
            authority_request.substantive_text
            if authority_request is not None
            else None
        )
        authority_substantive_fragments = (
            list(authority_request.substantive_fragments)
            if authority_request is not None
            else []
        )
        authority_removed_spans = (
            list(authority_request.authority_spans)
            if authority_request is not None
            else []
        )
        state_before = state.model_dump(mode="json")
        agent = ConsultativeTurnAgent()
        routed = (
            agent.route_repair(
                context,
                state,
                blueprint,
                include_low_information=(
                    style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                ),
            )
            if session.flow_version == "progressive_v3_3"
            and not mixed_authority_request
            else None
        )
        result = routed or agent.generate(
            context, state, blueprint, nickname=participant.nickname
        )
        if result.output.plan is None:
            fallback = agent.fallback(context, state, blueprint)
            raw_plan = fallback.plan
            result.status = "failed"
            result.error_code = "MISSING_TURN_PLAN"
            result.fallback_type = "deterministic_consultative_turn"
        else:
            raw_plan = result.output.plan
        if raw_plan is None:
            raise ValueError("consultative fallback did not produce a plan")
        if mixed_authority_request:
            latest_user_turn = context.latest_user_turn
            substantive_text = authority_request.substantive_text
            if latest_user_turn is None or not substantive_text:
                raise ValueError("mixed authority request lost its substantive span")
            measurement_latest_turn = latest_user_turn.model_copy(
                update={"content": substantive_text}
            )
            measurement_history = [
                (
                    item.model_copy(update={"content": substantive_text})
                    if item.turn_id == latest_user_turn.turn_id
                    else item
                )
                for item in context.dialogue_history
            ]
            measurement_context = context.model_copy(
                update={
                    "latest_user_turn": measurement_latest_turn,
                    "dialogue_history": measurement_history,
                }
            )
            raw_plan = (
                InterviewPlannerAgent()
                .build_deterministic_plan(
                    measurement_context,
                    state,
                    blueprint,
                )
                .model_copy(
                    update={
                        "warnings": [
                            *raw_plan.warnings,
                            "mixed authority request; autonomy boundary required",
                        ]
                    }
                )
            )
        model_call_status = (
            "not_called"
            if routed is not None or result.model_attempt_count == 0
            else "success"
            if result.status == "ok"
            else "failed"
        )

        control_intent = classify_consultative_control_intent(turn.content)
        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1:
            if mixed_authority_request:
                control_intent = None
            elif authority_request is not None:
                control_intent = "boundary_redirect"
        if pure_authority_request:
            raw_plan = raw_plan.model_copy(
                update={
                    "response_intent": "redirect",
                    "action": "CLARIFY",
                    "active_topic": "自主判断标准",
                    "target_dimension": None,
                    "target_evidence": None,
                    "release_event_code": None,
                    "release_unit_code": None,
                    "delivery_mode": "clarification",
                    "question_intent": ("说明不能替用户作决定，并邀请用户说明自己的判断标准"),
                    "reflection_basis_turn_ids": [],
                    "evidence_observations": [],
                    "warnings": [
                        *raw_plan.warnings,
                        "deterministic autonomy-support boundary applied",
                    ],
                }
            )
        elif control_intent and raw_plan.response_intent == "assess_answer":
            raw_plan = raw_plan.model_copy(
                update={
                    "response_intent": (
                        "redirect"
                        if control_intent == "boundary_redirect"
                        else control_intent
                    ),
                    "action": "CLARIFY",
                    "target_dimension": None,
                    "target_evidence": None,
                    "evidence_observations": [],
                    "warnings": [
                        *raw_plan.warnings,
                        "deterministic control-intent isolation applied",
                    ],
                }
            )
        if (
            style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
            and raw_plan.response_intent == "assess_answer"
        ):
            v11_evidence_source_text = (
                authority_substantive_text
                if mixed_authority_request and authority_substantive_text
                else turn.content
            )
            raw_plan = raw_plan.model_copy(
                update={
                    "evidence_observations": (
                        InterviewPlannerAgent._v11_observations(  # noqa: SLF001
                            v11_evidence_source_text,
                            allow_dynamic=(
                                "counter_evidence" in state.released_event_codes
                            ),
                        )
                    )
                }
            )
        formal_answer_candidate = (
            raw_plan.response_intent == "assess_answer" and control_intent is None
        )
        evidence_response_origin = (
            EvidenceTrackerService.classify_response_origin(
                formal_answer=formal_answer_candidate,
                preceding_ai_content_type=(
                    preceding_ai_turn.content_type
                    if preceding_ai_turn is not None
                    else None
                ),
            )
            if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
            else None
        )
        formal_answer = bool(
            formal_answer_candidate and evidence_response_origin != "not_scored"
        )
        opportunity_target: str | None = None

        if formal_answer:
            state.formal_user_turn_count += 1
            state.clarification_count_for_last_answer = 0
            opportunity_target = self._opportunity_target_for_answer(state)
            if opportunity_target in state.dimension_slots:
                state.dimension_opportunity_counts[opportunity_target] = (
                    state.dimension_opportunity_counts.get(opportunity_target, 0) + 1
                )
                state.dimension_opportunity_quality[opportunity_target] = max(
                    state.dimension_opportunity_quality.get(opportunity_target, 0),
                    15
                    if (state.last_plan or {}).get("response_intent")
                    in {
                        "request_context",
                        "conversation_repair",
                        "clarify_question",
                        "explain_term",
                    }
                    else 25,
                )
        else:
            state.clarification_count_for_last_answer = min(
                state.clarification_count_for_last_answer + 1,
                blueprint.conversation_budget.max_clarifications_per_answer,
            )
            if raw_plan.response_intent in {"request_context", "conversation_repair"}:
                state.context_repair_count += 1
        should_audit_observations = bool(
            formal_answer
            or (
                style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and formal_answer_candidate
            )
        )
        quote_grounded_observations = (
            [
                item
                for item in raw_plan.evidence_observations
                if item.quote.strip() and item.quote.strip() in turn.content
            ]
            if should_audit_observations
            else []
        )
        discarded_quote_count = len(raw_plan.evidence_observations) - len(
            quote_grounded_observations
        )
        valid_observations = quote_grounded_observations
        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1:
            valid_observations = EvidenceTrackerService.annotate_observations(
                valid_observations,
                response_origin=evidence_response_origin or "not_scored",
                source_turn_id=turn.id,
                preceding_ai_turn_id=(
                    preceding_ai_turn.id if preceding_ai_turn is not None else None
                ),
                preceding_ai_text=(
                    preceding_ai_turn.content if preceding_ai_turn is not None else None
                ),
                earlier_user_texts=[item.content for item in earlier_user_turns],
                source_text=turn.content,
            )
        if (
            formal_answer
            and opportunity_target in state.dimension_slots
            and result.status == "ok"
            and not any(
                item.dimension_key == opportunity_target
                and item.validity in {"valid", "weak"}
                and item.disposition == "accepted"
                for item in valid_observations
            )
        ):
            tracker = EvidenceTrackerService()
            rule = tracker.rules[opportunity_target]
            weak_quote = turn.content[:500]
            if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1:
                weak_quote = tracker.original_span_after_exclusions(
                    turn.content,
                    [
                        item.quote
                        for item in valid_observations
                        if item.introduced_by_ai
                    ],
                )
            weak_observations = (
                [
                    EvidenceObservation(
                        dimension_key=opportunity_target,
                        behavior_key=rule.behaviors[0].behavior_key,
                        quote=weak_quote,
                        validity="weak",
                        rationale=(
                            "该轮获得了公平且中性的实质作答机会，"
                            "但回答未呈现该维度合同要求的可观察行为；"
                            "按诊断性低表现证据记录，不等同于缺少作答机会。"
                        ),
                        extraction_confidence=0.9,
                    )
                ]
                if weak_quote
                else []
            )
            if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1 and weak_observations:
                weak_observations = tracker.annotate_observations(
                    weak_observations,
                    response_origin=evidence_response_origin or "not_scored",
                    source_turn_id=turn.id,
                    preceding_ai_turn_id=(
                        preceding_ai_turn.id if preceding_ai_turn is not None else None
                    ),
                    preceding_ai_text=(
                        preceding_ai_turn.content
                        if preceding_ai_turn is not None
                        else None
                    ),
                    earlier_user_texts=[item.content for item in earlier_user_turns],
                    source_text=turn.content,
                )
            valid_observations.extend(weak_observations)
        evidence_deltas = (
            EvidenceTrackerService().apply(
                state, turn_id=turn.id, observations=valid_observations
            )
            if formal_answer
            else []
        )
        for observation in valid_observations:
            if observation.disposition == "excluded":
                continue
            if observation.dimension_key in state.dimension_slots:
                state.dimension_opportunity_counts[observation.dimension_key] = max(
                    state.dimension_opportunity_counts.get(
                        observation.dimension_key, 0
                    ),
                    1,
                )
                state.dimension_opportunity_quality[observation.dimension_key] = max(
                    state.dimension_opportunity_quality.get(
                        observation.dimension_key, 0
                    ),
                    15,
                )
            if observation.validity == "weak":
                bucket = state.weak_evidence_turn_ids.setdefault(
                    observation.dimension_key, []
                )
                if turn.id not in bucket:
                    bucket.append(turn.id)
        plan = raw_plan.model_copy(
            update={
                "evidence_observations": valid_observations,
                "warnings": [
                    *raw_plan.warnings,
                    *(
                        ["discarded evidence quote not found in raw user turn"]
                        if discarded_quote_count
                        else []
                    ),
                ],
            }
        )
        if (
            style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
            and routed is not None
            and plan.response_intent == "conversation_repair"
        ):
            correction = compose_v11_correction_acknowledgement(
                turn.content,
                result.output.interviewer.message,
                target_dimension=plan.target_dimension,
            )
            if correction is not None:
                correction_message, correction_quote = correction
                plan = plan.model_copy(update={"reflection_basis_turn_ids": [turn.id]})
                correction_output = result.output.interviewer.model_copy(
                    update={
                        "message": correction_message,
                        "reflection_turn_ids": [turn.id],
                        "reflection_source_quotes": [
                            ReflectionSourceQuote(
                                turn_id=turn.id,
                                quote=correction_quote,
                            )
                        ],
                        "warnings": [
                            *result.output.interviewer.warnings,
                            "v1.1 explicit correction acknowledged",
                        ],
                    }
                )
                result.output = result.output.model_copy(
                    update={
                        "plan": plan,
                        "interviewer": correction_output,
                    }
                )
        state.memory = plan.memory_update
        decision_source_text = (
            authority_substantive_text
            if mixed_authority_request and authority_substantive_text
            else turn.content
        )
        if formal_answer and any(
            marker in decision_source_text
            for marker in (
                "我倾向",
                "我选择",
                "我决定",
                "我会采用",
                "继续逐项",
                "小范围试用",
                "保留关键",
                "先在非关键",
            )
        ):
            state.memory.prior_decision_formed = True
            state.memory.user_position = decision_source_text[:160]
        if formal_answer:
            plan = InterviewPlannerAgent().enforce(plan, state, blueprint)
            plan = InterviewPlannerAgent().avoid_duplicate(plan, state, blueprint)

        action_changed = any(
            getattr(plan, key) != getattr(raw_plan, key)
            for key in (
                "action",
                "target_dimension",
                "release_event_code",
                "release_unit_code",
                "delivery_mode",
            )
        )
        previous_questions = [
            item.content
            for item in self.repo.list_turns(session.id)
            if item.speaker == "ai" and "?" in item.content.replace("？", "?")
        ]
        planner_rerender_applied = False
        planner_interviewer_discarded = False
        if action_changed and routed is None:
            rerendered = agent.rerender_after_plan_enforcement(
                context,
                state,
                blueprint,
                plan,
                fallback_used=result.status != "ok",
            )
            validation_errors = agent.validate_turn(
                rerendered,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=previous_questions,
                state=state,
            )
            if validation_errors:
                validation_errors.append("planner_action_enforced")
            else:
                result.output = result.output.model_copy(
                    update={"plan": plan, "interviewer": rerendered}
                )
                planner_rerender_applied = True
        else:
            validation_errors = agent.validate_turn(
                result.output.interviewer,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=previous_questions,
                state=state,
            )
        format_repair_applied = False
        if (
            not action_changed
            and set(validation_errors) == {"too_many_sentences"}
            and "。" in result.output.interviewer.message
        ):
            repaired_interviewer = result.output.interviewer.model_copy(
                update={
                    "message": result.output.interviewer.message.replace("。", "；", 1),
                    "warnings": [
                        *result.output.interviewer.warnings,
                        "deterministic sentence-boundary repair",
                    ],
                }
            )
            repaired_errors = agent.validate_turn(
                repaired_interviewer,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=previous_questions,
                state=state,
            )
            if not repaired_errors:
                result.output = result.output.model_copy(
                    update={"plan": plan, "interviewer": repaired_interviewer}
                )
                validation_errors = []
                format_repair_applied = True
        if validation_errors:
            rendered = agent.fallback(
                context, state, blueprint, plan=plan
            ).interviewer.model_copy(
                update={
                    "fallback_used": True,
                    "warnings": [f"quality gate: {','.join(validation_errors)}"],
                }
            )
            fallback_errors = agent.validate_turn(
                rendered,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=previous_questions,
                state=state,
            )
            duplicate_errors = {
                "duplicate_question",
                "semantic_duplicate_question",
            } & set(fallback_errors)
            if duplicate_errors and plan.action != "RELEASE_EVENT":
                plan = plan.model_copy(
                    update={
                        "delivery_mode": "clarification",
                        "reflection_basis_turn_ids": [],
                        "question_intent": "换用可观察结果检验当前判断",
                        "warnings": [
                            *plan.warnings,
                            "fallback duplicate replaced with verification question",
                        ],
                    }
                )
                rendered = rendered.model_copy(
                    update={
                        "message": InterviewerAgent.select_probe_message(
                            plan.target_dimension,
                            previous_questions=previous_questions,
                        ),
                        "reflection_turn_ids": [],
                        "reflection_source_quotes": [],
                        "warnings": [
                            *rendered.warnings,
                            "fallback anti-repeat replacement",
                        ],
                    }
                )
                validation_errors.extend(sorted(duplicate_errors))
            if (
                style_version == HUMANISTIC_INTERVIEWER_STYLE
                and model_call_status == "success"
            ):
                rendered = _compact_humanistic_runtime_fallback(
                    rendered,
                    plan=plan,
                    blueprint=blueprint,
                    context=context,
                )
            result.output = result.output.model_copy(
                update={"plan": plan, "interviewer": rendered}
            )
            final_seed_errors = agent.validate_turn(
                rendered,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=previous_questions,
                state=state,
            )
            if final_seed_errors:
                result.status = "failed"
                result.error_code = (
                    result.error_code or "MEASUREMENT_CORE_VALIDATION_FAILED"
                )
                result.fallback_type = (
                    result.fallback_type or "deterministic_measurement_core"
                )
                validation_errors.extend(final_seed_errors)
            elif _is_humanistic_interviewer_style(style_version):
                # The Planner's original wording is not user-visible in the
                # two-stage Humanistic runtime. A validated deterministic seed
                # may proceed to the independent renderer without classifying
                # the measurement core as failed.
                planner_interviewer_discarded = True
        else:
            result.output = result.output.model_copy(update={"plan": plan})
        result.validation_errors = list(
            dict.fromkeys([*(result.validation_errors or []), *validation_errors])
        )
        core_failed = result.status != "ok"
        if formal_answer and core_failed:
            state.technical_fallback_count += 1

        if plan.action == "RELEASE_EVENT" and plan.release_event_code:
            if (
                plan.release_unit_code
                and plan.release_unit_code not in state.released_unit_codes
            ):
                state.released_unit_codes.append(plan.release_unit_code)
            EvidenceTrackerService().unlock_for_event(state, plan.release_event_code)
            event = next(
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            )
            state.current_node_code = event.node_code
            stage = self.repo.get_stage_by_code(session.scenario_id, event.node_code)
            if stage is not None:
                session.current_stage_id = stage.id
        if plan.action in {"PROBE", "CHALLENGE"}:
            state.topic_probe_counters[plan.active_topic] = (
                state.topic_probe_counters.get(plan.active_topic, 0) + 1
            )
            if plan.target_dimension:
                state.dimension_probe_counters[plan.target_dimension] = (
                    state.dimension_probe_counters.get(plan.target_dimension, 0) + 1
                )
                if state.consecutive_dimension == plan.target_dimension:
                    state.consecutive_dimension_count += 1
                else:
                    state.consecutive_dimension = plan.target_dimension
                    state.consecutive_dimension_count = 1
        else:
            state.consecutive_dimension = None
            state.consecutive_dimension_count = 0
        state.active_topic = plan.active_topic
        intent_key = InterviewPlannerAgent.intent_key(plan)
        if intent_key not in state.asked_intent_keys:
            state.asked_intent_keys.append(intent_key)
        if (
            plan.target_dimension == "integrative_decision"
            and "初步决定" in plan.question_intent
        ):
            state.initial_decision_prompted = True

        delta_payload = [item.model_dump(mode="json") for item in evidence_deltas]
        evidence_provenance_payload = [
            {
                "dimension_key": item.dimension_key,
                "behavior_key": item.behavior_key,
                "quote": item.quote,
                "validity": item.validity,
                "extraction_confidence": item.extraction_confidence,
                "response_origin": item.response_origin,
                "source_turn_id": item.source_turn_id,
                "preceding_ai_turn_id": item.preceding_ai_turn_id,
                "introduced_by_ai": item.introduced_by_ai,
                "disposition": item.disposition,
                "exclusion_reason": item.exclusion_reason,
                "evidence_policy_version": item.evidence_policy_version,
            }
            for item in valid_observations
        ]
        v11_evidence_audit = (
            {
                "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                "evidence_response_origin": evidence_response_origin,
                "evidence_source_turn_id": turn.id,
                "preceding_ai_turn_id": (
                    preceding_ai_turn.id if preceding_ai_turn is not None else None
                ),
                "evidence_provenance": evidence_provenance_payload,
                "pure_authority_request": pure_authority_request,
                "mixed_authority_request": mixed_authority_request,
                "authority_request_kind": (
                    authority_request.kind if authority_request is not None else None
                ),
                "authority_substantive_text": authority_substantive_text,
                "authority_substantive_fragments": authority_substantive_fragments,
                "authority_removed_spans": authority_removed_spans,
            }
            if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
            else {}
        )
        turn.analysis_json = {
            "analysis_source": CONSULTATIVE_TURN_PROMPT_VERSION,
            "response_intent": plan.response_intent,
            "evidence_delta": delta_payload,
            "formal_answer": formal_answer,
            "technical_fallback": core_failed,
            "renderer_fallback": False,
            "excluded_from_scoring": not formal_answer,
            "interviewer_style_version": style_version,
            **v11_evidence_audit,
        }
        trace = AgentTrace(
            session_id=session.id,
            stage_id=session.current_stage_id,
            trigger_turn_id=turn.id,
            agent_name="consultative_turn",
            generation_mode=(
                get_settings().MODEL_GATEWAY_MODE.lower()
                if result.model_attempt_count
                else "deterministic"
            ),
            ai_generation_weight=100 if result.model_attempt_count else 0,
            config_snapshot_json={
                "prompt_version": CONSULTATIVE_TURN_PROMPT_VERSION,
                "prompt_variant": CONSULTATIVE_TURN_PROMPT_VARIANT,
                "measurement_scope": (
                    "formal_answer" if formal_answer else "non_measurement"
                ),
                "measurement_source": "deterministic_measurement_core_v1",
                "evidence_source": "deterministic_behavior_signals_v1",
                **(
                    {
                        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                        "evidence_response_origin": evidence_response_origin,
                        "evidence_source_turn_id": turn.id,
                        "preceding_ai_turn_id": (
                            preceding_ai_turn.id
                            if preceding_ai_turn is not None
                            else None
                        ),
                        "evidence_provenance": evidence_provenance_payload,
                        "pure_authority_request": pure_authority_request,
                        "mixed_authority_request": mixed_authority_request,
                        "authority_request_kind": (
                            authority_request.kind
                            if authority_request is not None
                            else None
                        ),
                        "authority_substantive_text": authority_substantive_text,
                        "authority_substantive_fragments": (
                            authority_substantive_fragments
                        ),
                        "authority_removed_spans": authority_removed_spans,
                        "accepted_observation_count": sum(
                            item["disposition"] == "accepted"
                            for item in evidence_provenance_payload
                        ),
                        "excluded_observation_count": sum(
                            item["disposition"] == "excluded"
                            for item in evidence_provenance_payload
                        ),
                    }
                    if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    else {}
                ),
                "measurement_core_status": ("failed" if core_failed else "success"),
                "flow_version": session.flow_version,
                "configured_style_version": session.interviewer_style_version,
                "interviewer_style_version": style_version,
                "hidden_target_dimension": plan.target_dimension,
                "target_evidence": plan.target_evidence,
                "action": plan.action,
                "delivery_mode": plan.delivery_mode,
                "release_event_code": plan.release_event_code,
                "release_unit_code": plan.release_unit_code,
                "reflection_basis_turn_ids": plan.reflection_basis_turn_ids,
                "reflection_source_quotes": [
                    item.model_dump(mode="json")
                    for item in result.output.interviewer.reflection_source_quotes
                ],
                "question_intent_key": intent_key,
                "evidence_delta": delta_payload,
                "validation_errors": result.validation_errors,
                "format_repair_applied": format_repair_applied,
                "planner_rerender_applied": planner_rerender_applied,
                "planner_interviewer_discarded": planner_interviewer_discarded,
                "model_call_status": model_call_status,
                "model_attempt_count": result.model_attempt_count,
                "transport_retry_limit": 0,
                "transport_retry_reason": result.retry_reason,
                "visible_renderer": (
                    "deterministic_router"
                    if routed is not None
                    else "independent_renderer"
                ),
                "timeout_ms": state.turn_latency_budget_ms,
                "identity_constraints": state.identity_constraints,
                "task_domain": state.task_domain,
            },
            input_json={
                "trigger_turn_id": turn.id,
                "state": state_before,
                "latest_user_turn": turn.content,
                **(
                    {
                        "audited_observations": evidence_provenance_payload,
                        "authority_substantive_text": authority_substantive_text,
                        "authority_substantive_fragments": (
                            authority_substantive_fragments
                        ),
                        "authority_removed_spans": authority_removed_spans,
                    }
                    if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    else {}
                ),
            },
            output_json=result.output.model_dump(mode="json"),
            raw_output=result.raw_output,
            status="success" if result.status == "ok" else "fallback",
            error_code=result.error_code,
            fallback_type=result.fallback_type,
            model_name=result.model_name,
            duration_ms=max(result.duration_ms, 0),
        )
        self.db.add(trace)
        self.db.flush()
        visible_output = result.output.interviewer
        visible_trace = trace
        total_duration_ms = result.duration_ms
        if style_version in SUPPORTED_INTERVIEWER_STYLES:
            renderer_agent = InterviewerAgent()
            renderer_settings = get_settings()
            runtime_interview_settings = get_runtime_interview_settings()
            renderer_mode = renderer_settings.MODEL_GATEWAY_MODE.lower()
            renderer_prompt_code = (
                RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION_V1_1
                if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                else RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION
                if style_version == HUMANISTIC_INTERVIEWER_STYLE
                else RUNTIME_INTERVIEWER_PROMPT_VERSION
            )
            renderer_prompt = self._prompt_for_agent_version(
                "interviewer",
                template_code=renderer_prompt_code,
                version=renderer_prompt_code,
            )
            remaining_ms = max(
                state.turn_latency_budget_ms - max(result.duration_ms, 0),
                0,
            )
            renderer_total_timeout_seconds = max(
                0.0,
                remaining_ms / 1000,
            )
            renderer_primary_timeout_seconds = min(
                6.0,
                float(
                    runtime_interview_settings.RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS
                ),
                renderer_total_timeout_seconds,
            )
            renderer_prompt_id = None
            renderer_prompt_version = "deterministic_renderer_v1"
            renderer_input = renderer_agent.runtime_renderer_input_payload(
                context,
                blueprint,
                plan,
                style_version=style_version,
            )
            v11_polish_mode = str(
                getattr(
                    runtime_interview_settings,
                    "RUNTIME_HUMANISTIC_V11_MODEL_POLISH_MODE",
                    "adaptive",
                )
            )
            v11_model_polish_required = bool(
                style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and renderer_agent.v11_requires_model_polish(
                    renderer_input,
                    mode=v11_polish_mode,
                )
            )
            v11_router_model_eligible = bool(
                style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and routed is not None
                and plan.response_intent in {"clarify_question", "low_information"}
                and not pure_authority_request
            )
            v11_router_bypass = bool(
                style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and routed is not None
                and not v11_router_model_eligible
                and not pure_authority_request
            )
            if v11_router_bypass:
                event_intro_audit = {
                    key: renderer_input.get(key)
                    for key in (
                        "event_intro_selector_version",
                        "previous_event_intro_frame",
                        "selected_event_intro_frame",
                    )
                }
                renderer_input = {
                    "style_version": style_version,
                    **event_intro_audit,
                    "validated_plan": plan.model_dump(mode="json"),
                    "draft": result.output.interviewer.message,
                    "candidate_selection_applied": False,
                    "renderer_bypass_reason": ("deterministic_non_measurement_router"),
                    "question_candidates": [],
                    "selected_candidate_id": None,
                    "selected_question": None,
                    "planner_question_intent": plan.question_intent,
                    "planner_target_evidence": plan.target_evidence,
                }
            if routed is not None and not v11_router_model_eligible:
                routed_output = (
                    renderer_agent._fallback(  # noqa: SLF001
                        plan,
                        blueprint,
                        context,
                        style_version=style_version,
                    )
                    if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    and pure_authority_request
                    else result.output.interviewer
                )
                renderer_result = InterviewerAgentResult(
                    output=routed_output,
                    raw_output=routed_output.model_dump_json(),
                    model_name=(
                        "deterministic-autonomy-support-v1_1"
                        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        and pure_authority_request
                        else "deterministic-router-v1"
                    ),
                    duration_ms=0,
                    audit_metadata=(
                        renderer_agent._v11_audit_metadata(  # noqa: SLF001
                            renderer_input
                        )
                        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        else {}
                    ),
                )
                renderer_prompt_version = (
                    "deterministic_autonomy_support_v1_1"
                    if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    and pure_authority_request
                    else "deterministic_router_v1"
                )
            elif (
                style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and not v11_model_polish_required
            ):
                renderer_result = renderer_agent.render(
                    context,
                    blueprint,
                    plan,
                    previous_questions=previous_questions,
                    style_version=style_version,
                    timeout_seconds=renderer_total_timeout_seconds,
                    primary_timeout_seconds=renderer_primary_timeout_seconds,
                    allow_model_call=False,
                    deterministic_primary=True,
                    renderer_input=renderer_input,
                )
                renderer_prompt_version = "deterministic_humanistic_v1_1"
            elif renderer_prompt is None and renderer_mode != "mock":
                missing_prompt_output = renderer_agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    context,
                    style_version=style_version,
                ).model_copy(
                    update={
                        "fallback_used": True,
                        "warnings": [
                            "versioned renderer prompt missing; deterministic fallback used"
                        ],
                    }
                )
                renderer_result = InterviewerAgentResult(
                    output=missing_prompt_output,
                    raw_output=None,
                    model_name=None,
                    duration_ms=0,
                    status="failed",
                    error_code=(
                        "HUMANISTIC_PROMPT_TEMPLATE_MISSING"
                        if _is_humanistic_interviewer_style(style_version)
                        else "INTERVIEWER_PROMPT_TEMPLATE_MISSING"
                    ),
                    fallback_type=(
                        "humanistic_deterministic_renderer"
                        if _is_humanistic_interviewer_style(style_version)
                        else "neutral_renderer"
                    ),
                    validation_errors=["prompt_template_missing"],
                )
                renderer_prompt_version = "renderer_prompt_missing"
            else:
                allow_model_call = bool(
                    renderer_mode != "mock"
                    and remaining_ms >= 1000
                    and renderer_total_timeout_seconds >= 1
                    and (
                        style_version != HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        or v11_model_polish_required
                    )
                )
                renderer_result = renderer_agent.render(
                    context,
                    blueprint,
                    plan,
                    previous_questions=previous_questions,
                    template_content=(
                        renderer_prompt.content if renderer_prompt else None
                    ),
                    style_version=style_version,
                    timeout_seconds=renderer_total_timeout_seconds,
                    primary_timeout_seconds=renderer_primary_timeout_seconds,
                    allow_model_call=allow_model_call,
                    deterministic_primary=False,
                    renderer_input=renderer_input,
                )
                if renderer_result.model_attempt_count:
                    renderer_prompt_id = renderer_prompt.id
                    renderer_prompt_version = renderer_prompt_code
                elif renderer_mode == "mock":
                    renderer_prompt_version = "deterministic_renderer_mock_v1"
                else:
                    renderer_prompt_version = "deterministic_renderer_budget_v1"

            if (
                renderer_result.status != "ok"
                and style_version == HUMANISTIC_INTERVIEWER_STYLE
            ):
                renderer_result.output = _compact_humanistic_runtime_fallback(
                    renderer_result.output,
                    plan=plan,
                    blueprint=blueprint,
                    context=context,
                )

            enforce_humanistic_safety = _is_humanistic_interviewer_style(style_version)
            hard_validation_errors = agent.validate_turn(
                renderer_result.output,
                plan=plan,
                blueprint=blueprint,
                context=context,
                previous_questions=previous_questions,
                state=state,
                enforce_humanistic_safety=enforce_humanistic_safety,
            )
            hard_validation_errors = list(
                dict.fromkeys(
                    [
                        *hard_validation_errors,
                        *renderer_agent.runtime_expression_errors(
                            renderer_result.output.message,
                            renderer_input,
                        ),
                    ]
                )
            )
            if (
                style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and not v11_router_bypass
            ):
                hard_validation_errors = list(
                    dict.fromkeys(
                        [
                            *hard_validation_errors,
                            *renderer_agent._v11_contract_errors(  # noqa: SLF001
                                renderer_result.output,
                                renderer_input,
                            ),
                        ]
                    )
                )
            if hard_validation_errors:
                safe_output = renderer_agent._fallback(  # noqa: SLF001
                    plan,
                    blueprint,
                    context,
                    style_version=style_version,
                ).model_copy(
                    update={
                        "fallback_used": True,
                        "warnings": [
                            "hard safety gate replaced renderer output",
                            *renderer_result.output.warnings,
                        ],
                    }
                )
                safe_output = _compact_humanistic_runtime_fallback(
                    safe_output,
                    plan=plan,
                    blueprint=blueprint,
                    context=context,
                )
                safe_errors = agent.validate_turn(
                    safe_output,
                    plan=plan,
                    blueprint=blueprint,
                    context=context,
                    previous_questions=previous_questions,
                    state=state,
                    enforce_humanistic_safety=enforce_humanistic_safety,
                )
                safe_errors = list(
                    dict.fromkeys(
                        [
                            *safe_errors,
                            *renderer_agent.runtime_expression_errors(
                                safe_output.message,
                                renderer_input,
                            ),
                        ]
                    )
                )
                if (
                    style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                    and not v11_router_bypass
                ):
                    safe_errors = list(
                        dict.fromkeys(
                            [
                                *safe_errors,
                                *renderer_agent._v11_contract_errors(  # noqa: SLF001
                                    safe_output,
                                    renderer_input,
                                ),
                            ]
                        )
                    )
                if safe_errors:
                    safe_output = result.output.interviewer.model_copy(
                        update={
                            "fallback_used": True,
                            "warnings": [
                                "renderer fallback invalid; validated core fallback used"
                            ],
                        }
                    )
                    safe_errors = agent.validate_turn(
                        safe_output,
                        plan=plan,
                        blueprint=blueprint,
                        context=context,
                        previous_questions=previous_questions,
                        state=state,
                        enforce_humanistic_safety=enforce_humanistic_safety,
                    )
                if safe_errors:
                    raise ValueError(
                        "Interviewer renderer failed closed because both "
                        "deterministic safety fallbacks were invalid: "
                        + ",".join(safe_errors)
                    )
                renderer_result.output = safe_output
                renderer_result.status = "failed"
                renderer_result.error_code = (
                    "HUMANISTIC_HARD_GATE_FALLBACK"
                    if enforce_humanistic_safety
                    else "INTERVIEWER_HARD_GATE_FALLBACK"
                )
                renderer_result.fallback_type = (
                    "humanistic_deterministic_renderer"
                    if enforce_humanistic_safety
                    else "neutral_renderer"
                )
                renderer_result.validation_errors = list(
                    dict.fromkeys(
                        [
                            *renderer_result.validation_errors,
                            *hard_validation_errors,
                            *safe_errors,
                        ]
                    )
                )

            renderer_trace = AgentTrace(
                session_id=session.id,
                stage_id=session.current_stage_id,
                trigger_turn_id=turn.id,
                prompt_template_id=renderer_prompt_id,
                agent_name="interviewer_renderer",
                generation_mode=(
                    renderer_mode
                    if renderer_result.model_attempt_count
                    else "deterministic"
                ),
                ai_generation_weight=(
                    100
                    if renderer_result.model_attempt_count
                    and renderer_result.status == "ok"
                    else 0
                ),
                config_snapshot_json={
                    "prompt_version": renderer_prompt_version,
                    "prompt_variant": INTERVIEWER_RENDER_PROMPT_VARIANT,
                    "flow_version": session.flow_version,
                    "parent_trace_id": trace.id,
                    "measurement_source": "deterministic_measurement_core_v1",
                    "renderer_source": (
                        "deterministic_router"
                        if routed is not None and not v11_router_model_eligible
                        else "deterministic_primary"
                        if renderer_result.status == "ok"
                        and renderer_result.model_attempt_count == 0
                        and style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        else "model"
                        if renderer_result.status == "ok"
                        and renderer_result.model_attempt_count
                        else "deterministic_fallback"
                    ),
                    "renderer_status": (
                        "success" if renderer_result.status == "ok" else "fallback"
                    ),
                    "configured_style_version": session.interviewer_style_version,
                    "interviewer_style_version": style_version,
                    "action": plan.action,
                    "target_dimension": plan.target_dimension,
                    "release_event_code": plan.release_event_code,
                    "release_unit_code": plan.release_unit_code,
                    "delivery_mode": plan.delivery_mode,
                    "reflection_basis_turn_ids": plan.reflection_basis_turn_ids,
                    "event_intro_selector_version": renderer_input.get(
                        "event_intro_selector_version"
                    ),
                    "previous_event_intro_frame": renderer_input.get(
                        "previous_event_intro_frame"
                    ),
                    "selected_event_intro_frame": renderer_input.get(
                        "selected_event_intro_frame"
                    ),
                    **(
                        {"humanistic_v1_1_audit": (renderer_result.audit_metadata)}
                        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        else {}
                    ),
                    "validation_codes": renderer_result.validation_errors,
                    "fallback_reason": renderer_result.error_code,
                    "timeout_ms": int(renderer_total_timeout_seconds * 1000),
                    "primary_timeout_ms": int(renderer_primary_timeout_seconds * 1000),
                    "planner_timeout_ms": state.turn_latency_budget_ms,
                    "shared_planner_renderer_budget": True,
                    "v11_model_polish_mode": (
                        v11_polish_mode
                        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        else None
                    ),
                    "v11_model_polish_required": (
                        v11_model_polish_required
                        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        else None
                    ),
                    "v11_router_model_eligible": (
                        v11_router_model_eligible
                        if style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                        else None
                    ),
                    "transport_retry_limit": INTERVIEWER_RENDER_FAST_RETRY_LIMIT,
                    "transport_retry_reason": renderer_result.retry_reason,
                    "transport_errors": renderer_result.transport_errors,
                    "attempt_durations_ms": (renderer_result.attempt_durations_ms),
                    "model_attempt_count": renderer_result.model_attempt_count,
                    "model_call_status": (
                        "not_called"
                        if renderer_result.model_attempt_count == 0
                        else "success"
                        if renderer_result.status == "ok"
                        else "failed"
                    ),
                    "single_model_attempt": (renderer_result.model_attempt_count <= 1),
                },
                input_json=renderer_input,
                output_json=renderer_result.output.model_dump(mode="json"),
                raw_output=renderer_result.raw_output,
                status=("success" if renderer_result.status == "ok" else "fallback"),
                error_code=renderer_result.error_code,
                fallback_type=renderer_result.fallback_type,
                model_name=renderer_result.model_name,
                duration_ms=max(renderer_result.duration_ms, 0),
            )
            self.db.add(renderer_trace)
            self.db.flush()
            visible_output = renderer_result.output
            visible_trace = renderer_trace
            total_duration_ms += renderer_result.duration_ms
            turn.analysis_json = {
                **(turn.analysis_json or {}),
                "renderer_trace_id": renderer_trace.id,
                "renderer_fallback": renderer_result.status != "ok",
            }
        content_type = {
            "CLARIFY": "interview_clarification",
            "RELEASE_EVENT": "interview_event",
            "INTEGRATE": "interview_integration",
            "CONCLUDE": "interview_closing",
        }.get(plan.action, "interview_followup")
        ai_turn = DialogueTurn(
            session_id=session.id,
            stage_id=session.current_stage_id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="ai",
            content=visible_output.message,
            content_type=content_type,
            source_agent_trace_id=visible_trace.id,
        )
        self.db.add(ai_turn)
        InterviewStateService.save(session, state, plan=plan)
        self.db.flush()
        next_action = "wait_user_answer"
        if plan.action == "CONCLUDE":
            self._block_unresolved_progressive_slots(
                session, reason="probe_budget_exhausted"
            )
            self._mark_session_completed(session)
            next_action = "generate_report"
        return ai_turn, next_action, total_duration_ms

    def _process_progressive_turn(
        self,
        session: AssessmentSession,
        turn: DialogueTurn,
    ) -> tuple[DialogueTurn, str, int]:
        scenario = self.repo.get_scenario(session.scenario_id)
        if scenario is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Progressive interview scenario is missing.",
            )
        blueprint = InterviewStateService.blueprint(scenario)
        if blueprint is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Progressive interview blueprint is missing.",
            )
        state = InterviewStateService.load(session, scenario)
        context = self._build_agent_context(session, turn)
        state_before = state.model_dump(mode="json")

        planner_prompt = self._active_prompt_for_agent("planner")
        planner_result = InterviewPlannerAgent().generate(
            context,
            state,
            blueprint,
            planner_prompt.content if planner_prompt else None,
        )
        plan = planner_result.output
        if plan.response_intent == "assess_answer":
            state.formal_user_turn_count += 1
            state.clarification_count_for_last_answer = 0
        else:
            state.clarification_count_for_last_answer = min(
                state.clarification_count_for_last_answer + 1,
                blueprint.conversation_budget.max_clarifications_per_answer,
            )

        valid_observations = [
            item
            for item in plan.evidence_observations
            if item.quote.strip() and item.quote.strip() in turn.content
        ]
        evidence_deltas = EvidenceTrackerService().apply(
            state,
            turn_id=turn.id,
            observations=valid_observations,
        )
        if len(valid_observations) != len(plan.evidence_observations):
            plan = plan.model_copy(
                update={
                    "evidence_observations": valid_observations,
                    "warnings": [
                        *plan.warnings,
                        "discarded evidence quote not found in raw user turn",
                    ],
                }
            )
        state.memory = plan.memory_update
        if plan.response_intent == "assess_answer":
            plan = InterviewPlannerAgent().enforce(plan, state, blueprint)

        if plan.action == "RELEASE_EVENT" and plan.release_event_code:
            if (
                plan.release_unit_code
                and plan.release_unit_code not in state.released_unit_codes
            ):
                state.released_unit_codes.append(plan.release_unit_code)
            EvidenceTrackerService().unlock_for_event(state, plan.release_event_code)
            event = next(
                item
                for item in blueprint.event_cards
                if item.event_code == plan.release_event_code
            )
            state.current_node_code = event.node_code
            stage = self.repo.get_stage_by_code(session.scenario_id, event.node_code)
            if stage is not None:
                session.current_stage_id = stage.id

        if plan.action in {"PROBE", "CHALLENGE"}:
            state.topic_probe_counters[plan.active_topic] = (
                state.topic_probe_counters.get(plan.active_topic, 0) + 1
            )
            if plan.target_dimension:
                state.dimension_probe_counters[plan.target_dimension] = (
                    state.dimension_probe_counters.get(plan.target_dimension, 0) + 1
                )
                if state.consecutive_dimension == plan.target_dimension:
                    state.consecutive_dimension_count += 1
                else:
                    state.consecutive_dimension = plan.target_dimension
                    state.consecutive_dimension_count = 1
        else:
            state.consecutive_dimension = None
            state.consecutive_dimension_count = 0
        state.active_topic = plan.active_topic

        plan_payload = plan.model_dump(mode="json")
        delta_payload = [item.model_dump(mode="json") for item in evidence_deltas]
        turn.analysis_json = {
            "analysis_source": "progressive_planner_v3_1",
            "response_intent": plan.response_intent,
            "evidence_delta": delta_payload,
            "formal_answer": plan.response_intent == "assess_answer",
        }
        planner_trace = AgentTrace(
            session_id=session.id,
            stage_id=session.current_stage_id,
            trigger_turn_id=turn.id,
            prompt_template_id=planner_prompt.id if planner_prompt else None,
            agent_name="planner",
            generation_mode=get_settings().MODEL_GATEWAY_MODE.lower(),
            ai_generation_weight=100,
            config_snapshot_json={
                "prompt_version": PLANNER_PROMPT_VERSION,
                "flow_version": session.flow_version,
                "state_version_before": session.state_version,
                "action": plan.action,
                "target_dimension": plan.target_dimension,
                "target_evidence": plan.target_evidence,
                "reason": plan.reason,
                "release_event_code": plan.release_event_code,
                "release_unit_code": plan.release_unit_code,
                "delivery_mode": plan.delivery_mode,
                "evidence_delta": delta_payload,
            },
            input_json={
                "trigger_turn_id": turn.id,
                "state": state_before,
                "latest_user_turn": turn.content,
            },
            output_json=plan_payload,
            raw_output=planner_result.raw_output,
            status=("success" if planner_result.status == "ok" else "fallback"),
            error_code=planner_result.error_code,
            fallback_type=planner_result.fallback_type,
            model_name=planner_result.model_name,
            duration_ms=max(planner_result.duration_ms, 0),
        )
        self.db.add(planner_trace)
        self.db.flush()

        previous_questions = [
            item.content
            for item in self.repo.list_turns(session.id)
            if item.speaker == "ai" and "?" in item.content.replace("？", "?")
        ]
        interviewer_prompt = self._prompt_for_agent_version(
            "interviewer",
            template_code=RUNTIME_INTERVIEWER_PROMPT_VERSION,
            version=RUNTIME_INTERVIEWER_PROMPT_VERSION,
        )
        interviewer_agent = InterviewerAgent()
        interviewer_renderer_input = interviewer_agent.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=BASELINE_INTERVIEWER_STYLE,
        )
        interviewer_result = interviewer_agent.render(
            context,
            blueprint,
            plan,
            previous_questions=previous_questions,
            template_content=(
                interviewer_prompt.content if interviewer_prompt else None
            ),
            renderer_input=interviewer_renderer_input,
        )
        interviewer_trace = AgentTrace(
            session_id=session.id,
            stage_id=session.current_stage_id,
            trigger_turn_id=turn.id,
            prompt_template_id=interviewer_prompt.id if interviewer_prompt else None,
            agent_name="interviewer",
            generation_mode=get_settings().MODEL_GATEWAY_MODE.lower(),
            ai_generation_weight=100,
            config_snapshot_json={
                "prompt_version": RUNTIME_INTERVIEWER_PROMPT_VERSION,
                "flow_version": session.flow_version,
                "planner_trace_id": planner_trace.id,
                "action": plan.action,
                "release_event_code": plan.release_event_code,
                "release_unit_code": plan.release_unit_code,
                "delivery_mode": plan.delivery_mode,
                "reflection_basis_turn_ids": plan.reflection_basis_turn_ids,
                "event_intro_selector_version": interviewer_renderer_input.get(
                    "event_intro_selector_version"
                ),
                "previous_event_intro_frame": interviewer_renderer_input.get(
                    "previous_event_intro_frame"
                ),
                "selected_event_intro_frame": interviewer_renderer_input.get(
                    "selected_event_intro_frame"
                ),
                "evidence_delta": delta_payload,
            },
            input_json=interviewer_renderer_input,
            output_json=interviewer_result.output.model_dump(mode="json"),
            raw_output=interviewer_result.raw_output,
            status=("success" if interviewer_result.status == "ok" else "fallback"),
            error_code=interviewer_result.error_code,
            fallback_type=interviewer_result.fallback_type,
            model_name=interviewer_result.model_name,
            duration_ms=max(interviewer_result.duration_ms, 0),
        )
        self.db.add(interviewer_trace)
        self.db.flush()

        content_type = {
            "CLARIFY": "interview_clarification",
            "RELEASE_EVENT": "interview_event",
            "INTEGRATE": "interview_integration",
            "CONCLUDE": "interview_closing",
        }.get(plan.action, "interview_followup")
        ai_turn = DialogueTurn(
            session_id=session.id,
            stage_id=session.current_stage_id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="ai",
            content=interviewer_result.output.message,
            content_type=content_type,
            source_agent_trace_id=interviewer_trace.id,
        )
        self.db.add(ai_turn)
        InterviewStateService.save(session, state, plan=plan)
        self.db.flush()

        next_action = "wait_user_answer"
        if plan.action == "CONCLUDE":
            self._block_unresolved_progressive_slots(
                session, reason="interview_concluded_without_sufficient_evidence"
            )
            self._mark_session_completed(session)
            next_action = "generate_report"
        return (
            ai_turn,
            next_action,
            planner_result.duration_ms + interviewer_result.duration_ms,
        )

    def _active_prompt_for_agent(self, agent_name: str) -> PromptTemplate | None:
        return (
            self.db.execute(
                select(PromptTemplate)
                .where(
                    PromptTemplate.agent_name == agent_name,
                    PromptTemplate.status == "active",
                )
                .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
            )
            .scalars()
            .first()
        )

    def _prompt_for_agent_version(
        self,
        agent_name: str,
        *,
        template_code: str,
        version: str,
    ) -> PromptTemplate | None:
        """Resolve an immutable runtime prompt without changing other versions."""
        return self.db.execute(
            select(PromptTemplate).where(
                PromptTemplate.agent_name == agent_name,
                PromptTemplate.template_code == template_code,
                PromptTemplate.version == version,
                PromptTemplate.status == "active",
            )
        ).scalar_one_or_none()

    @staticmethod
    def _block_unresolved_progressive_slots(
        session: AssessmentSession, *, reason: str
    ) -> None:
        payload = dict(session.interview_state_json or {})
        slots = dict(payload.get("dimension_slots") or {})
        changed = False
        for key, raw_slot in slots.items():
            slot = dict(raw_slot or {})
            if slot.get("status") in {"not_started", "partial"}:
                slot["status"] = "blocked"
                slot["insufficient_reason"] = reason
                slots[key] = slot
                changed = True
        if changed:
            payload["dimension_slots"] = slots
            session.interview_state_json = payload
            session.state_version = (session.state_version or 0) + 1

    @staticmethod
    def _turn_payload(turn: DialogueTurn) -> dict[str, Any]:
        return {
            "turn_index": turn.turn_index,
            "speaker": turn.speaker,
            "content": turn.content,
            "content_type": turn.content_type,
            "created_at": turn.created_at.isoformat(),
        }

    def generate_report_if_completed(self, session_uuid: str) -> bool:
        """Claim and generate a completed-session report without a long DB lock."""
        claim_token = str(uuid4())
        claimed_session_id: int | None = None
        claimed_attempts = 0
        for _ in range(5):
            session = self.db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == session_uuid
                )
            ).scalar_one_or_none()
            if session is None or session.status != COMPLETED_SESSION_STATUS:
                self.db.rollback()
                return False
            if self.repo.get_report(session.id) is not None:
                self.db.rollback()
                return False

            generation_state = self._report_generation_state(session)
            generation_status = str(generation_state.get("status") or "")
            if generation_status == "failed":
                self.db.rollback()
                return False
            if (
                generation_status == "running"
                and self._report_generation_state_is_active(generation_state)
            ):
                self.db.rollback()
                return False

            attempts = int(generation_state.get("attempts") or 0)
            if attempts >= REPORT_GENERATION_MAX_ATTEMPTS:
                self.db.rollback()
                return False
            attempts += 1
            if self._cas_report_generation_state(
                session,
                status_value="running",
                attempts=attempts,
                lease_token=claim_token,
            ):
                claimed_session_id = session.id
                claimed_attempts = attempts
                break
        if claimed_session_id is None:
            return False

        session = self.db.execute(
            select(AssessmentSession).where(
                AssessmentSession.id == claimed_session_id
            )
        ).scalar_one()
        try:
            self._generate_scoring_and_report(session)
            self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()
            self._finish_report_generation_claim(
                claimed_session_id,
                claim_token,
                status_value="failed",
                attempts=claimed_attempts,
            )
            raise

        generated = self.repo.get_report(claimed_session_id) is not None
        self.db.rollback()
        self._finish_report_generation_claim(
            claimed_session_id,
            claim_token,
            status_value="ready" if generated else "failed",
            attempts=claimed_attempts,
        )
        return generated

    def request_report_generation(
        self,
        session_uuid: str,
    ) -> ReportGenerationResponse:
        """Atomically schedule one bounded recovery attempt for a missing report."""
        for _ in range(5):
            session = self.db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == session_uuid
                )
            ).scalar_one_or_none()
            if session is None:
                self.db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Session not found.",
                )
            if session.status != COMPLETED_SESSION_STATUS:
                self.db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Report generation is available only after session completion.",
                )
            if self.repo.get_report(session.id) is not None:
                self.db.rollback()
                return ReportGenerationResponse(
                    session_uuid=session_uuid,
                    status="ready",
                )

            generation_state = self._report_generation_state(session)
            generation_status = str(generation_state.get("status") or "")
            attempts = int(generation_state.get("attempts") or 0)
            if (
                generation_status in {"scheduled", "running"}
                and self._report_generation_state_is_active(generation_state)
            ):
                self.db.rollback()
                return ReportGenerationResponse(
                    session_uuid=session_uuid,
                    status="running",
                )
            if attempts >= REPORT_GENERATION_MAX_ATTEMPTS:
                self.db.rollback()
                return ReportGenerationResponse(
                    session_uuid=session_uuid,
                    status="failed",
                )
            if self._cas_report_generation_state(
                session,
                status_value="scheduled",
                attempts=attempts,
            ):
                return ReportGenerationResponse(
                    session_uuid=session_uuid,
                    status="scheduled",
                )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report generation state changed concurrently; retry shortly.",
        )

    @staticmethod
    def _report_generation_state(session: AssessmentSession) -> dict[str, Any]:
        payload = session.interview_state_json or {}
        value = payload.get(REPORT_GENERATION_STATE_KEY)
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _report_generation_state_is_active(value: dict[str, Any]) -> bool:
        updated_at = value.get("updated_at")
        if not isinstance(updated_at, str) or not updated_at:
            return False
        try:
            updated = datetime.fromisoformat(updated_at.removesuffix("Z"))
        except ValueError:
            return False
        return (
            datetime.utcnow() - updated
        ).total_seconds() < REPORT_GENERATION_ACTIVE_LEASE_SECONDS

    def _cas_report_generation_state(
        self,
        session: AssessmentSession,
        *,
        status_value: str,
        attempts: int,
        lease_token: str | None = None,
    ) -> bool:
        payload = dict(session.interview_state_json or {})
        generation_state: dict[str, Any] = {
            "status": status_value,
            "attempts": attempts,
            "max_attempts": REPORT_GENERATION_MAX_ATTEMPTS,
            "lease_seconds": REPORT_GENERATION_ACTIVE_LEASE_SECONDS,
            "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        if lease_token is not None:
            generation_state["lease_token"] = lease_token
        payload[REPORT_GENERATION_STATE_KEY] = generation_state
        expected_version = int(session.state_version or 0)
        result = self.db.execute(
            update(AssessmentSession)
            .where(
                AssessmentSession.id == session.id,
                AssessmentSession.state_version == expected_version,
            )
            .values(
                interview_state_json=payload,
                state_version=expected_version + 1,
            )
        )
        if result.rowcount != 1:
            self.db.rollback()
            self.db.expire_all()
            return False
        self.db.commit()
        if hasattr(session, "_sa_instance_state"):
            from sqlalchemy.orm.attributes import set_committed_value

            set_committed_value(session, "interview_state_json", payload)
            set_committed_value(session, "state_version", expected_version + 1)
        else:
            session.interview_state_json = payload
            session.state_version = expected_version + 1
        return True

    def _finish_report_generation_claim(
        self,
        session_id: int,
        lease_token: str,
        *,
        status_value: str,
        attempts: int,
    ) -> bool:
        for _ in range(5):
            session = self.db.execute(
                select(AssessmentSession).where(AssessmentSession.id == session_id)
            ).scalar_one_or_none()
            if session is None:
                self.db.rollback()
                return False
            generation_state = self._report_generation_state(session)
            if generation_state.get("lease_token") != lease_token:
                self.db.rollback()
                return False
            if self._cas_report_generation_state(
                session,
                status_value=status_value,
                attempts=attempts,
            ):
                return True
        return False

    def _generate_scoring_and_report(self, session: AssessmentSession) -> None:
        """Generate scoring and report for a completed session.

        Failures are recorded as agent traces but do not block session completion.
        """
        turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if not turn.content_type.startswith("profile_")
        ]
        latest_user_turn = next(
            (turn for turn in reversed(turns) if turn.speaker == "user"),
            None,
        )

        try:
            context = self._build_agent_context(session, latest_user_turn)
        except Exception as exc:  # noqa: BLE001
            # If context building fails, we cannot score/report; record a trace and return.
            trace = AgentTrace(
                session_id=session.id,
                stage_id=session.current_stage_id,
                trigger_turn_id=latest_user_turn.id if latest_user_turn else None,
                agent_name="scoring",
                generation_mode="mock",
                ai_generation_weight=0,
                config_snapshot_json={"failure_reason": f"Context build failed: {exc}"},
                input_json={},
                output_json=None,
                raw_output=None,
                status="failed",
                error_code="CONTEXT_BUILD_ERROR",
                model_name="mock",
                duration_ms=0,
            )
            self.db.add(trace)
            self.db.flush()
            return

        flow_version = session.flow_version
        # Context is fully materialized. Release the read transaction before
        # either model call so SQLite UAT does not hold a shared lock while the
        # remote provider is running.
        self.db.commit()

        scoring_output = None
        scoring_started = perf_counter()
        try:
            scoring_output = ScoringAgent().generate(context, snapshot_type="final")
            if flow_version == "progressive_v3_3":
                scoring_output, _ = EvidenceSufficiencyService(self.db).apply_scoring(
                    session, scoring_output
                )
            elif _is_progressive_flow(flow_version):
                slot_map = (session.interview_state_json or {}).get(
                    "dimension_slots", {}
                )
                scoring_output = scoring_output.model_copy(
                    update={
                        "scores": [
                            item
                            if (slot_map.get(item.dimension_key) or {}).get("status")
                            == "sufficient"
                            else DimensionScore(
                                dimension_key=item.dimension_key,
                                score=None,
                                assessment_status="insufficient_evidence",
                                confidence=None,
                                reason=(
                                    "渐进式访谈证据槽位未达到 sufficient；"
                                    "不得把缺少回答、技术失败或轮次耗尽解释为低能力。"
                                ),
                                evidence=[],
                                scoring_source=item.scoring_source,
                            )
                            for item in scoring_output.scores
                        ],
                        "warnings": [
                            *scoring_output.warnings,
                            "progressive_v3 scores require sufficient evidence slots",
                        ],
                    }
                )
            scoring_generation_duration_ms = int(
                (perf_counter() - scoring_started) * 1000
            )
            ScoringService(self.db).persist_scoring_output(
                context,
                scoring_output,
                generation_duration_ms=scoring_generation_duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            ScoringService(self.db).persist_scoring_failure(
                context,
                error_code="SCORING_AGENT_ERROR",
                reason=str(exc),
                generation_duration_ms=int((perf_counter() - scoring_started) * 1000),
            )

        # Scoring persistence is complete. Commit before the report model call
        # to keep the write transaction short on both SQLite and MySQL.
        self.db.commit()

        report_started = perf_counter()
        try:
            if scoring_output is not None:
                report_output = ReportAgent().generate(context, scoring_output)
                if flow_version == "progressive_v3_3":
                    quality = EvidenceSufficiencyService(self.db).measurement_quality(
                        session, scoring_output.scores
                    )
                    score_by_key = {
                        item.dimension_key: item for item in scoring_output.scores
                    }
                    report_output = report_output.model_copy(
                        update={
                            "measurement_quality": quality,
                            "dimension_reports": [
                                item.model_copy(
                                    update={
                                        "evidence_sufficiency_index": score_by_key[
                                            item.dimension_key
                                        ].evidence_sufficiency_index,
                                        "evidence_sufficiency_level": score_by_key[
                                            item.dimension_key
                                        ].evidence_sufficiency_level,
                                        "score_kind": score_by_key[
                                            item.dimension_key
                                        ].score_kind,
                                        "evidence_sufficiency_note": score_by_key[
                                            item.dimension_key
                                        ].evidence_sufficiency_note,
                                        "level_label": (
                                            "未测到"
                                            if score_by_key[
                                                item.dimension_key
                                            ].score_kind
                                            == "unobserved"
                                            else item.level_label
                                        ),
                                        "strength": (
                                            "没有释放对应情境或没有获得公平作答机会。"
                                            if score_by_key[
                                                item.dimension_key
                                            ].score_kind
                                            == "unobserved"
                                            else item.strength
                                        ),
                                    }
                                )
                                for item in report_output.dimension_reports
                            ],
                        }
                    )
                ReportService(self.db).persist_report_output(
                    context,
                    report_output,
                    generation_duration_ms=int(
                        (perf_counter() - report_started) * 1000
                    ),
                )
            else:
                ReportService(self.db).persist_report_failure(
                    context,
                    error_code="REPORT_SKIPPED",
                    reason="Scoring failed before report generation.",
                    generation_duration_ms=int(
                        (perf_counter() - report_started) * 1000
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            ReportService(self.db).persist_report_failure(
                context,
                error_code="REPORT_AGENT_ERROR",
                reason=str(exc),
                generation_duration_ms=int((perf_counter() - report_started) * 1000),
            )

    def submit_feedback(
        self,
        session_uuid: str,
        payload: SubmitFeedbackRequest,
    ) -> FeedbackResponse:
        session = self._get_session_or_404(session_uuid)
        feedback = self.repo.get_feedback(session.id)
        values = payload.model_dump()
        if feedback is None:
            feedback = SessionFeedback(
                session_id=session.id,
                status="active",
                metadata_json={"source": "assessment_report_page"},
                **values,
            )
            self.db.add(feedback)
        else:
            for key, value in values.items():
                setattr(feedback, key, value)
            feedback.status = "active"
            feedback.metadata_json = {
                **(feedback.metadata_json or {}),
                "source": "assessment_report_page",
                "updated_from_frontend": True,
            }
        self.db.commit()
        self.db.refresh(feedback)
        return self._feedback_response(session, feedback)

    def get_feedback(self, session_uuid: str) -> FeedbackStateResponse:
        session = self._get_session_or_404(session_uuid)
        feedback = self.repo.get_feedback(session.id)
        if feedback is None:
            return FeedbackStateResponse(
                session_uuid=session.session_uuid,
                submitted=False,
                feedback=None,
            )
        return FeedbackStateResponse(
            session_uuid=session.session_uuid,
            submitted=True,
            feedback=self._feedback_response(session, feedback),
        )

    def get_report(self, session_uuid: str) -> ReportResponse:
        session = self._get_session_or_404(session_uuid)
        report = self.repo.get_report(session.id)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report has not been generated yet.",
            )
        participant = self.repo.get_participant(session.participant_id)
        report_json = _redact_report_occupation(
            report.report_json,
            participant.career_direction if participant else None,
        )
        report_json = self._with_measurement_quality(session, report_json)
        return ReportResponse(
            session_uuid=session.session_uuid,
            status=report.status,
            report=report_json,
        )

    def _with_measurement_quality(
        self,
        session: AssessmentSession,
        report_json: dict[str, Any],
    ) -> dict[str, Any]:
        if _is_progressive_flow(session.flow_version):
            quality = EvidenceSufficiencyService(self.db).measurement_quality(session)
            persisted_quality = report_json.get("measurement_quality") or {}
            if (
                session.flow_version == "progressive_v3_3"
                and persisted_quality.get("overall_evidence_sufficiency_index")
                is not None
            ):
                quality = quality.model_copy(
                    update={
                        "overall_evidence_sufficiency_index": persisted_quality[
                            "overall_evidence_sufficiency_index"
                        ]
                    }
                )
            report_json = {
                **report_json,
                "measurement_quality": quality.model_dump(mode="json"),
            }
            if quality.status == "invalid":
                # Keep the persisted raw report unchanged, but do not expose
                # interpretive results from an invalid measurement process.
                report_json = {
                    **report_json,
                    "summary": "测评过程异常，结果不宜解释，建议重新测评。",
                    "overall_level": "结果无效",
                    "dimension_reports": [],
                    "dimension_scores": [],
                    "advantages": [],
                    "strengths": [],
                    "improvement_suggestions": [],
                    "development_plan": [],
                }
            elif quality.status == "caution" and (
                quality.unobserved_dimensions or quality.provisional_dimensions
            ):
                report_json = {
                    **report_json,
                    "summary": ("本次仅形成部分维度结果；未测到或关键评分证据未达标的维度" "不作能力判断，请结合证据基础指数谨慎解释。"),
                    "overall_level": "部分结果",
                }
        return report_json

    def get_report_pdf(
        self,
        session_uuid: str,
        *,
        timezone_name: str = "Asia/Shanghai",
    ) -> tuple[bytes, str]:
        session = self._get_session_or_404(session_uuid)
        report = self.repo.get_report(session.id)
        if report is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report has not been generated yet.",
            )

        participant = self.repo.get_participant(session.participant_id)
        scenario = self.repo.get_scenario(session.scenario_id)
        nickname = (
            participant.nickname if participant and participant.nickname else "受测者"
        )
        occupation = participant.career_direction if participant else None
        scenario_title = _report_scenario_title(
            scenario.title if scenario else None,
            occupation,
        )
        safe_nickname = re.sub(r"[\\/:*?\"<>|\s]+", "-", nickname).strip("-") or "受测者"

        try:
            report_json = self._with_measurement_quality(
                session,
                _redact_report_occupation(
                    report.report_json,
                    occupation,
                ),
            )
            content = ReportPdfService().generate(
                report=report_json,
                nickname=nickname,
                scenario_title=scenario_title,
                generated_at=report.updated_at,
                timezone_name=timezone_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "report PDF generation failed for session %s", session_uuid
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="报告 PDF 生成失败，请稍后重试。",
            ) from exc
        return content, f"审辩式思维动态测评报告-{safe_nickname}.pdf"

    @staticmethod
    def _feedback_response(
        session: AssessmentSession,
        feedback: SessionFeedback,
    ) -> FeedbackResponse:
        return FeedbackResponse(
            session_uuid=session.session_uuid,
            realism_score=feedback.realism_score,
            difficulty_score=feedback.difficulty_score,
            naturalness_score=feedback.naturalness_score,
            fatigue_score=feedback.fatigue_score,
            report_trust_score=feedback.report_trust_score,
            overall_satisfaction_score=feedback.overall_satisfaction_score,
            open_feedback=feedback.open_feedback,
            submitted_at=feedback.updated_at,
        )

    def _get_session_or_404(self, session_uuid: str) -> AssessmentSession:
        session = self.repo.get_session_by_uuid(session_uuid)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found.",
            )
        return session

    def _preparation_states(
        self,
        session: AssessmentSession,
        turns: list[DialogueTurn],
    ) -> tuple[OnboardingState, ScenarioPreparationState, str]:
        profile = self.db.execute(
            select(ParticipantProfile).where(
                ParticipantProfile.session_id == session.id
            )
        ).scalar_one_or_none()
        job = self.db.execute(
            select(ScenarioGenerationJob).where(
                ScenarioGenerationJob.session_id == session.id
            )
        ).scalar_one_or_none()
        question_count = sum(
            1 for turn in turns if turn.content_type == "profile_question"
        )
        profile_completed = bool(
            profile and (profile.ai_profile_json or {}).get("completed")
        )
        onboarding = OnboardingState(
            question_count=min(question_count, MAX_PROFILE_QUESTIONS),
            max_questions=MAX_PROFILE_QUESTIONS,
            completed=profile_completed,
        )
        if _is_consultative_flow(session.flow_version) and profile_completed:
            job_status = "skeleton_ready"
        else:
            job_status = job.status if job else "fallback"
        messages = {
            "queued": "职业基础情景正在排队准备。",
            "drafting": "正在生成与你职业背景相关的基础材料。",
            "reviewing": "正在检查情景的一致性和知识门槛。",
            "base_ready": "职业基础情景已准备，等待完成背景适配。",
            "adapting": "正在根据背景访谈做轻量适配。",
            "completed": "情景已准备完成。",
            "fallback": "个性化情景暂不可用，已切换为通用情景。",
            "skeleton_ready": "日常任务骨架已就绪，正在开始逐轮访谈。",
        }
        preparation = ScenarioPreparationState(
            status=job_status,
            cache_hit=bool(job and job.cache_hit),
            fallback_used=bool(job and job.fallback_used),
            message=messages.get(job_status),
        )
        if session.status == "completed":
            phase = "completed"
        elif (
            _is_consultative_flow(session.flow_version)
            and profile_completed
            and (session.interview_state_json or {}).get("opening_status") != "saved"
        ):
            phase = "opening_pending"
        elif session.status in {"in_progress", "generating", "created"}:
            phase = "assessment"
        elif profile_completed:
            phase = "scenario_preparing"
        else:
            phase = "onboarding"
        return onboarding, preparation, phase

    def _begin_turn_generation_or_409(self, session_uuid: str) -> AssessmentSession:
        if self.repo.try_mark_session_generating(session_uuid):
            self.db.commit()
            return self._get_session_or_404(session_uuid)

        self.db.rollback()
        session = self.repo.get_session_by_uuid(session_uuid)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found.",
            )
        if session.status == GENERATING_SESSION_STATUS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="AI response is already being generated for this session.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session status does not allow new turns: {session.status}",
        )

    @staticmethod
    def _release_generation_status(session: AssessmentSession) -> None:
        if session.status == GENERATING_SESSION_STATUS:
            session.status = "in_progress"

    def _reset_generation_status(self, session_uuid: str) -> None:
        try:
            session = self.repo.get_session_by_uuid(session_uuid)
            if session is not None and session.status == GENERATING_SESSION_STATUS:
                session.status = "in_progress"
                self.db.commit()
        except Exception:  # noqa: BLE001
            self.db.rollback()

    @staticmethod
    def _mark_session_completed(session: AssessmentSession) -> None:
        now = datetime.utcnow()
        session.status = COMPLETED_SESSION_STATUS
        session.completed_at = now
        if session.started_at:
            session.total_duration_seconds = int(
                (now - session.started_at).total_seconds()
            )

    @staticmethod
    def _build_session_progress(
        session: AssessmentSession,
        estimated_minutes: int,
        current_stage: ScenarioStage | None,
        stages: list[ScenarioStage],
        turns: list[DialogueTurn],
    ) -> SessionProgress:
        current_order = current_stage.stage_order if current_stage else None
        elapsed_seconds = (
            int((datetime.utcnow() - session.started_at).total_seconds())
            if session.started_at
            and session.status in ACTIVE_SESSION_STATUSES | {GENERATING_SESSION_STATUS}
            else session.total_duration_seconds
        )
        followup_types = {"followup_question", "dynamic_info_question"}
        progress_items: list[StageProgressItem] = []

        for stage in stages:
            stage_turns = [turn for turn in turns if turn.stage_id == stage.id]
            raw_used_followups = sum(
                1
                for turn in stage_turns
                if turn.speaker == "ai" and turn.content_type in followup_types
            )
            used_followups = min(raw_used_followups, stage.max_followups)
            used_clarifications = sum(
                1
                for turn in stage_turns
                if turn.speaker == "ai"
                and turn.content_type == "clarification_response"
            )
            skipped = any(turn.content_type == "stage_skipped" for turn in stage_turns)
            evidence_coverage = SessionService._stage_evidence_coverage(
                stage, stage_turns
            )
            missing_evidence = [
                key for key, value in evidence_coverage.items() if value != "complete"
            ]
            waiting_for_stage_choice = bool(
                stage.stage_order == current_order
                and not skipped
                and missing_evidence
                and (
                    raw_used_followups >= stage.max_followups
                    or (
                        stage_turns
                        and stage_turns[-1].content_type == "stage_incomplete_prompt"
                    )
                )
            )
            released_dynamic_info_count = sum(
                1
                for turn in stage_turns
                if turn.speaker == "ai"
                and (
                    turn.content_type in {"dynamic_info", "dynamic_info_question"}
                    or turn.dynamic_info_id is not None
                )
            )

            if skipped:
                stage_status = "skipped"
            elif session.status == "completed":
                stage_status = "completed"
            elif current_order is None:
                stage_status = "pending"
            elif stage.stage_order < current_order:
                stage_status = "completed"
            elif stage.stage_order == current_order:
                stage_status = "active"
            else:
                stage_status = "pending"

            progress_items.append(
                StageProgressItem(
                    stage_code=stage.stage_code,
                    title=stage.title,
                    stage_order=stage.stage_order,
                    status=stage_status,
                    max_followups=stage.max_followups,
                    used_followups=used_followups,
                    used_clarifications=used_clarifications,
                    can_skip=(
                        stage.stage_order == current_order
                        and bool(missing_evidence)
                        and (
                            used_clarifications >= 2
                            or raw_used_followups >= stage.max_followups
                            or waiting_for_stage_choice
                        )
                        and not skipped
                        and session.status in ACTIVE_SESSION_STATUSES
                    ),
                    skipped=skipped,
                    released_dynamic_info_count=released_dynamic_info_count,
                    estimated_minutes=stage.estimated_minutes,
                    evidence_coverage=evidence_coverage,
                    missing_evidence=missing_evidence,
                    waiting_for_stage_choice=waiting_for_stage_choice,
                )
            )

        return SessionProgress(
            total_stages=len(progress_items),
            current_stage_order=current_order,
            estimated_minutes=estimated_minutes,
            elapsed_seconds=elapsed_seconds,
            stages=progress_items,
        )

    @staticmethod
    def _build_interview_progress(session: AssessmentSession) -> InterviewProgress:
        count = int(
            (session.interview_state_json or {}).get("formal_user_turn_count", 0)
        )
        completed = session.status == COMPLETED_SESSION_STATUS
        percent = 100 if completed else min(99, round(count / 12 * 100))
        if completed or count >= 12:
            estimated_remaining_minutes = 0
        elif count < 9:
            estimated_remaining_minutes = (9 - count) * 2
        else:
            estimated_remaining_minutes = 2
        elapsed_seconds = (
            int((datetime.utcnow() - session.started_at).total_seconds())
            if session.started_at
            and session.status in ACTIVE_SESSION_STATUSES | {GENERATING_SESSION_STATUS}
            else int(session.total_duration_seconds or 0)
        )
        return InterviewProgress(
            formal_answer_count=count,
            target_min_answers=9,
            target_max_answers=12,
            percent=percent,
            estimated_remaining_minutes=estimated_remaining_minutes,
            elapsed_seconds=max(0, elapsed_seconds),
        )

    @staticmethod
    def _stage_evidence_coverage(
        stage: ScenarioStage, stage_turns: list[DialogueTurn]
    ) -> dict[str, str]:
        expected = list((stage.exit_criteria_json or {}).get("expected_evidence") or [])
        coverage = {str(item): "missing" for item in expected}
        latest_snapshot = next(
            (
                turn.analysis_json.get("resolved_evidence_snapshot")
                for turn in reversed(stage_turns)
                if turn.speaker == "user"
                and turn.analysis_json
                and isinstance(
                    turn.analysis_json.get("resolved_evidence_snapshot"), list
                )
            ),
            None,
        )
        resolved_keys: set[str] = set()
        if latest_snapshot is not None:
            for item in latest_snapshot:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("evidence_key") or "")
                state = item.get("coverage")
                if key not in coverage or state not in {
                    "covered",
                    "partial",
                    "missing",
                }:
                    continue
                coverage[key] = "complete" if state == "covered" else state
                resolved_keys.add(key)
        for turn in stage_turns:
            if turn.speaker != "user" or not turn.analysis_json:
                continue
            if not is_scoring_analysis(turn.analysis_json, text=turn.content):
                continue
            for key in turn.analysis_json.get("evidence_keys") or []:
                if key in coverage and key not in resolved_keys:
                    coverage[key] = "complete"
        return coverage

    def _generate_followup(
        self,
        session: AssessmentSession,
        trigger_turn: DialogueTurn,
        context: AgentRuntimeContext,
    ) -> tuple[FollowupOutput, AgentTrace]:
        (
            output,
            status_value,
            error_code,
            raw_output,
            duration_ms,
        ) = self._run_followup_agent(context)
        self._apply_model_resolution(session, trigger_turn, output)

        trace = self._save_agent_trace(
            session=session,
            stage_id=session.current_stage_id,
            trigger_turn_id=trigger_turn.id,
            agent_name="followup",
            input_json=context.model_dump(mode="json"),
            output_json=output.model_dump(mode="json"),
            generation_mode=output.generation_mode,
            ai_generation_weight=output.ai_generation_weight,
            status_value=status_value,
            error_code=error_code,
            raw_output=raw_output,
            duration_ms=duration_ms,
            selected_rule_code=output.selected_rule_code,
            selected_dynamic_info_code=output.selected_dynamic_info_code,
        )
        return output, trace

    @staticmethod
    def _run_followup_agent(
        context: AgentRuntimeContext,
    ) -> tuple[FollowupOutput, str, str | None, str | None, int]:
        started_at = perf_counter()
        try:
            output = FollowupAgent().generate(context)
            error_code = SessionService._followup_fallback_error_code(output)
            status_value = "failed" if error_code else output.status
            raw_output = None
        except Exception as exc:  # noqa: BLE001
            output = FollowupOutput(
                question="可以具体说说这个判断背后的主要依据吗？",
                content_type="followup_question",
                question_type="clarify",
                resolved_response_category="encourage_answer",
                reason="followup agent raised an exception; used service fallback",
                next_action="ask_followup",
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=0.4,
                fallback_used=True,
                warnings=[str(exc)],
            )
            status_value = "failed"
            error_code = "FOLLOWUP_AGENT_ERROR"
            raw_output = str(exc)
        return (
            output,
            status_value,
            error_code,
            raw_output,
            int((perf_counter() - started_at) * 1000),
        )

    @staticmethod
    def _stream_followup_agent(
        context: AgentRuntimeContext,
    ) -> Iterator[str]:
        started_at = perf_counter()

        try:
            output = FollowupAgent().generate(context)
            error_code = SessionService._followup_fallback_error_code(output)
            status_value = "failed" if error_code else output.status
            if output.next_action != "ask_followup":
                return (
                    output,
                    status_value,
                    error_code,
                    output.question,
                    int((perf_counter() - started_at) * 1000),
                )

            # FollowupAgent already returns final display text from its single
            # structured model call. A single delta preserves the streaming HTTP
            # contract without invoking the model a second time.
            yield _stream_event("agent_delta", {"delta": output.question})
            return (
                output,
                status_value,
                error_code,
                output.question,
                int((perf_counter() - started_at) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            fallback_question = "可以具体说说这个判断背后的主要依据吗？"
            output = FollowupOutput(
                question=fallback_question,
                content_type="guidance_response",
                question_type="clarify",
                resolved_response_category="encourage_answer",
                reason="followup generation failed; used service fallback",
                next_action="ask_followup",
                generation_mode="fixed_question",
                ai_generation_weight=0,
                confidence=0.4,
                fallback_used=True,
                warnings=[str(exc)],
            )
            yield _stream_event("agent_delta", {"delta": fallback_question})

            return (
                output,
                "failed",
                "FOLLOWUP_STREAM_ERROR",
                str(exc),
                int((perf_counter() - started_at) * 1000),
            )

    @staticmethod
    def _followup_fallback_error_code(output: FollowupOutput) -> str | None:
        if output.fallback_used and any(
            warning.startswith("real model failed:") for warning in output.warnings
        ):
            return "FOLLOWUP_MODEL_FALLBACK"
        return None

    def _apply_model_resolution(
        self,
        session: AssessmentSession,
        trigger_turn: DialogueTurn,
        output: FollowupOutput,
    ) -> None:
        stage = self.repo.get_stage(trigger_turn.stage_id)
        stage_turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if turn.stage_id == trigger_turn.stage_id and turn.speaker == "user"
        ]
        expected = (
            list((stage.exit_criteria_json or {}).get("expected_evidence") or [])
            if stage
            else []
        )
        previous_snapshot = next(
            (
                turn.analysis_json.get("resolved_evidence_snapshot")
                for turn in reversed(stage_turns)
                if turn.id != trigger_turn.id
                and turn.analysis_json
                and isinstance(
                    turn.analysis_json.get("resolved_evidence_snapshot"), list
                )
            ),
            [],
        )
        snapshot_by_key = {
            item.get("evidence_key"): item
            for item in previous_snapshot
            if isinstance(item, dict) and item.get("evidence_key")
        }
        provided_keys = {item.evidence_key for item in output.resolved_evidence}
        for item in output.resolved_evidence:
            snapshot_by_key[item.evidence_key] = item.model_dump(mode="json")

        for turn in stage_turns:
            analysis = dict(turn.analysis_json or {})
            retained = [
                item
                for item in analysis.get("resolved_evidence") or []
                if isinstance(item, dict)
                and item.get("evidence_key") not in provided_keys
            ]
            attached = [
                item.model_dump(mode="json")
                for item in output.resolved_evidence
                if turn.turn_index in item.supporting_turn_indexes
            ]
            analysis["resolved_evidence"] = retained + attached
            turn.analysis_json = analysis

        analysis = dict(trigger_turn.analysis_json or {})
        if output.resolved_response_category:
            analysis["resolved_response_category"] = output.resolved_response_category
            analysis["analysis_source"] = "deepseek"
            analysis["analysis_status"] = "resolved"
            if output.resolved_response_category in {
                "clarify_question",
                "explain_term",
            }:
                session.language_mode = "plain"
        elif output.fallback_used:
            analysis["analysis_source"] = "deepseek"
            analysis["analysis_status"] = "failed"
        if output.category_correction_reason:
            analysis["category_correction_reason"] = output.category_correction_reason
        analysis["resolved_evidence_snapshot"] = [
            snapshot_by_key[key] for key in expected if key in snapshot_by_key
        ]
        trigger_turn.analysis_json = analysis
        self.db.flush()

    def _persist_followup_result(
        self,
        session: AssessmentSession,
        trigger_turn: DialogueTurn,
        output: FollowupOutput,
        trace: AgentTrace,
    ) -> str:
        if output.next_action == "advance_stage":
            transition_stage = self.repo.get_stage(trigger_turn.stage_id)
            self._record_stage_transition(
                session,
                trigger_turn,
                transition_stage,
                output.transition_reason or "evidence_complete",
            )
            next_stage = self._advance_to_next_stage(session, trigger_turn)
            if next_stage is not None:
                return "wait_user_answer"
            self._persist_ai_followup_turn(session, output, trace)
            self._mark_session_completed(session)
            return "generate_report"

        if output.next_action == "finish_ready":
            transition_stage = self.repo.get_stage(trigger_turn.stage_id)
            self._record_stage_transition(
                session,
                trigger_turn,
                transition_stage,
                output.transition_reason or "evidence_complete",
            )
            self._persist_ai_followup_turn(session, output, trace)
            self._mark_session_completed(session)
            return "generate_report"

        self._persist_ai_followup_turn(session, output, trace)
        return output.next_action

    def _record_stage_transition(
        self,
        session: AssessmentSession,
        trigger_turn: DialogueTurn,
        stage: ScenarioStage | None,
        reason: str,
    ) -> None:
        stage_turns = (
            [
                turn
                for turn in self.repo.list_turns(session.id)
                if turn.stage_id == trigger_turn.stage_id
            ]
            if stage
            else []
        )
        coverage = self._stage_evidence_coverage(stage, stage_turns) if stage else {}
        missing = [key for key, value in coverage.items() if value != "complete"]
        formal_followups = sum(
            1
            for turn in stage_turns
            if turn.speaker == "ai"
            and turn.content_type in {"followup_question", "dynamic_info_question"}
        )
        analysis = dict(trigger_turn.analysis_json or {})
        analysis["stage_transition"] = {
            "reason": reason,
            "evidence_coverage": coverage,
            "missing_evidence": missing,
            "formal_followups_used": min(
                formal_followups,
                stage.max_followups if stage else formal_followups,
            ),
            "max_followups": stage.max_followups if stage else None,
        }
        trigger_turn.analysis_json = analysis
        self.db.flush()

    def _persist_ai_followup_turn(
        self,
        session: AssessmentSession,
        output: FollowupOutput,
        trace: AgentTrace,
    ) -> DialogueTurn:
        selected_info_id = trace.selected_dynamic_info_id
        selected_rule_id = trace.selected_rule_id
        if selected_info_id is not None and output.released_dynamic_info_text:
            dynamic_turn = DialogueTurn(
                session_id=session.id,
                stage_id=session.current_stage_id,
                turn_index=self.repo.next_turn_index(session.id),
                speaker="ai",
                content=_strip_dynamic_info_prefix(output.released_dynamic_info_text),
                content_type="dynamic_info",
                source_agent_trace_id=trace.id,
                dynamic_info_id=selected_info_id,
            )
            self.db.add(dynamic_turn)
            self.db.flush()

        visible_content_type = (
            "followup_question"
            if output.content_type == "dynamic_info_question"
            else output.content_type
        )
        ai_turn = DialogueTurn(
            session_id=session.id,
            stage_id=session.current_stage_id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="ai",
            content=output.question,
            content_type=visible_content_type,
            source_agent_trace_id=trace.id,
            dynamic_info_id=None,
            intervention_rule_id=selected_rule_id,
        )
        self.db.add(ai_turn)
        self.db.flush()
        return ai_turn

    def _advance_to_next_stage(
        self,
        session: AssessmentSession,
        trigger_turn: DialogueTurn,
    ) -> ScenarioStage | None:
        current_stage = self.repo.get_stage(session.current_stage_id)
        if current_stage is None:
            return None

        next_stage = self.repo.get_next_active_stage(
            session.scenario_id,
            current_stage.stage_order,
        )
        if next_stage is None:
            return None

        session.current_stage_id = next_stage.id
        self.db.flush()
        host_context = self._build_agent_context(session, latest_user_turn=None)

        started_at = perf_counter()
        try:
            output = HostAgent().generate(host_context)
            status_value = output.status
            error_code = None
            raw_output = None
        except Exception as exc:  # noqa: BLE001
            output = HostOutput(
                stage_code=next_stage.stage_code,
                message=self._build_opening_message(
                    host_context.participant.nickname or "受测者",
                    host_context.scenario.background,
                    next_stage.context,
                    next_stage.main_question,
                ),
                content_type="stage_question",
                generation_mode=next_stage.context_generation_mode,
                ai_generation_weight=next_stage.context_ai_weight,
                reason="host agent raised an exception; used service fallback",
                next_action="wait_user_answer",
                fallback_used=True,
                warnings=[str(exc)],
            )
            status_value = "failed"
            error_code = "HOST_AGENT_ERROR"
            raw_output = str(exc)

        output = output.model_copy(
            update={
                "message": self._build_stage_opening_message(next_stage),
                "reason": (
                    f"{output.reason or 'host transition generated'}; "
                    "configured stage facts and question enforced"
                ),
            }
        )

        trace = self._save_agent_trace(
            session=session,
            stage_id=next_stage.id,
            trigger_turn_id=trigger_turn.id,
            agent_name="host",
            input_json=host_context.model_dump(mode="json"),
            output_json=output.model_dump(mode="json"),
            generation_mode=output.generation_mode,
            ai_generation_weight=output.ai_generation_weight,
            status_value=status_value,
            error_code=error_code,
            raw_output=raw_output,
            duration_ms=int((perf_counter() - started_at) * 1000),
            selected_rule_code=None,
            selected_dynamic_info_code=None,
        )

        ai_turn = DialogueTurn(
            session_id=session.id,
            stage_id=next_stage.id,
            turn_index=self.repo.next_turn_index(session.id),
            speaker="ai",
            content=output.message,
            content_type=output.content_type,
            source_agent_trace_id=trace.id,
        )
        self.db.add(ai_turn)
        self.db.flush()
        if next_stage.stage_code == "s5_dynamic_adjustment":
            infos = self.repo.list_active_dynamic_infos(next_stage.id)
            if infos:
                previous_answer = trigger_turn.content
                selected_info = next(
                    (
                        info
                        for info in infos
                        if info.info_code == "key_user_positive_feedback"
                        and any(word in previous_answer for word in ("延期", "暂停", "不上线"))
                    ),
                    infos[0],
                )
                dynamic_turn = DialogueTurn(
                    session_id=session.id,
                    stage_id=next_stage.id,
                    turn_index=self.repo.next_turn_index(session.id),
                    speaker="ai",
                    content=selected_info.content,
                    content_type="dynamic_info",
                    dynamic_info_id=selected_info.id,
                )
                self.db.add(dynamic_turn)
                self.db.flush()
                question_turn = DialogueTurn(
                    session_id=session.id,
                    stage_id=next_stage.id,
                    turn_index=self.repo.next_turn_index(session.id),
                    speaker="ai",
                    content=next_stage.main_question,
                    content_type="stage_question",
                )
                self.db.add(question_turn)
                self.db.flush()
        return next_stage

    def _save_agent_trace(
        self,
        *,
        session: AssessmentSession,
        stage_id: int | None,
        trigger_turn_id: int | None,
        agent_name: str,
        input_json: dict[str, Any],
        output_json: dict[str, Any],
        generation_mode: str | None,
        ai_generation_weight: int | None,
        status_value: str,
        error_code: str | None,
        raw_output: str | None,
        duration_ms: int,
        selected_rule_code: str | None,
        selected_dynamic_info_code: str | None,
    ) -> AgentTrace:
        selected_rule = (
            self.repo.get_intervention_rule_by_code(stage_id, selected_rule_code)
            if stage_id
            else None
        )
        selected_info = (
            self.repo.get_dynamic_info_by_code(stage_id, selected_dynamic_info_code)
            if stage_id
            else None
        )
        trace = AgentTrace(
            session_id=session.id,
            stage_id=stage_id,
            trigger_turn_id=trigger_turn_id,
            agent_name=agent_name,
            generation_mode=generation_mode,
            ai_generation_weight=ai_generation_weight,
            config_snapshot_json={
                "selected_rule_code": selected_rule_code,
                "selected_dynamic_info_code": selected_dynamic_info_code,
            },
            input_json=input_json,
            output_json=output_json,
            raw_output=raw_output,
            status=status_value,
            error_code=error_code,
            model_name=None,
            duration_ms=duration_ms,
            selected_dynamic_info_id=selected_info.id if selected_info else None,
            selected_rule_id=selected_rule.id if selected_rule else None,
        )
        self.db.add(trace)
        self.db.flush()
        return trace

    def _prepare_user_turn_context(
        self, session: AssessmentSession, turn: DialogueTurn
    ) -> AgentRuntimeContext:
        context = self._build_agent_context(session, turn)
        if get_settings().MODEL_GATEWAY_MODE.lower() == "real":
            analysis = {
                "analysis_source": "deepseek",
                "analysis_status": "pending",
            }
        else:
            analysis = analyze_user_turn(context, turn.content)
        turn.analysis_json = analysis
        if get_settings().MODEL_GATEWAY_MODE.lower() != "real" and analysis.get(
            "needs_plain_language"
        ):
            session.language_mode = "plain"
        self.db.flush()
        return self._build_agent_context(session, turn)

    def _build_agent_context(
        self,
        session: AssessmentSession,
        latest_user_turn: DialogueTurn | None,
    ) -> AgentRuntimeContext:
        if latest_user_turn and latest_user_turn.content_type.startswith("profile_"):
            latest_user_turn = None
        participant = self.repo.get_participant(session.participant_id)
        scenario = self.repo.get_scenario(session.scenario_id)
        stage = self.repo.get_stage(session.current_stage_id)
        if participant is None or scenario is None or stage is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Session references missing participant, scenario, or stage.",
            )

        stages = self.repo.list_active_stages(session.scenario_id)
        stage_code_by_id = {item.id: item.stage_code for item in stages}
        turns = [
            turn
            for turn in self.repo.list_turns(session.id)
            if not turn.content_type.startswith("profile_")
        ]
        dynamic_infos = self.repo.list_active_dynamic_infos(stage.id)
        intervention_rules = self.repo.list_active_intervention_rules(stage.id)
        dynamic_dimension_map = self.repo.list_dynamic_info_dimension_keys(stage.id)
        rule_dimension_map = self.repo.list_rule_dimension_keys(stage.id)
        stage_dimension_bindings = self.repo.list_stage_dimension_bindings(
            session.scenario_id
        )
        dynamic_info_code_by_id = {info.id: info.info_code for info in dynamic_infos}

        return AgentRuntimeContext(
            session=SessionContext(
                session_id=session.id,
                session_uuid=session.session_uuid,
                assessment_mode=session.assessment_mode,
                status=session.status,
                language_mode=session.language_mode,
            ),
            participant=ParticipantContext(
                participant_id=participant.id,
                nickname=participant.nickname,
                profile_summary=participant.self_description,
            ),
            scenario=ScenarioContext(
                scenario_id=scenario.id,
                scenario_code=scenario.scenario_code,
                title=scenario.title,
                background=scenario.background,
            ),
            stage=StageContext(
                stage_id=stage.id,
                stage_code=stage.stage_code,
                stage_order=stage.stage_order,
                title=stage.title,
                stage_goal=stage.stage_goal,
                context=stage.context,
                main_question=stage.main_question,
                context_generation_mode=stage.context_generation_mode,
                context_ai_weight=stage.context_ai_weight,
                max_followups=stage.max_followups,
                estimated_minutes=stage.estimated_minutes,
                exit_criteria=stage.exit_criteria_json or {},
            ),
            dialogue_history=[
                DialogueTurnContext(
                    turn_id=turn.id,
                    turn_index=turn.turn_index,
                    stage_id=turn.stage_id,
                    stage_code=stage_code_by_id.get(turn.stage_id),
                    speaker=turn.speaker,
                    content=turn.content,
                    content_type=turn.content_type,
                    dynamic_info_id=turn.dynamic_info_id,
                    selected_dynamic_info_code=dynamic_info_code_by_id.get(
                        turn.dynamic_info_id
                    ),
                    analysis_json=turn.analysis_json,
                )
                for turn in turns
            ],
            rubric_dimensions=[
                RubricDimensionContext(
                    dimension_key=dimension.dimension_key,
                    name=dimension.name,
                    definition=dimension.definition,
                    observable_behaviors=_json_list(dimension.observable_behaviors),
                    invalid_evidence_desc=dimension.invalid_evidence_desc,
                )
                for dimension in self.repo.list_active_rubric_dimensions()
            ],
            rubric_anchors=[
                RubricAnchorContext(
                    dimension_key=dimension_key,
                    score_level=anchor.score_level,
                    level_name=anchor.level_name,
                    behavior_desc=anchor.behavior_desc,
                    evidence_examples=_optional_json_list(anchor.evidence_examples),
                    counter_examples=_optional_json_list(anchor.counter_examples),
                )
                for anchor, dimension_key in self.repo.list_active_rubric_anchors()
            ],
            stage_dimension_bindings=[
                StageDimensionBindingContext(
                    stage_code=stage_code,
                    dimension_key=dimension_key,
                    observe_role=observe_role,
                    weight=weight,
                )
                for (
                    stage_code,
                    dimension_key,
                    observe_role,
                    weight,
                ) in stage_dimension_bindings
            ],
            candidate_dynamic_infos=[
                DynamicInfoContext(
                    dynamic_info_id=info.id,
                    info_code=info.info_code,
                    title=info.title,
                    content=info.content,
                    info_type=info.info_type,
                    trigger_condition=info.trigger_condition,
                    priority=info.priority,
                    target_dimensions=dynamic_dimension_map.get(info.id, []),
                )
                for info in dynamic_infos
            ],
            candidate_intervention_rules=[
                InterventionRuleContext(
                    rule_id=rule.id,
                    rule_code=rule.rule_code,
                    rule_type=rule.rule_type,
                    trigger_condition=rule.trigger_condition,
                    strategy_direction=rule.strategy_direction,
                    sample_question=rule.sample_question,
                    question_generation_mode=rule.question_generation_mode,
                    question_ai_weight=rule.question_ai_weight,
                    question_generation_constraints_json=rule.question_generation_constraints_json,
                    fallback_question=rule.fallback_question,
                    exit_prompt=rule.exit_prompt,
                    priority=rule.priority,
                    max_use_count=rule.max_use_count,
                    target_dimensions=rule_dimension_map.get(rule.id, []),
                )
                for rule in intervention_rules
            ],
            latest_user_turn=(
                DialogueTurnContext(
                    turn_id=latest_user_turn.id,
                    turn_index=latest_user_turn.turn_index,
                    stage_id=latest_user_turn.stage_id,
                    stage_code=stage_code_by_id.get(latest_user_turn.stage_id),
                    speaker=latest_user_turn.speaker,
                    content=latest_user_turn.content,
                    content_type=latest_user_turn.content_type,
                    dynamic_info_id=latest_user_turn.dynamic_info_id,
                    selected_dynamic_info_code=dynamic_info_code_by_id.get(
                        latest_user_turn.dynamic_info_id
                    ),
                    analysis_json=latest_user_turn.analysis_json,
                )
                if latest_user_turn is not None
                else None
            ),
        )

    @staticmethod
    def _build_opening_message(
        nickname: str,
        scenario_background: str,
        stage_context: str,
        main_question: str,
    ) -> str:
        return (
            f"{nickname}\uff0c\u4f60\u597d\u3002\u63a5\u4e0b\u6765\u6211\u4f1a\u7ed9\u4f60\u4e00\u4e2a\u7ba1\u7406\u51b3\u7b56\u60c5\u5883\uff0c"
            "\u8bf7\u6309\u7167\u4f60\u7684\u771f\u5b9e\u60f3\u6cd5\u56de\u7b54\uff0c\u4e0d\u9700\u8981\u8ffd\u6c42\u6807\u51c6\u7b54\u6848\u3002"
            f"\n\n\u3010\u60c5\u5883\u80cc\u666f\u3011\n{scenario_background}"
            f"\n\n\u3010\u5f53\u524d\u4fe1\u606f\u3011\n{stage_context}"
            f"\n\n\u3010\u95ee\u9898\u3011\n{main_question}"
        )

    @staticmethod
    def _build_stage_opening_message(stage: ScenarioStage) -> str:
        if stage.stage_code == "s5_dynamic_adjustment":
            return (
                f"上一部分已结束。现在进入第 {stage.stage_order} 部分：{stage.title}。"
                f"\n\n【当前已知信息】\n{stage.context}"
            )
        return (
            f"上一部分已结束。现在进入第 {stage.stage_order} 部分：{stage.title}。"
            f"\n\n【当前已知信息】\n{stage.context}"
            f"\n\n【当前问题】\n{stage.main_question}"
        )


def _compact_humanistic_runtime_fallback(
    output: InterviewerOutput,
    *,
    plan: InterviewPlanOutput,
    blueprint: Any,
    context: AgentRuntimeContext,
) -> InterviewerOutput:
    """Keep online deterministic event fallback inside the frozen contract."""
    if plan.action != "RELEASE_EVENT":
        return output
    sentence_marks = len(re.findall(r"[。！？!?]", output.message))
    if len(output.message) <= 90 and sentence_marks <= 2:
        return output
    event = next(
        (
            item
            for item in blueprint.event_cards
            if item.event_code == plan.release_event_code
        ),
        None,
    )
    unit = InterviewerAgent._selected_unit(  # noqa: SLF001
        event,
        plan.release_unit_code,
    )
    if unit is None:
        return output
    fact = unit.text.rstrip("。！？!?")
    question_match = re.search(r"([^。！？!?;\n]+[？?])\s*$", output.message)
    short_question = (
        question_match.group(1) if question_match is not None else "这会让你怎么调整？"
    )
    selected_frame = InterviewerAgent.event_intro_frame_audit(
        context,
        event_turn=True,
    )["selected_event_intro_frame"]
    updates: dict[str, Any] = {
        "warnings": [*output.warnings, "runtime event fallback compacted"],
    }
    candidate = InterviewerAgent._event_intro_message(  # noqa: SLF001
        frame=str(selected_frame),
        reason="为了继续判断",
        fact=fact,
        question=short_question,
    )
    if len(candidate) <= 90:
        updates.update(
            {
                "message": candidate,
                "reflection_turn_ids": [],
                "reflection_source_quotes": [],
            }
        )
        return output.model_copy(update=updates)
    return output


def generate_completed_session_report_background(session_uuid: str) -> None:
    """Run idempotent report generation with a fresh request-independent DB session."""
    db = get_sessionmaker()()
    try:
        SessionService(db).generate_report_if_completed(session_uuid)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception(
            "Background report generation failed for session %s",
            session_uuid,
        )
    finally:
        db.close()


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, dict):
        return [f"{key}: {item}" for key, item in value.items()]
    if value is None:
        return []
    return [str(value)]


def _optional_json_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return _json_list(value)


def _stream_event(event: str, payload: dict[str, Any]) -> str:
    return json.dumps({"event": event, **payload}, ensure_ascii=False) + "\n"


def _strip_dynamic_info_prefix(text: str) -> str:
    cleaned = text.strip()
    for prefix in ("现在补充一条新信息：", "补充信息：", "补充信息:", "新信息：", "新信息:"):
        if cleaned.startswith(prefix):
            return cleaned[len(prefix) :].strip()
    return cleaned


def _report_scenario_title(
    title: str | None,
    occupation: str | None,
) -> str:
    normalized = (title or "").strip() or "测评情境"
    redacted = _redact_report_occupation(normalized, occupation)
    if isinstance(redacted, str) and redacted.strip():
        return redacted.strip()
    return "测评情境"


def _redact_report_occupation(value: Any, occupation: str | None) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_report_occupation(item, occupation)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_report_occupation(item, occupation) for item in value]
    if isinstance(value, str) and occupation and occupation.strip():
        return value.replace(occupation, "熟悉领域")
    return value
