from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.measurement_contract import SlotStatus


PlannerAction = Literal[
    "CLARIFY",
    "PROBE",
    "RELEASE_EVENT",
    "CHALLENGE",
    "INTEGRATE",
    "CONCLUDE",
]
DeliveryMode = Literal[
    "reflective_probe",
    "clarification",
    "summary_check",
    "event_link",
    "perspective_shift",
    "integration",
    "closing",
]
ResponseIntent = Literal[
    "assess_answer",
    "clarify_question",
    "explain_term",
    "low_information",
    "redirect",
    "request_context",
    "conversation_repair",
]
EvidenceResponseOrigin = Literal[
    "spontaneous_evidence",
    "elicited_evidence",
    "not_scored",
]
EvidenceDisposition = Literal["accepted", "excluded"]


class ProgressiveModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceObservation(ProgressiveModel):
    dimension_key: str
    behavior_key: str
    quote: str = Field(min_length=1)
    validity: Literal["valid", "weak", "invalid", "unscorable"] = "valid"
    novelty: Literal["new", "elaborated", "repeated", "contradictory"] = "new"
    polarity: Literal["support", "counter"] = "support"
    rationale: str = Field(min_length=1)
    extraction_confidence: float = Field(ge=0, le=1)
    contradiction_with: list[str] = Field(default_factory=list)
    response_origin: EvidenceResponseOrigin | None = None
    source_turn_id: int | None = None
    preceding_ai_turn_id: int | None = None
    introduced_by_ai: bool = False
    disposition: EvidenceDisposition = "accepted"
    exclusion_reason: str | None = None
    evidence_policy_version: str | None = None

    @model_validator(mode="after")
    def validate_contradiction(self) -> "EvidenceObservation":
        if self.novelty == "contradictory" and not self.contradiction_with:
            raise ValueError("contradictory evidence must reference earlier evidence")
        if self.introduced_by_ai and (
            self.validity != "invalid"
            or self.disposition != "excluded"
            or not self.exclusion_reason
        ):
            raise ValueError(
                "AI-introduced evidence must be invalid, excluded, and explain why"
            )
        if self.disposition == "excluded" and not self.exclusion_reason:
            raise ValueError("excluded evidence must include exclusion_reason")
        return self


class DimensionSlotState(ProgressiveModel):
    dimension_key: str
    status: SlotStatus
    evidence_turn_ids: list[int] = Field(default_factory=list)
    diagnostic_low_evidence_turn_ids: list[int] = Field(default_factory=list)
    observed_behavior_keys: list[str] = Field(default_factory=list)
    missing_behavior_keys: list[str] = Field(default_factory=list)
    conflicting_evidence_turn_ids: list[int] = Field(default_factory=list)
    confidence: None = None
    insufficient_reason: str | None = None


class EvidenceDeltaAudit(ProgressiveModel):
    dimension_key: str
    status_before: SlotStatus
    status_after: SlotStatus
    confidence_before: None = None
    confidence_after: None = None
    confidence_delta: None = None
    delta_type: Literal[
        "none",
        "new_partial",
        "partial_to_sufficient",
        "corroborating",
        "contradictory",
        "invalidated",
    ]
    added_evidence_turn_ids: list[int] = Field(default_factory=list)
    added_behavior_keys: list[str] = Field(default_factory=list)
    repeated_evidence_turn_ids: list[int] = Field(default_factory=list)
    added_conflicting_turn_ids: list[int] = Field(default_factory=list)
    extraction_confidences: list[float] = Field(default_factory=list)


class InterviewMemory(ProgressiveModel):
    user_position: str | None = None
    main_reasons: list[str] = Field(default_factory=list)
    unresolved_threads: list[str] = Field(default_factory=list)
    prior_decision_formed: bool = False


class PlannerBudget(ProgressiveModel):
    used_turns: int = Field(ge=0)
    remaining_turns: int = Field(ge=0)
    reserved_update_turns: int = Field(ge=0)
    reserved_closure_turns: int = Field(ge=0)


