from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AgentName = Literal[
    "host", "followup", "planner", "interviewer", "scoring", "report"
]
AgentStatus = Literal["ok", "failed"]
ResponseCategory = Literal[
    "assess_answer",
    "clarify_question",
    "explain_term",
    "encourage_answer",
    "redirect",
]
NextAction = Literal[
    "wait_user_answer",
    "ask_followup",
    "advance_stage",
    "finish_ready",
    "generate_report",
    "stop",
]
StageTransitionReason = Literal[
    "evidence_complete",
    "followup_limit_reached",
    "user_navigation",
]


class AgentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionContext(AgentSchema):
    session_id: int | None = None
    session_uuid: str
    assessment_mode: str = "mock"
    status: str = "in_progress"
    language_mode: Literal["standard", "plain"] = "standard"


class ParticipantContext(AgentSchema):
    participant_id: int | None = None
    nickname: str | None = None
    profile_summary: str | None = None


class ScenarioContext(AgentSchema):
    scenario_id: int | None = None
    scenario_code: str
    title: str
    background: str


class StageContext(AgentSchema):
    stage_id: int | None = None
    stage_code: str
    stage_order: int
    title: str
    stage_goal: str
    context: str
    main_question: str
    context_generation_mode: str = "config_guided"
    context_ai_weight: int = Field(default=30, ge=0, le=100)
    max_followups: int = Field(default=2, ge=0)
    estimated_minutes: int | None = Field(default=None, ge=1)
    exit_criteria: dict[str, Any] = Field(default_factory=dict)


class DialogueTurnContext(AgentSchema):
    turn_id: int | None = None
    turn_index: int | None = None
    stage_id: int | None = None
    stage_code: str | None = None
    speaker: Literal["ai", "user", "system"]
    content: str
    content_type: str
    dynamic_info_id: int | None = None
    selected_dynamic_info_code: str | None = None
    analysis_json: dict[str, Any] | None = None


class RubricDimensionContext(AgentSchema):
    dimension_key: str
    name: str
    definition: str
    observable_behaviors: list[str] = Field(default_factory=list)
    invalid_evidence_desc: str | None = None


class RubricAnchorContext(AgentSchema):
    dimension_key: str
    score_level: int = Field(ge=1, le=5)
    level_name: str
    behavior_desc: str
    evidence_examples: list[str] | None = None
    counter_examples: list[str] | None = None


class StageDimensionBindingContext(AgentSchema):
    stage_code: str
    dimension_key: str
    observe_role: Literal["primary", "secondary"]
    weight: float | None = None


class DynamicInfoContext(AgentSchema):
    dynamic_info_id: int | None = None
    info_code: str
    title: str
    content: str
    info_type: str
    trigger_condition: str | None = None
    priority: int = 100
    target_dimensions: list[str] = Field(default_factory=list)


class InterventionRuleContext(AgentSchema):
    rule_id: int | None = None
    rule_code: str
    rule_type: str
    trigger_condition: str | None = None
    strategy_direction: str
    sample_question: str | None = None
    question_generation_mode: str = "strategy_guided"
    question_ai_weight: int = Field(default=40, ge=0, le=100)
    question_generation_constraints_json: dict[str, Any] | None = None
    fallback_question: str | None = None
    exit_prompt: str | None = None
    priority: int = 100
    max_use_count: int | None = Field(default=None, ge=1)
    target_dimensions: list[str] = Field(default_factory=list)


class ScoreBrief(AgentSchema):
    dimension_key: str
    score: int = Field(ge=1, le=5)
    reason: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class ScoreGapSummary(AgentSchema):
    missing_dimensions: list[str] = Field(default_factory=list)
    argument_issues: list[str] = Field(default_factory=list)
    low_score_dimensions: list[str] = Field(default_factory=list)


class LatestScoreSnapshotContext(AgentSchema):
    snapshot_id: int | None = None
    snapshot_type: str
    summary: str | None = None
    scores: list[ScoreBrief] = Field(default_factory=list)


class AgentRuntimeContext(AgentSchema):
    session: SessionContext
    participant: ParticipantContext
    scenario: ScenarioContext
    stage: StageContext
    dialogue_history: list[DialogueTurnContext] = Field(default_factory=list)
    rubric_dimensions: list[RubricDimensionContext] = Field(default_factory=list)
    rubric_anchors: list[RubricAnchorContext] = Field(default_factory=list)
    stage_dimension_bindings: list[StageDimensionBindingContext] = Field(
        default_factory=list
    )
    candidate_dynamic_infos: list[DynamicInfoContext] = Field(default_factory=list)
    candidate_intervention_rules: list[InterventionRuleContext] = Field(default_factory=list)
    latest_user_turn: DialogueTurnContext | None = None
    score_gap_summary: ScoreGapSummary | None = None
    latest_score_snapshot: LatestScoreSnapshotContext | None = None
    professional_context: list[str] = Field(default_factory=list)


class AgentFailureOutput(AgentSchema):
    status: Literal["failed"] = "failed"
    agent_name: AgentName
    error_code: str
    reason: str
    fallback_used: bool = True
    fallback_message: str | None = None
    warnings: list[str] = Field(default_factory=list)


