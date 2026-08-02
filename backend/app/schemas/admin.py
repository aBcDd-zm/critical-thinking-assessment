from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str
    role: str
    status: str
    last_login_at: datetime | None = None


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AdminUserOut


class DashboardSummary(BaseModel):
    scenario_count: int
    active_scenario_count: int
    stage_count: int
    dynamic_info_count: int
    intervention_rule_count: int
    rubric_dimension_count: int
    rubric_anchor_count: int
    prompt_template_count: int
    report_template_count: int


class DashboardSessionStatusItem(BaseModel):
    status: str
    count: int


class DashboardAgentStatusItem(BaseModel):
    status: str
    count: int


class DashboardRecentSessionItem(BaseModel):
    session_uuid: str
    nickname: str
    scenario_title: str
    status: str
    assessment_mode: str
    turn_count: int
    agent_trace_count: int
    report_status: str | None = None
    duration_minutes: float | None = None
    started_at: datetime | None = None
    updated_at: datetime


class DashboardStageProgressItem(BaseModel):
    stage_title: str
    ai_turn_count: int
    user_turn_count: int
    trace_count: int


class DashboardFeedbackAverages(BaseModel):
    realism_score: float | None = None
    difficulty_score: float | None = None
    naturalness_score: float | None = None
    fatigue_score: float | None = None
    report_trust_score: float | None = None
    overall_satisfaction_score: float | None = None


class DashboardFeedbackCommentItem(BaseModel):
    nickname: str
    overall_satisfaction_score: int
    naturalness_score: int
    report_trust_score: int
    open_feedback: str
    submitted_at: datetime


class DashboardAnalytics(BaseModel):
    session_count: int
    completed_session_count: int
    in_progress_session_count: int
    completion_rate: float
    average_duration_minutes: float | None = None
    average_turn_count: float | None = None
    dialogue_turn_count: int
    agent_trace_count: int
    agent_success_rate: float | None = None
    report_count: int
    score_snapshot_count: int
    score_result_count: int
    score_evidence_count: int
    status_distribution: list[DashboardSessionStatusItem]
    agent_status_distribution: list[DashboardAgentStatusItem]
    recent_sessions: list[DashboardRecentSessionItem]
    stage_progress: list[DashboardStageProgressItem]
    feedback_count: int
    feedback_coverage_rate: float
    feedback_averages: DashboardFeedbackAverages
    low_satisfaction_count: int
    recent_feedback_comments: list[DashboardFeedbackCommentItem]


class RubricAnchorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dimension_id: int
    score_level: int
    level_name: str
    behavior_desc: str
    evidence_examples: list[str] | None = None
    counter_examples: list[str] | None = None
    status: str
    updated_at: datetime


class RubricAnchorUpdate(BaseModel):
    level_name: str | None = Field(default=None, max_length=64)
    behavior_desc: str | None = None
    evidence_examples: list[str] | None = None
    counter_examples: list[str] | None = None
    status: str | None = Field(default=None, max_length=32)


class RubricDimensionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dimension_key: str
    name: str
    definition: str
    observable_behaviors: list[str] | dict[str, Any]
    invalid_evidence_desc: str | None = None
    version: str
    status: str
    updated_at: datetime
    anchors: list[RubricAnchorOut] = Field(default_factory=list)


class RubricDimensionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    definition: str | None = None
    observable_behaviors: list[str] | dict[str, Any] | None = None
    invalid_evidence_desc: str | None = None
    version: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=32)


class ScenarioListItem(BaseModel):
    id: int
    scenario_code: str
    title: str
    target_audience: str
    scenario_type: str
    difficulty_level: str
    estimated_minutes: int
    rotation_weight: int
    is_default: bool
    version: str
    status: str
    stage_count: int
    updated_at: datetime
    source_type: str = "seeded"
    occupation_category: str | None = None
    occupation: str | None = None
    occupation_key: str | None = None
    generation_prompt_version: str | None = None
    generation_model: str | None = None
    is_immutable: bool = False
    validation_status: str | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_code: str
    title: str
    background: str
    target_audience: str
    scenario_type: str
    difficulty_level: str
    estimated_minutes: int
    rotation_weight: int
    is_default: bool
    version: str
    status: str
    updated_at: datetime
    source_type: str = "seeded"
    base_scenario_id: int | None = None
    occupation_category: str | None = None
    occupation_key: str | None = None
    generation_prompt_version: str | None = None
    generation_model: str | None = None
    generation_metadata_json: dict[str, Any] | None = None
    is_immutable: bool = False


class ScenarioUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    background: str | None = None
    target_audience: str | None = Field(default=None, max_length=64)
    scenario_type: str | None = Field(default=None, max_length=64)
    difficulty_level: str | None = Field(default=None, max_length=32)
    estimated_minutes: int | None = Field(default=None, ge=1)
    rotation_weight: int | None = Field(default=None, ge=0)
    is_default: bool | None = None
    version: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=32)


class DimensionBinding(BaseModel):
    dimension_id: int
    observe_role: str = Field(default="primary", max_length=32)
    weight: float | None = None


class DimensionBindingOut(DimensionBinding):
    dimension_key: str
    dimension_name: str


class ScenarioStageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_id: int
    stage_code: str
    stage_order: int
    title: str
    stage_goal: str
    context: str
    main_question: str
    context_generation_mode: str
    context_ai_weight: int
    context_generation_constraints_json: dict[str, Any] | None = None
    max_followups: int
    estimated_minutes: int
    exit_criteria_json: dict[str, Any] | None = None
    status: str
    updated_at: datetime
    dimensions: list[DimensionBindingOut] = Field(default_factory=list)


class ScenarioStageUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    stage_goal: str | None = None
    context: str | None = None
    main_question: str | None = None
    context_generation_mode: str | None = Field(default=None, max_length=32)
    context_ai_weight: int | None = Field(default=None, ge=0, le=100)
    context_generation_constraints_json: dict[str, Any] | None = None
    max_followups: int | None = Field(default=None, ge=0)
    estimated_minutes: int | None = Field(default=None, ge=1)
    exit_criteria_json: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=32)


class StageDynamicInfoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_id: int
    info_code: str
    title: str
    content: str
    info_type: str
    trigger_condition: str | None = None
    priority: int
    status: str
    updated_at: datetime


class StageDynamicInfoCreate(BaseModel):
    info_code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    info_type: str = Field(min_length=1, max_length=32)
    trigger_condition: str | None = None
    priority: int = Field(default=100, ge=0)
    status: str = Field(default="active", max_length=32)


class StageDynamicInfoUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=128)
    content: str | None = None
    info_type: str | None = Field(default=None, max_length=32)
    trigger_condition: str | None = None
    priority: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=32)


class StageInterventionRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_id: int
    rule_code: str
    rule_type: str
    trigger_condition: str | None = None
    strategy_direction: str
    sample_question: str | None = None
    question_generation_mode: str
    question_ai_weight: int
    question_generation_constraints_json: dict[str, Any] | None = None
    fallback_question: str | None = None
    exit_prompt: str | None = None
    priority: int
    max_use_count: int | None = None
    status: str
    updated_at: datetime


class StageInterventionRuleCreate(BaseModel):
    rule_code: str = Field(min_length=1, max_length=64)
    rule_type: str = Field(min_length=1, max_length=32)
    trigger_condition: str | None = None
    strategy_direction: str = Field(min_length=1)
    sample_question: str | None = None
    question_generation_mode: str = Field(default="strategy_guided", max_length=32)
    question_ai_weight: int = Field(default=40, ge=0, le=100)
    question_generation_constraints_json: dict[str, Any] | None = None
    fallback_question: str | None = None
    exit_prompt: str | None = None
    priority: int = Field(default=100, ge=0)
    max_use_count: int | None = Field(default=None, ge=0)
    status: str = Field(default="active", max_length=32)


class StageInterventionRuleUpdate(BaseModel):
    rule_type: str | None = Field(default=None, max_length=32)
    trigger_condition: str | None = None
    strategy_direction: str | None = None
    sample_question: str | None = None
    question_generation_mode: str | None = Field(default=None, max_length=32)
    question_ai_weight: int | None = Field(default=None, ge=0, le=100)
    question_generation_constraints_json: dict[str, Any] | None = None
    fallback_question: str | None = None
    exit_prompt: str | None = None
    priority: int | None = Field(default=None, ge=0)
    max_use_count: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=32)
