from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import case, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.admin import AdminUser
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.feedback import SessionFeedback
from app.models.participant import Participant
from app.models.prompt import PromptTemplate
from app.models.report import AssessmentReport, ReportTemplate
from app.models.rubric import RubricAnchor, RubricDimension
from app.models.scenario import (
    Scenario,
    ScenarioGenerationJob,
    ScenarioStage,
    ScenarioStageDimension,
    StageDynamicInfo,
    StageInterventionRule,
)
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot
from app.agents.scenario_design_agent import ScenarioDesignAgent
from app.services.scenario_materialization_service import ScenarioMaterializationService
from app.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    AdminUserOut,
    DashboardAgentStatusItem,
    DashboardAnalytics,
    DashboardFeedbackAverages,
    DashboardFeedbackCommentItem,
    DashboardRecentSessionItem,
    DashboardSessionStatusItem,
    DashboardStageProgressItem,
    DashboardSummary,
    DimensionBinding,
    DimensionBindingOut,
    RubricAnchorOut,
    RubricAnchorUpdate,
    RubricDimensionOut,
    RubricDimensionUpdate,
    ScenarioListItem,
    ScenarioOut,
    ScenarioStageOut,
    ScenarioStageUpdate,
    ScenarioUpdate,
    StageDynamicInfoCreate,
    StageDynamicInfoOut,
    StageDynamicInfoUpdate,
    StageInterventionRuleCreate,
    StageInterventionRuleOut,
    StageInterventionRuleUpdate,
)

router = APIRouter(prefix="/admin", tags=["admin"])
security = HTTPBearer(auto_error=False)


def _not_found(name: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{name} not found")


def _update_model(instance: Any, payload: Any) -> Any:
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(instance, key, value)
    return instance


def _get_one_or_404(db: Session, model: type, item_id: int, name: str) -> Any:
    item = db.get(model, item_id)
    if item is None:
        raise _not_found(name)
    return item


def _assert_stage_scenario_mutable(db: Session, stage_id: int) -> ScenarioStage:
    stage = _get_one_or_404(db, ScenarioStage, stage_id, "Scenario stage")
    scenario = _get_one_or_404(db, Scenario, stage.scenario_id, "Scenario")
    if scenario.is_immutable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Generated scenarios are immutable; disable or regenerate the base scenario.",
        )
    return stage


def _get_admin_by_username(db: Session, username: str) -> AdminUser | None:
    return db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none()