class HostOutput(AgentSchema):
    status: Literal["ok"] = "ok"
    agent_name: Literal["host"] = "host"
    stage_code: str
    message: str
    content_type: Literal["stage_question", "advance_prompt", "system_message"] = "stage_question"
    generation_mode: str = "config_guided"
    ai_generation_weight: int = Field(default=30, ge=0, le=100)
    reason: str | None = None
    next_action: NextAction = "wait_user_answer"
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class HumanisticFollowupSteps(AgentSchema):
    listening_acknowledgement: str | None = None
    reflective_clarification: str | None = None
    safety_prompt: str | None = None
    evidence_probe: str | None = None


class ResolvedEvidenceItem(AgentSchema):
    evidence_key: str
    coverage: Literal["covered", "partial", "missing"]
    supporting_turn_indexes: list[int] = Field(default_factory=list)
    reason: str
    confidence: float | None = Field(default=None, ge=0, le=1)


class FollowupOutput(AgentSchema):
    status: Literal["ok"] = "ok"
    agent_name: Literal["followup"] = "followup"
    question: str
    content_type: Literal[
        "followup_question",
        "dynamic_info_question",
        "clarification_response",
        "guidance_response",
        "term_explanation",
        "redirect_response",
        "stage_incomplete_prompt",
        "supplement_question",
        "advance_prompt",
        "system_message",
    ] = "followup_question"
    question_type: str
    resolved_response_category: ResponseCategory | None = None
    category_correction_reason: str | None = None
    resolved_evidence: list[ResolvedEvidenceItem] = Field(default_factory=list)
    selected_rule_code: str | None = None
    selected_dynamic_info_code: str | None = None
    released_dynamic_info_text: str | None = None
    target_dimensions: list[str] = Field(default_factory=list)
    trigger_reason: str | None = None
    reflection_summary: str | None = None
    evidence_gap: str | None = None
    humanistic_steps: HumanisticFollowupSteps | None = None
    generation_mode: str | None = None
    ai_generation_weight: int | None = Field(default=None, ge=0, le=100)
    reason: str
    next_action: NextAction = "ask_followup"
    transition_reason: StageTransitionReason | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class EvidenceItem(AgentSchema):
    text: str
    evidence_type: str
    explanation: str | None = None
    dialogue_turn_id: int | None = None


class DimensionScore(AgentSchema):
    dimension_key: str
    score: int | None = Field(default=None, ge=1, le=5)
    assessment_status: Literal["scored", "insufficient_evidence"] = "scored"
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    scoring_source: Literal["agent", "mock", "manual"] = "agent"
    evidence_sufficiency_index: int | None = Field(default=None, ge=0, le=100)
    evidence_sufficiency_level: Literal["low", "medium", "high"] | None = None
    score_kind: Literal["supported", "provisional", "unobserved"] = "unobserved"
    evidence_sufficiency_note: str = ""


class ScoringOutput(AgentSchema):
    status: Literal["ok"] = "ok"
    agent_name: Literal["scoring"] = "scoring"
    snapshot_type: Literal["turn", "stage", "final"] = "stage"
    summary: str
    trend_analysis: str | None = None
    scores: list[DimensionScore] = Field(min_length=1)
    detected_score_gaps: list[str] = Field(default_factory=list)
    detected_argument_issues: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class DimensionReport(AgentSchema):
    dimension_key: str
    dimension_name: str
    score: int | None = Field(default=None, ge=1, le=5)
    assessment_status: Literal["scored", "insufficient_evidence"] = "scored"
    level_label: str
    strength: str
    weakness: str | None = None
    evidence_quotes: list[str] = Field(default_factory=list)
    suggestion: str
    evidence_sufficiency_index: int | None = Field(default=None, ge=0, le=100)
    evidence_sufficiency_level: Literal["low", "medium", "high"] | None = None
    score_kind: Literal["supported", "provisional", "unobserved"] = "unobserved"
    evidence_sufficiency_note: str = ""


class MeasurementQuality(AgentSchema):
    status: Literal["valid", "caution", "invalid"] = "valid"
    technical_failure_rate: float = Field(default=0, ge=0, le=1)
    total_fallback_rate: float = Field(default=0, ge=0, le=1)
    missing_events: list[str] = Field(default_factory=list)
    unobserved_dimensions: list[str] = Field(default_factory=list)
    provisional_dimensions: list[str] = Field(default_factory=list)
    scoring_contamination_turn_ids: list[int] = Field(default_factory=list)
    retest_recommended: bool = False
    reasons: list[str] = Field(default_factory=list)
    overall_evidence_sufficiency_index: float | None = Field(
        default=None, ge=0, le=100
    )


class ReportOutput(AgentSchema):
    status: Literal["ok"] = "ok"
    agent_name: Literal["report"] = "report"
    summary: str
    overall_level: str
    dimension_reports: list[DimensionReport] = Field(min_length=1)
    advantages: list[str] = Field(default_factory=list)
    improvement_suggestions: list[str] = Field(default_factory=list)
    development_plan: list[str] = Field(default_factory=list)
    disclaimer: str
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    measurement_quality: MeasurementQuality = Field(default_factory=MeasurementQuality)
