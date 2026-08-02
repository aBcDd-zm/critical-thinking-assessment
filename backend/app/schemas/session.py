from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


OccupationCategory = Literal[
    "学生",
    "教育培训",
    "医疗健康",
    "互联网/信息技术",
    "工程/制造/建筑",
    "商业/金融/管理",
    "政府/公共服务",
    "科研/法律/专业服务",
    "文化/传媒/创意",
    "零售/餐饮/生活服务",
    "自由职业/个体经营",
    "待业/退休/其他",
]
CONSENT_VERSION = "critical_thinking_assessment_consent_v1"


class CreateSessionRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)
    occupation_category: OccupationCategory
    occupation: str = Field(min_length=2, max_length=64)
    info_collect_method: str = "ai_dialogue"
    assessment_mode: str = "mock"
    consent_accepted: Literal[True]
    consent_version: Literal["critical_thinking_assessment_consent_v1"]


class StageSummary(BaseModel):
    stage_code: str
    title: str
    stage_order: int
    context: str
    main_question: str
    max_followups: int


class ScenarioSummary(BaseModel):
    scenario_code: str
    title: str
    background: str
    estimated_minutes: int
    version: str
    source_type: str = "seeded"


class OnboardingState(BaseModel):
    question_count: int = 0
    max_questions: int = 3
    completed: bool = False


class ScenarioPreparationState(BaseModel):
    status: str = "queued"
    cache_hit: bool = False
    fallback_used: bool = False
    message: str | None = None


class DialogueTurnItem(BaseModel):
    turn_index: int
    speaker: str
    content: str
    content_type: str
    created_at: datetime
    analysis: dict[str, Any] | None = None


class StageProgressItem(BaseModel):
    stage_code: str
    title: str
    stage_order: int
    status: str
    max_followups: int
    used_followups: int
    used_clarifications: int
    can_skip: bool
    skipped: bool
    released_dynamic_info_count: int
    estimated_minutes: int
    evidence_coverage: dict[str, str] = Field(default_factory=dict)
    missing_evidence: list[str] = Field(default_factory=list)
    waiting_for_stage_choice: bool = False


class SessionProgress(BaseModel):
    total_stages: int
    current_stage_order: int | None
    estimated_minutes: int
    elapsed_seconds: int | None
    stages: list[StageProgressItem] = Field(default_factory=list)


class InterviewProgress(BaseModel):
    formal_answer_count: int = Field(ge=0, le=12)
    target_min_answers: int = 9
    target_max_answers: int = 12
    percent: int = Field(ge=0, le=100)
    estimated_remaining_minutes: int = Field(ge=0)
    elapsed_seconds: int = Field(default=0, ge=0)


class SessionResponse(BaseModel):
    session_uuid: str
    status: str
    flow_version: Literal[
        "legacy_v2", "progressive_v3", "progressive_v3_2", "progressive_v3_3"
    ] = "legacy_v2"
    interviewer_style_version: str = "baseline_v1"
    participant_nickname: str
    scenario: ScenarioSummary
    current_stage: StageSummary | None
    turns: list[DialogueTurnItem] = Field(default_factory=list)
    progress: SessionProgress | None = None
    interview_progress: InterviewProgress | None = None
    language_mode: str = "standard"
    phase: Literal[
        "onboarding", "scenario_preparing", "opening_pending", "assessment", "completed"
    ] = (
        "assessment"
    )
    onboarding: OnboardingState | None = None
    scenario_preparation: ScenarioPreparationState | None = None


class ProfileTurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class PreparationResponse(BaseModel):
    session_uuid: str
    phase: Literal[
        "onboarding", "scenario_preparing", "opening_pending", "assessment", "completed"
    ]
    onboarding: OnboardingState
    scenario_preparation: ScenarioPreparationState
    assessment_ready: bool


class SubmitTurnRequest(BaseModel):
    content: str = Field(min_length=1)
    content_type: str = "scenario_answer"
    client_turn_id: UUID = Field(default_factory=uuid4)
    answer_duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)


class SubmitTurnResponse(BaseModel):
    session_uuid: str
    saved_turn_index: int
    next_action: str
    message: str
    replayed: bool = False


class FinishSessionResponse(BaseModel):
    session_uuid: str
    status: str
    completed_at: datetime


class SkipStageResponse(BaseModel):
    session_uuid: str
    next_action: str
    message: str


class ContinueStageResponse(BaseModel):
    session_uuid: str
    next_action: str
    message: str


class UpdateLanguageModeRequest(BaseModel):
    language_mode: str = Field(pattern="^(standard|plain)$")


class LanguageModeResponse(BaseModel):
    session_uuid: str
    language_mode: str


class SubmitFeedbackRequest(BaseModel):
    realism_score: int = Field(ge=1, le=5)
    difficulty_score: int = Field(ge=1, le=5)
    naturalness_score: int = Field(ge=1, le=5)
    fatigue_score: int = Field(ge=1, le=5)
    report_trust_score: int = Field(ge=1, le=5)
    overall_satisfaction_score: int = Field(ge=1, le=5)
    open_feedback: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    session_uuid: str
    realism_score: int
    difficulty_score: int
    naturalness_score: int
    fatigue_score: int
    report_trust_score: int
    overall_satisfaction_score: int
    open_feedback: str | None = None
    submitted_at: datetime


class FeedbackStateResponse(BaseModel):
    session_uuid: str
    submitted: bool
    feedback: FeedbackResponse | None = None


class ReportResponse(BaseModel):
    session_uuid: str
    status: str
    report: dict[str, Any]


class ReportGenerationResponse(BaseModel):
    session_uuid: str
    status: Literal["ready", "scheduled", "running", "failed"]
