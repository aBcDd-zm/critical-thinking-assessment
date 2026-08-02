from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AdminSessionListItem(BaseModel):
    session_uuid: str
    nickname: str
    scenario_code: str
    scenario_title: str
    status: str
    assessment_mode: str
    turn_count: int
    agent_trace_count: int
    report_status: str | None = None
    review_status: str
    review_decision: str | None = None
    min_ai_confidence: float | None = None
    expert_score_count: int
    expert_score_target_count: int
    expert_score_completion_rate: float
    duration_minutes: float | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class AdminSessionListResponse(BaseModel):
    items: list[AdminSessionListItem]
    total: int
    page: int
    page_size: int


class AdminReviewSession(BaseModel):
    session_uuid: str
    nickname: str
    scenario_code: str
    scenario_title: str
    scenario_version: str
    scenario_source_type: str = "seeded"
    base_scenario_id: int | None = None
    occupation_category: str | None = None
    occupation: str | None = None
    scenario_generation_status: str | None = None
    scenario_cache_hit: bool = False
    scenario_fallback_used: bool = False
    status: str
    assessment_mode: str
    flow_version: str = "legacy_v2"
    interviewer_style_version: str = "baseline_v1"
    state_version: int = 0
    current_stage_code: str | None = None
    current_stage_title: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_minutes: float | None = None
    created_at: datetime
    updated_at: datetime


class AdminReviewTurn(BaseModel):
    turn_id: int
    turn_index: int
    stage_code: str | None = None
    stage_title: str | None = None
    speaker: str
    content: str
    content_type: str
    source_agent_trace_id: int | None = None
    intervention_rule_code: str | None = None
    dynamic_info_code: str | None = None
    client_turn_id: str | None = None
    answer_duration_ms: int | None = None
    created_at: datetime


class AdminReviewTrace(BaseModel):
    trace_id: int
    stage_code: str | None = None
    stage_title: str | None = None
    trigger_turn_id: int | None = None
    agent_name: str
    generation_mode: str | None = None
    ai_generation_weight: int | None = None
    config_snapshot_json: dict[str, Any] | None = None
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    raw_output: str | None = None
    status: str
    error_code: str | None = None
    fallback_type: str | None = None
    fallback_reason: str | None = None
    prompt_template_id: int | None = None
    parent_trace_id: int | None = None
    interviewer_style_version: str | None = None
    validation_codes: list[str] = Field(default_factory=list)
    model_name: str | None = None
    duration_ms: int | None = None
    selected_rule_code: str | None = None
    selected_dynamic_info_code: str | None = None
    created_at: datetime


class AdminReviewEvidence(BaseModel):
    evidence_id: int
    dialogue_turn_id: int | None = None
    evidence_text: str
    evidence_type: str
    explanation: str | None = None
    created_at: datetime


class AdminReviewScoreResult(BaseModel):
    score_result_id: int
    dimension_key: str
    dimension_name: str
    score: int | None = None
    assessment_status: str
    reason: str
    confidence: float | None = None
    evidence_sufficiency_index: int | None = Field(default=None, ge=0, le=100)
    score_kind: str | None = None
    scoring_source: str
    evidence: list[AdminReviewEvidence] = Field(default_factory=list)
    created_at: datetime


class AdminReviewScoreSnapshot(BaseModel):
    snapshot_id: int
    stage_code: str | None = None
    stage_title: str | None = None
    dialogue_turn_id: int | None = None
    snapshot_type: str
    summary: str | None = None
    trend_analysis: str | None = None
    agent_trace_id: int | None = None
    results: list[AdminReviewScoreResult] = Field(default_factory=list)
    created_at: datetime


class AdminReviewReport(BaseModel):
    status: str
    summary: str | None = None
    report_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AdminReviewFeedback(BaseModel):
    realism_score: int
    difficulty_score: int
    naturalness_score: int
    fatigue_score: int
    report_trust_score: int
    overall_satisfaction_score: int
    open_feedback: str | None = None
    submitted_at: datetime


class HumanReviewUpdate(BaseModel):
    status: Literal["pending", "in_review", "completed", "needs_adjudication"]
    decision: Literal["valid", "needs_adjudication", "exclude"] | None = None
    notes: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_decision(self) -> "HumanReviewUpdate":
        if self.status == "completed" and self.decision not in {"valid", "exclude"}:
            raise ValueError("Completed reviews require a valid or exclude decision")
        if (
            self.status == "needs_adjudication"
            and self.decision != "needs_adjudication"
        ):
            raise ValueError(
                "Adjudication reviews require a needs_adjudication decision"
            )
        return self


class HumanReviewOut(BaseModel):
    status: str
    decision: str | None = None
    notes: str | None = None
    reviewer_id: int | None = None
    reviewer_name: str | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExpertScoreWrite(BaseModel):
    stage_code: str = Field(min_length=1, max_length=64)
    dimension_key: str = Field(min_length=1, max_length=64)
    assessment_status: Literal["scored", "insufficient_evidence"]
    score: int | None = Field(default=None, ge=1, le=5)
    evidence_ids: list[int] = Field(default_factory=list)
    bars_reason: str = Field(min_length=1, max_length=5000)
    next_level_gap: str | None = Field(default=None, max_length=5000)
    annotator_confidence: Literal["high", "medium", "low"]
    review_flag: bool = False
    review_reason: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def validate_score(self) -> "ExpertScoreWrite":
        if self.assessment_status == "scored" and self.score is None:
            raise ValueError("Scored annotations require a score")
        if self.assessment_status == "insufficient_evidence" and self.score is not None:
            raise ValueError("Insufficient-evidence annotations cannot include a score")
        if self.review_flag and not (self.review_reason or "").strip():
            raise ValueError("Flagged annotations require a review reason")
        return self


class ExpertScoreBatchRequest(BaseModel):
    items: list[ExpertScoreWrite] = Field(min_length=1, max_length=200)


class ExpertScoreOut(BaseModel):
    annotation_id: int
    stage_code: str
    stage_title: str
    dimension_key: str
    dimension_name: str
    annotator_id: int
    annotator_name: str
    is_current_annotator: bool
    assessment_status: str
    score: int | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    bars_reason: str
    next_level_gap: str | None = None
    annotator_confidence: str
    review_flag: bool
    review_reason: str | None = None
    source: str
    import_batch_id: str | None = None
    ai_score: int | None = None
    ai_confidence: float | None = None
    score_difference: int | None = None
    created_at: datetime
    updated_at: datetime


class ExpertScoreTarget(BaseModel):
    stage_code: str
    stage_title: str
    dimension_key: str
    dimension_name: str
    ai_score: int | None = None
    ai_confidence: float | None = None


class ExpertScoreBatchResponse(BaseModel):
    saved_count: int
    imported_count: int = 0
    import_batch_id: str | None = None
    items: list[ExpertScoreOut]


class AdminSessionReviewResponse(BaseModel):
    session: AdminReviewSession
    turns: list[AdminReviewTurn]
    traces: list[AdminReviewTrace]
    score_snapshots: list[AdminReviewScoreSnapshot]
    report: AdminReviewReport | None = None
    feedback: AdminReviewFeedback | None = None
    human_review: HumanReviewOut
    expert_score_targets: list[ExpertScoreTarget]
    expert_scores: list[ExpertScoreOut]
    progressive_audit: dict[str, Any] | None = None