class InterviewPlanOutput(ProgressiveModel):
    response_intent: ResponseIntent
    action: PlannerAction
    active_topic: str = Field(min_length=1, max_length=120)
    target_dimension: str | None = None
    target_evidence: str | None = None
    release_event_code: str | None = None
    release_unit_code: str | None = None
    delivery_mode: DeliveryMode
    question_intent: str = Field(min_length=1, max_length=300)
    reflection_basis_turn_ids: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)
    evidence_observations: list[EvidenceObservation] = Field(default_factory=list)
    memory_update: InterviewMemory = Field(default_factory=InterviewMemory)
    budget: PlannerBudget
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_action(self) -> "InterviewPlanOutput":
        if self.action == "RELEASE_EVENT" and (
            not self.release_event_code or not self.release_unit_code
        ):
            raise ValueError("RELEASE_EVENT requires event and presentation unit")
        if self.action != "RELEASE_EVENT" and (
            self.release_event_code is not None or self.release_unit_code is not None
        ):
            raise ValueError("only RELEASE_EVENT can select an event unit")
        if self.action in {"PROBE", "CHALLENGE"} and not self.target_dimension:
            raise ValueError("evidence-seeking action requires target_dimension")
        return self


class InterviewQualityFlags(ProgressiveModel):
    single_focus: bool
    faithful_reflection: bool
    non_judgmental: bool
    non_leading: bool
    no_internal_terms: bool
    no_unreleased_facts: bool


class ReflectionSourceQuote(ProgressiveModel):
    turn_id: int
    quote: str = Field(min_length=1, max_length=500)


class InterviewerOutput(ProgressiveModel):
    message: str = Field(min_length=1, max_length=500)
    message_type: Literal[
        "opening", "followup", "event", "clarification", "integration", "closing"
    ]
    question_count: int = Field(ge=0, le=1)
    introduced_fact_codes: list[str] = Field(default_factory=list)
    reflection_turn_ids: list[int] = Field(default_factory=list)
    reflection_source_quotes: list[ReflectionSourceQuote] = Field(default_factory=list)
    quality_flags: InterviewQualityFlags
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class ConsultativeTurnOutput(ProgressiveModel):
    plan: InterviewPlanOutput | None = None
    interviewer: InterviewerOutput


class InterviewState(ProgressiveModel):
    schema_version: Literal["interview_state_v3", "interview_state_v3_3"] = "interview_state_v3"
    current_node_code: str
    formal_user_turn_count: int = Field(default=0, ge=0, le=12)
    released_event_codes: list[str] = Field(default_factory=list)
    released_unit_codes: list[str] = Field(default_factory=list)
    active_topic: str = "初始判断"
    topic_probe_counters: dict[str, int] = Field(default_factory=dict)
    dimension_probe_counters: dict[str, int] = Field(default_factory=dict)
    consecutive_dimension: str | None = None
    consecutive_dimension_count: int = 0
    clarification_count_for_last_answer: int = 0
    dimension_slots: dict[str, DimensionSlotState] = Field(default_factory=dict)
    memory: InterviewMemory = Field(default_factory=InterviewMemory)
    last_plan: dict[str, Any] | None = None
    evidence_timeline: list[dict[str, Any]] = Field(default_factory=list)
    identity_constraints: dict[str, Any] = Field(default_factory=dict)
    task_domain: str | None = None
    fact_envelope_codes: list[str] = Field(default_factory=list)
    opening_status: Literal["pending", "saved"] = "saved"
    turn_latency_budget_ms: int = Field(default=15000, ge=1000, le=30000)
    asked_intent_keys: list[str] = Field(default_factory=list)
    dimension_opportunity_counts: dict[str, int] = Field(default_factory=dict)
    dimension_opportunity_quality: dict[str, int] = Field(default_factory=dict)
    context_repair_count: int = Field(default=0, ge=0)
    technical_fallback_count: int = Field(default=0, ge=0)
    weak_evidence_turn_ids: dict[str, list[int]] = Field(default_factory=dict)
    initial_decision_prompted: bool = False


__all__ = [
    "DeliveryMode",
    "ConsultativeTurnOutput",
    "DimensionSlotState",
    "EvidenceDeltaAudit",
    "EvidenceDisposition",
    "EvidenceObservation",
    "EvidenceResponseOrigin",
    "InterviewPlanOutput",
    "InterviewQualityFlags",
    "InterviewerOutput",
    "InterviewState",
    "ReflectionSourceQuote",
    "PlannerAction",
    "PlannerBudget",
    "ResponseIntent",
]