def _bootstrap_admin(
    db: Session,
    request: AdminLoginRequest,
    settings: Settings,
) -> AdminUser | None:
    if not settings.ADMIN_BOOTSTRAP_ENABLED:
        return None
    if request.username != settings.ADMIN_USERNAME or request.password != settings.ADMIN_PASSWORD:
        return None

    admin = AdminUser(
        username=settings.ADMIN_USERNAME,
        password_hash=hash_password(settings.ADMIN_PASSWORD),
        display_name=settings.ADMIN_DISPLAY_NAME,
        role="ADMIN",
        status="active",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    try:
        payload = decode_access_token(credentials.credentials, settings.ADMIN_TOKEN_SECRET)
        admin_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from None

    admin = db.get(AdminUser, admin_id)
    if admin is None or admin.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive admin")
    return admin


AdminDep = Annotated[AdminUser, Depends(get_current_admin)]


@router.post("/auth/login", response_model=AdminLoginResponse)
def login(
    request: AdminLoginRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminLoginResponse:
    admin = _get_admin_by_username(db, request.username)
    if admin is None:
        admin = _bootstrap_admin(db, request, settings)

    if admin is None or not verify_password(request.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if admin.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin disabled")

    admin.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(admin)

    token = create_access_token(
        {"sub": str(admin.id), "role": admin.role, "typ": "admin"},
        settings.ADMIN_TOKEN_SECRET,
        settings.ADMIN_TOKEN_EXPIRE_MINUTES,
    )
    return AdminLoginResponse(
        access_token=token,
        expires_in=settings.ADMIN_TOKEN_EXPIRE_MINUTES * 60,
        user=AdminUserOut.model_validate(admin),
    )


@router.get("/auth/me", response_model=AdminUserOut)
def me(admin: AdminDep) -> AdminUserOut:
    return AdminUserOut.model_validate(admin)


@router.get("/dashboard/summary", response_model=DashboardSummary)
def dashboard_summary(
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> DashboardSummary:
    def count(model: type, *conditions: Any) -> int:
        statement = select(func.count()).select_from(model)
        if conditions:
            statement = statement.where(*conditions)
        return int(db.execute(statement).scalar_one())

    return DashboardSummary(
        scenario_count=count(Scenario),
        active_scenario_count=count(Scenario, Scenario.status == "active"),
        stage_count=count(ScenarioStage),
        dynamic_info_count=count(StageDynamicInfo),
        intervention_rule_count=count(StageInterventionRule),
        rubric_dimension_count=count(RubricDimension),
        rubric_anchor_count=count(RubricAnchor),
        prompt_template_count=count(PromptTemplate),
        report_template_count=count(ReportTemplate),
    )


@router.get("/dashboard/analytics", response_model=DashboardAnalytics)
def dashboard_analytics(
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> DashboardAnalytics:
    def count(model: type, *conditions: Any) -> int:
        statement = select(func.count()).select_from(model)
        if conditions:
            statement = statement.where(*conditions)
        return int(db.execute(statement).scalar_one())

    session_count = count(AssessmentSession)
    completed_session_count = count(AssessmentSession, AssessmentSession.status == "completed")
    in_progress_session_count = count(
        AssessmentSession,
        AssessmentSession.status.in_(["created", "in_progress"]),
    )
    completion_rate = (
        round(completed_session_count / session_count * 100, 1) if session_count else 0.0
    )

    average_duration_seconds = db.execute(
        select(func.avg(AssessmentSession.total_duration_seconds)).where(
            AssessmentSession.total_duration_seconds.is_not(None)
        )
    ).scalar_one()
    average_duration_minutes = (
        round(float(average_duration_seconds) / 60, 1)
        if average_duration_seconds is not None
        else None
    )

    turn_count_rows = db.execute(
        select(DialogueTurn.session_id, func.count(DialogueTurn.id)).group_by(
            DialogueTurn.session_id
        )
    ).all()
    average_turn_count = (
        round(sum(int(row[1]) for row in turn_count_rows) / len(turn_count_rows), 1)
        if turn_count_rows
        else None
    )

    dialogue_turn_count = count(DialogueTurn)
    agent_trace_count = count(AgentTrace)
    report_count = count(AssessmentReport)
    score_snapshot_count = count(ScoreSnapshot)
    score_result_count = count(ScoreResult)
    score_evidence_count = count(ScoreEvidence)
    feedback_count = count(SessionFeedback, SessionFeedback.status == "active")

    agent_success_count = count(AgentTrace, AgentTrace.status.in_(["ok", "success"]))
    agent_success_rate = (
        round(agent_success_count / agent_trace_count * 100, 1) if agent_trace_count else None
    )
    feedback_coverage_rate = (
        round(feedback_count / completed_session_count * 100, 1)
        if completed_session_count
        else 0.0
    )

    feedback_avg_row = db.execute(
        select(
            func.avg(SessionFeedback.realism_score),
            func.avg(SessionFeedback.difficulty_score),
            func.avg(SessionFeedback.naturalness_score),
            func.avg(SessionFeedback.fatigue_score),
            func.avg(SessionFeedback.report_trust_score),
            func.avg(SessionFeedback.overall_satisfaction_score),
        ).where(SessionFeedback.status == "active")
    ).one()
    feedback_averages = DashboardFeedbackAverages(
        realism_score=_round_optional(feedback_avg_row[0]),
        difficulty_score=_round_optional(feedback_avg_row[1]),
        naturalness_score=_round_optional(feedback_avg_row[2]),
        fatigue_score=_round_optional(feedback_avg_row[3]),
        report_trust_score=_round_optional(feedback_avg_row[4]),
        overall_satisfaction_score=_round_optional(feedback_avg_row[5]),
    )
    low_satisfaction_count = count(
        SessionFeedback,
        SessionFeedback.status == "active",
        SessionFeedback.overall_satisfaction_score <= 2,
    )

    status_rows = db.execute(
        select(AssessmentSession.status, func.count(AssessmentSession.id))
        .group_by(AssessmentSession.status)
        .order_by(AssessmentSession.status)
    ).all()
    status_distribution = [
        DashboardSessionStatusItem(status=str(status), count=int(row_count))
        for status, row_count in status_rows
    ]

    agent_status_rows = db.execute(
        select(AgentTrace.status, func.count(AgentTrace.id))
        .group_by(AgentTrace.status)
        .order_by(AgentTrace.status)
    ).all()
    agent_status_distribution = [
        DashboardAgentStatusItem(status=str(status), count=int(row_count))
        for status, row_count in agent_status_rows
    ]

    recent_rows = db.execute(
        select(
            AssessmentSession,
            Participant.nickname,
            Scenario.title,
            func.count(distinct(DialogueTurn.id)).label("turn_count"),
            func.count(distinct(AgentTrace.id)).label("agent_trace_count"),
            AssessmentReport.status.label("report_status"),
        )
        .join(Participant, AssessmentSession.participant_id == Participant.id)
        .join(Scenario, AssessmentSession.scenario_id == Scenario.id)
        .outerjoin(DialogueTurn, DialogueTurn.session_id == AssessmentSession.id)
        .outerjoin(AgentTrace, AgentTrace.session_id == AssessmentSession.id)
        .outerjoin(AssessmentReport, AssessmentReport.session_id == AssessmentSession.id)
        .group_by(
            AssessmentSession.id,
            Participant.nickname,
            Scenario.title,
            AssessmentReport.status,
        )
        .order_by(AssessmentSession.updated_at.desc())
        .limit(8)
    ).all()
    recent_sessions = [
        DashboardRecentSessionItem(
            session_uuid=session.session_uuid,
            nickname=nickname,
            scenario_title=scenario_title,
            status=session.status,
            assessment_mode=session.assessment_mode,
            turn_count=int(turn_count),
            agent_trace_count=int(trace_count),
            report_status=report_status,
            duration_minutes=(
                round(session.total_duration_seconds / 60, 1)
                if session.total_duration_seconds is not None
                else None
            ),
            started_at=session.started_at,
            updated_at=session.updated_at,
        )
        for session, nickname, scenario_title, turn_count, trace_count, report_status in recent_rows
    ]

    stage_rows = db.execute(
        select(
            ScenarioStage.title,
            func.count(distinct(case((DialogueTurn.speaker == "ai", DialogueTurn.id)))).label(
                "ai_turn_count"
            ),
            func.count(distinct(case((DialogueTurn.speaker == "user", DialogueTurn.id)))).label(
                "user_turn_count"
            ),
            func.count(distinct(AgentTrace.id)).label("trace_count"),
        )
        .outerjoin(DialogueTurn, DialogueTurn.stage_id == ScenarioStage.id)
        .outerjoin(AgentTrace, AgentTrace.stage_id == ScenarioStage.id)
        .group_by(ScenarioStage.id, ScenarioStage.title, ScenarioStage.stage_order)
        .order_by(ScenarioStage.stage_order)
    ).all()
    stage_progress = [
        DashboardStageProgressItem(
            stage_title=title,
            ai_turn_count=int(ai_turn_count or 0),
            user_turn_count=int(user_turn_count or 0),
            trace_count=int(trace_count or 0),
        )
        for title, ai_turn_count, user_turn_count, trace_count in stage_rows
    ]

    feedback_rows = db.execute(
        select(SessionFeedback, Participant.nickname)
        .join(AssessmentSession, SessionFeedback.session_id == AssessmentSession.id)
        .join(Participant, AssessmentSession.participant_id == Participant.id)
        .where(
            SessionFeedback.status == "active",
            SessionFeedback.open_feedback.is_not(None),
            SessionFeedback.open_feedback != "",
        )
        .order_by(SessionFeedback.updated_at.desc())
        .limit(6)
    ).all()
    recent_feedback_comments = [
        DashboardFeedbackCommentItem(
            nickname=nickname,
            overall_satisfaction_score=feedback.overall_satisfaction_score,
            naturalness_score=feedback.naturalness_score,
            report_trust_score=feedback.report_trust_score,
            open_feedback=feedback.open_feedback or "",
            submitted_at=feedback.updated_at,
        )
        for feedback, nickname in feedback_rows
    ]

    return DashboardAnalytics(
        session_count=session_count,
        completed_session_count=completed_session_count,
        in_progress_session_count=in_progress_session_count,
        completion_rate=completion_rate,
        average_duration_minutes=average_duration_minutes,
        average_turn_count=average_turn_count,
        dialogue_turn_count=dialogue_turn_count,
        agent_trace_count=agent_trace_count,
        agent_success_rate=agent_success_rate,
        report_count=report_count,
        score_snapshot_count=score_snapshot_count,
        score_result_count=score_result_count,
        score_evidence_count=score_evidence_count,
        status_distribution=status_distribution,
        agent_status_distribution=agent_status_distribution,
        recent_sessions=recent_sessions,
        stage_progress=stage_progress,
        feedback_count=feedback_count,
        feedback_coverage_rate=feedback_coverage_rate,
        feedback_averages=feedback_averages,
        low_satisfaction_count=low_satisfaction_count,
        recent_feedback_comments=recent_feedback_comments,
    )


def _round_optional(value: Any) -> float | None:
    return round(float(value), 2) if value is not None else None


def _dimension_bindings(db: Session, stage_id: int) -> list[DimensionBindingOut]:
    rows = db.execute(
        select(ScenarioStageDimension, RubricDimension)
        .join(RubricDimension, ScenarioStageDimension.dimension_id == RubricDimension.id)
        .where(ScenarioStageDimension.stage_id == stage_id)
        .order_by(ScenarioStageDimension.observe_role, RubricDimension.id)
    ).all()
    return [
        DimensionBindingOut(
            dimension_id=binding.dimension_id,
            observe_role=binding.observe_role,
            weight=float(binding.weight) if binding.weight is not None else None,
            dimension_key=dimension.dimension_key,
            dimension_name=dimension.name,
        )
        for binding, dimension in rows
    ]


def _stage_out(db: Session, stage: ScenarioStage) -> ScenarioStageOut:
    return ScenarioStageOut.model_validate(
        {
            **stage.__dict__,
            "dimensions": _dimension_bindings(db, stage.id),
        }
    )


@router.get("/rubric-dimensions", response_model=list[RubricDimensionOut])
def list_rubric_dimensions(
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> list[RubricDimensionOut]:
    dimensions = db.execute(select(RubricDimension).order_by(RubricDimension.id)).scalars().all()
    if not dimensions:
        return []

    anchors = db.execute(
        select(RubricAnchor)
        .where(RubricAnchor.dimension_id.in_([item.id for item in dimensions]))
        .order_by(RubricAnchor.dimension_id, RubricAnchor.score_level)
    ).scalars().all()
    anchors_by_dimension: dict[int, list[RubricAnchorOut]] = {}
    for anchor in anchors:
        anchors_by_dimension.setdefault(anchor.dimension_id, []).append(
            RubricAnchorOut.model_validate(anchor)
        )

    return [
        RubricDimensionOut.model_validate(
            {
                **dimension.__dict__,
                "anchors": anchors_by_dimension.get(dimension.id, []),
            }
        )
        for dimension in dimensions
    ]


@router.put("/rubric-dimensions/{dimension_id}", response_model=RubricDimensionOut)
def update_rubric_dimension(
    dimension_id: int,
    payload: RubricDimensionUpdate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> RubricDimensionOut:
    dimension = _get_one_or_404(db, RubricDimension, dimension_id, "Rubric dimension")
    _update_model(dimension, payload)
    db.commit()
    db.refresh(dimension)
    anchors = db.execute(
        select(RubricAnchor)
        .where(RubricAnchor.dimension_id == dimension.id)
        .order_by(RubricAnchor.score_level)
    ).scalars().all()
    return RubricDimensionOut.model_validate(
        {
            **dimension.__dict__,
            "anchors": [RubricAnchorOut.model_validate(anchor) for anchor in anchors],
        }
    )


@router.put("/rubric-anchors/{anchor_id}", response_model=RubricAnchorOut)
def update_rubric_anchor(
    anchor_id: int,
    payload: RubricAnchorUpdate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> RubricAnchorOut:
    anchor = _get_one_or_404(db, RubricAnchor, anchor_id, "Rubric anchor")
    _update_model(anchor, payload)
    db.commit()
    db.refresh(anchor)
    return RubricAnchorOut.model_validate(anchor)


@router.get("/scenarios", response_model=list[ScenarioListItem])
def list_scenarios(
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> list[ScenarioListItem]:
    scenarios = db.execute(select(Scenario).order_by(Scenario.id)).scalars().all()
    results: list[ScenarioListItem] = []
    for scenario in scenarios:
        stage_count = db.execute(
            select(func.count())
            .select_from(ScenarioStage)
            .where(ScenarioStage.scenario_id == scenario.id)
        ).scalar_one()
        usage_count, last_used_at = db.execute(
            select(
                func.count(distinct(AssessmentSession.id)),
                func.max(AssessmentSession.started_at),
            )
            .outerjoin(
                ScenarioGenerationJob,
                ScenarioGenerationJob.session_id == AssessmentSession.id,
            )
            .where(
                or_(
                    AssessmentSession.scenario_id == scenario.id,
                    ScenarioGenerationJob.base_scenario_id == scenario.id,
                )
            )
        ).one()
        results.append(
            ScenarioListItem(
                id=scenario.id,
                scenario_code=scenario.scenario_code,
                title=scenario.title,
                target_audience=scenario.target_audience,
                scenario_type=scenario.scenario_type,
                difficulty_level=scenario.difficulty_level,
                estimated_minutes=scenario.estimated_minutes,
                rotation_weight=scenario.rotation_weight,
                is_default=scenario.is_default,
                version=scenario.version,
                status=scenario.status,
                stage_count=int(stage_count),
                updated_at=scenario.updated_at,
                source_type=scenario.source_type,
                occupation_category=scenario.occupation_category,
                occupation=(scenario.generation_metadata_json or {}).get("occupation"),
                occupation_key=scenario.occupation_key,
                generation_prompt_version=scenario.generation_prompt_version,
                generation_model=scenario.generation_model,
                is_immutable=scenario.is_immutable,
                validation_status=(scenario.generation_metadata_json or {}).get(
                    "validation_status"
                ),
                usage_count=int(usage_count),
                last_used_at=last_used_at,
            )
        )
    return results


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(
    scenario_id: int,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> ScenarioOut:
    scenario = _get_one_or_404(db, Scenario, scenario_id, "Scenario")
    return ScenarioOut.model_validate(scenario)


@router.put("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(
    scenario_id: int,
    payload: ScenarioUpdate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> ScenarioOut:
    scenario = _get_one_or_404(db, Scenario, scenario_id, "Scenario")
    if scenario.is_immutable:
        changed_fields = set(payload.model_dump(exclude_unset=True))
        if changed_fields - {"status"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Generated scenarios are immutable; only status can be changed.",
            )
    _update_model(scenario, payload)
    if scenario.is_default:
        db.query(Scenario).filter(Scenario.id != scenario.id).update({"is_default": False})
    db.commit()
    db.refresh(scenario)
    return ScenarioOut.model_validate(scenario)


@router.post("/scenarios/{scenario_id}/regenerate", response_model=ScenarioOut)
def regenerate_generated_scenario(
    scenario_id: int,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> ScenarioOut:
    scenario = _get_one_or_404(db, Scenario, scenario_id, "Scenario")
    if scenario.source_type != "ai_base" or not scenario.occupation_category:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only occupation base scenarios can be regenerated.",
        )
    occupation = (
        (scenario.generation_metadata_json or {}).get("occupation")
        or (scenario.occupation_key or "").partition(":")[2]
        or "当前身份"
    )
    agent = ScenarioDesignAgent()
    design_prompt = db.execute(
        select(PromptTemplate)
        .where(
            PromptTemplate.agent_name == "scenario_design",
            PromptTemplate.status == "active",
        )
        .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
    ).scalars().first()
    draft = agent.generate_base(
        scenario.occupation_category,
        occupation,
        design_prompt.content if design_prompt else None,
    )
    if not draft.success or draft.scenario is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Scenario regeneration failed during draft generation.",
        )
    review_prompt = db.execute(
        select(PromptTemplate)
        .where(
            PromptTemplate.agent_name == "scenario_review",
            PromptTemplate.status == "active",
        )
        .order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc())
    ).scalars().first()
    reviewed = agent.review_base(
        scenario.occupation_category,
        occupation,
        draft.scenario,
        review_prompt.content if review_prompt else None,
    )
    if not reviewed.success or reviewed.scenario is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Scenario regeneration failed during review.",
        )
    replacement = ScenarioMaterializationService(db).materialize(
        reviewed.scenario,
        scenario_code=None,
        source_type="ai_base",
        occupation_category=scenario.occupation_category,
        occupation_key=scenario.occupation_key,
        model_name=reviewed.model_name,
        base_scenario_id=None,
    )
    replacement.generation_metadata_json = {
        **(replacement.generation_metadata_json or {}),
        "occupation": occupation,
    }
    scenario.status = "disabled"
    db.commit()
    db.refresh(replacement)
    return ScenarioOut.model_validate(replacement)


@router.get("/scenarios/{scenario_id}/stages", response_model=list[ScenarioStageOut])
def list_stages(
    scenario_id: int,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> list[ScenarioStageOut]:
    _get_one_or_404(db, Scenario, scenario_id, "Scenario")
    stages = db.execute(
        select(ScenarioStage)
        .where(ScenarioStage.scenario_id == scenario_id)
        .order_by(ScenarioStage.stage_order)
    ).scalars().all()
    return [_stage_out(db, stage) for stage in stages]


@router.put("/stages/{stage_id}", response_model=ScenarioStageOut)
def update_stage(
    stage_id: int,
    payload: ScenarioStageUpdate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> ScenarioStageOut:
    stage = _assert_stage_scenario_mutable(db, stage_id)
    _update_model(stage, payload)
    db.commit()
    db.refresh(stage)
    return _stage_out(db, stage)


@router.put("/stages/{stage_id}/dimensions", response_model=list[DimensionBindingOut])
def replace_stage_dimensions(
    stage_id: int,
    payload: list[DimensionBinding],
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> list[DimensionBindingOut]:
    _assert_stage_scenario_mutable(db, stage_id)
    db.query(ScenarioStageDimension).filter(ScenarioStageDimension.stage_id == stage_id).delete()
    for item in payload:
        _get_one_or_404(db, RubricDimension, item.dimension_id, "Rubric dimension")
        db.add(
            ScenarioStageDimension(
                stage_id=stage_id,
                dimension_id=item.dimension_id,
                observe_role=item.observe_role,
                weight=Decimal(str(item.weight)) if item.weight is not None else None,
            )
        )
    db.commit()
    return _dimension_bindings(db, stage_id)


@router.get("/stages/{stage_id}/dynamic-infos", response_model=list[StageDynamicInfoOut])
def list_dynamic_infos(
    stage_id: int,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> list[StageDynamicInfoOut]:
    _get_one_or_404(db, ScenarioStage, stage_id, "Scenario stage")
    items = db.execute(
        select(StageDynamicInfo)
        .where(StageDynamicInfo.stage_id == stage_id)
        .order_by(StageDynamicInfo.priority, StageDynamicInfo.id)
    ).scalars().all()
    return [StageDynamicInfoOut.model_validate(item) for item in items]


@router.post("/stages/{stage_id}/dynamic-infos", response_model=StageDynamicInfoOut)
def create_dynamic_info(
    stage_id: int,
    payload: StageDynamicInfoCreate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> StageDynamicInfoOut:
    _assert_stage_scenario_mutable(db, stage_id)
    item = StageDynamicInfo(stage_id=stage_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return StageDynamicInfoOut.model_validate(item)


@router.put("/dynamic-infos/{dynamic_info_id}", response_model=StageDynamicInfoOut)
def update_dynamic_info(
    dynamic_info_id: int,
    payload: StageDynamicInfoUpdate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> StageDynamicInfoOut:
    item = _get_one_or_404(db, StageDynamicInfo, dynamic_info_id, "Dynamic info")
    _assert_stage_scenario_mutable(db, item.stage_id)
    _update_model(item, payload)
    db.commit()
    db.refresh(item)
    return StageDynamicInfoOut.model_validate(item)


@router.get("/stages/{stage_id}/intervention-rules", response_model=list[StageInterventionRuleOut])
def list_intervention_rules(
    stage_id: int,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> list[StageInterventionRuleOut]:
    _get_one_or_404(db, ScenarioStage, stage_id, "Scenario stage")
    items = db.execute(
        select(StageInterventionRule)
        .where(StageInterventionRule.stage_id == stage_id)
        .order_by(StageInterventionRule.priority, StageInterventionRule.id)
    ).scalars().all()
    return [StageInterventionRuleOut.model_validate(item) for item in items]


@router.post("/stages/{stage_id}/intervention-rules", response_model=StageInterventionRuleOut)
def create_intervention_rule(
    stage_id: int,
    payload: StageInterventionRuleCreate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> StageInterventionRuleOut:
    _assert_stage_scenario_mutable(db, stage_id)
    item = StageInterventionRule(stage_id=stage_id, **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return StageInterventionRuleOut.model_validate(item)


@router.put("/intervention-rules/{rule_id}", response_model=StageInterventionRuleOut)
def update_intervention_rule(
    rule_id: int,
    payload: StageInterventionRuleUpdate,
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> StageInterventionRuleOut:
    item = _get_one_or_404(db, StageInterventionRule, rule_id, "Intervention rule")
    _assert_stage_scenario_mutable(db, item.stage_id)
    _update_model(item, payload)
    db.commit()
    db.refresh(item)
    return StageInterventionRuleOut.model_validate(item)


@router.get("/seeds/export", response_class=PlainTextResponse)
def export_seed_yaml(
    _admin: AdminDep,
    db: Annotated[Session, Depends(get_db)],
) -> PlainTextResponse:
    dimensions = list_rubric_dimensions(_admin, db)
    scenarios = list_scenarios(_admin, db)
    exported: dict[str, Any] = {
        "rubric_dimensions": [item.model_dump(mode="json") for item in dimensions],
        "scenarios": [],
    }

    for scenario_item in scenarios:
        scenario = get_scenario(scenario_item.id, _admin, db)
        stages = list_stages(scenario_item.id, _admin, db)
        exported["scenarios"].append(
            {
                **scenario.model_dump(mode="json"),
                "stages": [stage.model_dump(mode="json") for stage in stages],
            }
        )

    content = yaml.safe_dump(exported, allow_unicode=True, sort_keys=False)
    return PlainTextResponse(content, media_type="application/x-yaml; charset=utf-8")
