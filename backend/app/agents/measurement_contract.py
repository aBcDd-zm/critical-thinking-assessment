from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator


SlotStatus = Literal[
    "not_available",
    "not_started",
    "partial",
    "sufficient",
    "blocked",
]

EvidenceValidity = Literal["valid", "weak", "invalid", "unscorable"]
EvidenceSource = Literal[
    "spontaneous",
    "opening",
    "probe",
    "challenge",
    "event_response",
    "integration",
]
EvidenceNovelty = Literal[
    "new",
    "elaborated",
    "repeated",
    "contradictory",
]
ScaffoldLevel = Literal["none", "low", "medium", "high"]
EvidenceDeltaType = Literal[
    "none",
    "new_partial",
    "partial_to_sufficient",
    "corroborating",
    "contradictory",
    "invalidated",
]

EXPECTED_DIMENSION_KEYS = frozenset(
    {
        "problem_definition",
        "evidence_evaluation",
        "reasoning_argumentation",
        "multiple_perspectives",
        "integrative_decision",
        "dynamic_adjustment",
    }
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SlotStatusDefinition(ContractModel):
    meaning: str = Field(min_length=1)
    score_eligible: bool


class EvidencePolicy(ContractModel):
    source_speaker: Literal["user"]
    source_id_field: Literal["dialogue_turn_id"]
    allow_cross_node_accumulation: Literal[True]
    allow_same_turn_multiple_dimensions: Literal[True]
    absence_is_low_ability: Literal[False]
    conflict_policy: Literal["preserve_both"]
    deduplicate_by: list[
        Literal[
            "dimension_key",
            "behavior_key",
            "dialogue_turn_id",
            "polarity",
        ]
    ]
    opportunity_valid_if: list[str] = Field(min_length=1)
    diagnostic_low_requires: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_deduplication_fields(self) -> Self:
        expected = {
            "dimension_key",
            "behavior_key",
            "dialogue_turn_id",
            "polarity",
        }
        if len(self.deduplicate_by) != len(expected):
            raise ValueError("deduplicate_by contains duplicate or missing fields")
        if set(self.deduplicate_by) != expected:
            raise ValueError("deduplicate_by does not match the frozen contract")
        return self


class ConfidencePolicy(ContractModel):
    semantics: Literal["evidence_classification_confidence_not_ability"]
    delta_formula: Literal["confidence_after_minus_before"]
    numeric_mapping_status: Literal["requires_expert_calibration"]
    require_new_evidence_for_increase: Literal[True]
    repeated_evidence_delta: Literal["zero"]
    contradictory_evidence_effect: Literal["must_not_increase"]
    unchanged_without_evidence_delta: Literal[True]
    rounding_digits: int = Field(default=4, ge=0, le=6)


class InterviewBudget(ContractModel):
    min_total_user_turns: int = Field(ge=1)
    max_total_user_turns: int = Field(ge=1)
    max_probes_per_topic: int = Field(ge=0)
    max_consecutive_same_dimension: int = Field(ge=1)
    max_clarifications_per_answer: int = Field(ge=0)
    reserved_update_turns: int = Field(ge=0)
    reserved_closure_turns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        if self.min_total_user_turns > self.max_total_user_turns:
            raise ValueError(
                "min_total_user_turns must not exceed max_total_user_turns"
            )
        reserved = self.reserved_update_turns + self.reserved_closure_turns
        if reserved >= self.max_total_user_turns:
            raise ValueError("reserved turns exhaust the interview budget")
        return self


class ConclusionPolicy(ContractModel):
    not_available: Literal["insufficient_evidence"]
    not_started: Literal["insufficient_evidence"]
    partial: Literal["insufficient_evidence"]
    sufficient: Literal["score"]
    blocked: Literal["insufficient_evidence"]
    blocked_is_low_score: Literal[False]


class ObservableBehaviorRule(ContractModel):
    behavior_key: str = Field(min_length=1)
    rubric_behavior: str = Field(min_length=1)
    valid_evidence_signals: list[str] = Field(min_length=1)
    invalid_evidence_signals: list[str] = Field(default_factory=list)


class PartialCondition(ContractModel):
    any_of_behavior_keys: list[str] = Field(min_length=1)
    min_observed_behaviors: int = Field(default=1, ge=1)
    min_distinct_user_turns: int = Field(default=1, ge=1)


class SufficiencyCondition(ContractModel):
    all_of_behavior_keys: list[str] = Field(default_factory=list)
    any_of_behavior_groups: list[list[str]] = Field(default_factory=list)
    min_distinct_user_turns: int = Field(default=1, ge=1)
    require_explicit_relationship: bool = False
    require_no_unresolved_conflict: bool = True


class DiagnosticLowCondition(ContractModel):
    fair_opportunity_type: str = Field(min_length=1)
    required_response_signals: list[str] = Field(min_length=1)
    must_lack_behavior_keys: list[str] = Field(default_factory=list)
    requires_substantive_response: Literal[True] = True
    requires_no_technical_failure: Literal[True] = True
    requires_no_prompt_echo_only: Literal[True] = True


class EventOpportunity(ContractModel):
    event_code: str = Field(min_length=1)
    opportunity_dimensions: list[str] = Field(min_length=1)
    unlock_dimensions: list[str] = Field(default_factory=list)


class DimensionMeasurementRule(ContractModel):
    dimension_key: str = Field(min_length=1)
    initial_status: Literal["not_available", "not_started"]
    becomes_available_on: list[str] = Field(default_factory=list)
    opportunity_event_codes: list[str] = Field(min_length=1)
    behaviors: list[ObservableBehaviorRule] = Field(min_length=1)
    partial_when: PartialCondition
    sufficient_positive_when: SufficiencyCondition
    sufficient_diagnostic_low_when: DiagnosticLowCondition
    cross_node_accumulation: Literal[True] = True

    @model_validator(mode="after")
    def validate_behavior_references(self) -> Self:
        behavior_keys = [item.behavior_key for item in self.behaviors]
        if len(behavior_keys) != len(set(behavior_keys)):
            raise ValueError(
                f"duplicate behavior_key in {self.dimension_key}"
            )

        known = set(behavior_keys)
        referenced = set(self.partial_when.any_of_behavior_keys)
        referenced.update(self.sufficient_positive_when.all_of_behavior_keys)
        referenced.update(
            key
            for group in self.sufficient_positive_when.any_of_behavior_groups
            for key in group
        )
        referenced.update(
            self.sufficient_diagnostic_low_when.must_lack_behavior_keys
        )

        unknown = referenced - known
        if unknown:
            raise ValueError(
                f"unknown behavior references in {self.dimension_key}: "
                f"{sorted(unknown)}"
            )

        if self.initial_status == "not_available":
            if not self.becomes_available_on:
                raise ValueError(
                    f"{self.dimension_key} needs an availability event"
                )
        elif self.becomes_available_on:
            raise ValueError(
                f"{self.dimension_key} is initially available but also "
                "declares availability events"
            )

        return self


class MeasurementEvidenceItem(ContractModel):
    evidence_id: str = Field(min_length=1)
    dimension_key: str = Field(min_length=1)
    behavior_key: str = Field(min_length=1)
    dialogue_turn_id: PositiveInt
    quote: str = Field(min_length=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    validity: EvidenceValidity
    source: EvidenceSource
    novelty: EvidenceNovelty
    scaffold_level: ScaffoldLevel
    node_code: str = Field(min_length=1)
    event_code: str | None = None
    opportunity_id: str | None = None
    rationale: str = Field(min_length=1)
    extraction_confidence: float = Field(ge=0, le=1)
    contradiction_with: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_quote_span(self) -> Self:
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.novelty == "contradictory" and not self.contradiction_with:
            raise ValueError(
                "contradictory evidence must reference prior evidence"
            )
        return self


class DimensionSlotSnapshot(ContractModel):
    dimension_key: str = Field(min_length=1)
    status: SlotStatus
    evidence_turn_ids: list[PositiveInt] = Field(default_factory=list)
    observed_behavior_keys: list[str] = Field(default_factory=list)
    missing_behavior_keys: list[str] = Field(default_factory=list)
    conflicting_evidence_turn_ids: list[PositiveInt] = Field(
        default_factory=list
    )
    confidence: float | None = Field(default=None, ge=0, le=1)
    insufficient_reason: Literal[
        "no_opportunity",
        "no_valid_response",
        "user_declined",
        "technical_failure",
        "probe_budget_exhausted",
        "unresolved_ambiguity",
        "unresolved_contradiction",
    ] | None = None

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        for values, field_name in [
            (self.evidence_turn_ids, "evidence_turn_ids"),
            (
                self.conflicting_evidence_turn_ids,
                "conflicting_evidence_turn_ids",
            ),
            (self.observed_behavior_keys, "observed_behavior_keys"),
            (self.missing_behavior_keys, "missing_behavior_keys"),
        ]:
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")

        overlap = set(self.observed_behavior_keys) & set(
            self.missing_behavior_keys
        )
        if overlap:
            raise ValueError(
                f"behaviors cannot be observed and missing: {sorted(overlap)}"
            )

        if self.status == "not_available":
            if (
                self.evidence_turn_ids
                or self.observed_behavior_keys
                or self.conflicting_evidence_turn_ids
                or self.confidence is not None
            ):
                raise ValueError(
                    "not_available slot cannot contain evidence or confidence"
                )

        return self


class DimensionEvidenceDelta(ContractModel):
    dimension_key: str = Field(min_length=1)
    status_before: SlotStatus
    status_after: SlotStatus
    confidence_before: float | None = Field(default=None, ge=0, le=1)
    confidence_after: float | None = Field(default=None, ge=0, le=1)
    confidence_delta: float | None = Field(default=None, ge=-1, le=1)
    delta_type: EvidenceDeltaType
    added_evidence_turn_ids: list[PositiveInt] = Field(default_factory=list)
    added_behavior_keys: list[str] = Field(default_factory=list)
    repeated_evidence_turn_ids: list[PositiveInt] = Field(default_factory=list)
    added_conflicting_turn_ids: list[PositiveInt] = Field(default_factory=list)
    resolved_missing_behavior_keys: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        for values, field_name in [
            (
                self.added_evidence_turn_ids,
                "added_evidence_turn_ids",
            ),
            (self.added_behavior_keys, "added_behavior_keys"),
            (
                self.repeated_evidence_turn_ids,
                "repeated_evidence_turn_ids",
            ),
            (
                self.added_conflicting_turn_ids,
                "added_conflicting_turn_ids",
            ),
            (
                self.resolved_missing_behavior_keys,
                "resolved_missing_behavior_keys",
            ),
        ]:
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} must not contain duplicates")

        if (
            self.confidence_before is not None
            and self.confidence_after is not None
        ):
            expected = round(
                self.confidence_after - self.confidence_before,
                4,
            )
            if self.confidence_delta is None:
                raise ValueError("confidence_delta is required")
            if round(self.confidence_delta, 4) != expected:
                raise ValueError(
                    "confidence_delta must equal confidence_after "
                    "minus confidence_before"
                )
        elif self.confidence_delta is not None:
            raise ValueError(
                "confidence_delta must be null when either confidence "
                "endpoint is null"
            )

        if self.delta_type == "none":
            if (
                self.added_evidence_turn_ids
                or self.added_behavior_keys
                or self.added_conflicting_turn_ids
                or self.resolved_missing_behavior_keys
                or self.status_before != self.status_after
                or self.confidence_before != self.confidence_after
            ):
                raise ValueError(
                    "none delta cannot change evidence, status or confidence"
                )

        if self.delta_type == "contradictory":
            if not self.added_conflicting_turn_ids:
                raise ValueError(
                    "contradictory delta requires conflicting evidence"
                )
            if (
                self.confidence_before is not None
                and self.confidence_after is not None
                and self.confidence_after > self.confidence_before
            ):
                raise ValueError(
                    "confidence must not increase on contradictory evidence"
                )

        return self


class MeasurementContract(ContractModel):
    schema_version: Literal["measurement_contract_v1"]
    rubric_version: str = Field(min_length=1)
    slot_statuses: dict[SlotStatus, SlotStatusDefinition]
    evidence_policy: EvidencePolicy
    confidence_policy: ConfidencePolicy
    budget: InterviewBudget
    conclusion_policy: ConclusionPolicy
    events: list[EventOpportunity] = Field(min_length=1)
    dimensions: list[DimensionMeasurementRule] = Field(min_length=6)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        expected_statuses = {
            "not_available",
            "not_started",
            "partial",
            "sufficient",
            "blocked",
        }
        if set(self.slot_statuses) != expected_statuses:
            raise ValueError("slot_statuses must contain the frozen five states")

        dimension_keys = [item.dimension_key for item in self.dimensions]
        if len(dimension_keys) != len(set(dimension_keys)):
            raise ValueError("dimension_key must be unique")
        if set(dimension_keys) != EXPECTED_DIMENSION_KEYS:
            raise ValueError(
                "measurement dimensions do not match the six-dimension rubric"
            )

        event_codes = [item.event_code for item in self.events]
        if len(event_codes) != len(set(event_codes)):
            raise ValueError("event_code must be unique")

        known_events = set(event_codes)
        known_dimensions = set(dimension_keys)

        for event in self.events:
            referenced = set(event.opportunity_dimensions)
            referenced.update(event.unlock_dimensions)
            unknown = referenced - known_dimensions
            if unknown:
                raise ValueError(
                    f"event {event.event_code} references unknown dimensions: "
                    f"{sorted(unknown)}"
                )

        for dimension in self.dimensions:
            unknown_events = (
                set(dimension.opportunity_event_codes)
                | set(dimension.becomes_available_on)
            ) - known_events
            if unknown_events:
                raise ValueError(
                    f"{dimension.dimension_key} references unknown events: "
                    f"{sorted(unknown_events)}"
                )

        dynamic = next(
            item
            for item in self.dimensions
            if item.dimension_key == "dynamic_adjustment"
        )
        if dynamic.initial_status != "not_available":
            raise ValueError(
                "dynamic_adjustment must initially be not_available"
            )

        for dimension in self.dimensions:
            if (
                dimension.dimension_key != "dynamic_adjustment"
                and dimension.initial_status != "not_started"
            ):
                raise ValueError(
                    f"{dimension.dimension_key} must initially be not_started"
                )

        return self


DEFAULT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "seeds"
    / "measurement_contract_v1.yaml"
)


@lru_cache(maxsize=1)
def load_measurement_contract(
    path: Path = DEFAULT_CONTRACT_PATH,
) -> MeasurementContract:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Measurement contract must be a mapping")
    return MeasurementContract.model_validate(raw)


def validate_contract_against_rubric(
    contract: MeasurementContract,
    rubric: dict[str, Any],
) -> None:
    rubric_version = str(rubric.get("version") or "v1")
    if contract.rubric_version != rubric_version:
        raise ValueError(
            f"rubric version mismatch: "
            f"{contract.rubric_version} != {rubric_version}"
        )

    rubric_dimensions = {
        item["dimension_key"]: item
        for item in rubric.get("dimensions", [])
    }
    if set(rubric_dimensions) != EXPECTED_DIMENSION_KEYS:
        raise ValueError(
            "rubric.yaml does not contain the frozen six dimensions"
        )

    for rule in contract.dimensions:
        source = rubric_dimensions[rule.dimension_key]
        observable = set(source.get("observable_behaviors") or [])
        for behavior in rule.behaviors:
            if behavior.rubric_behavior not in observable:
                raise ValueError(
                    f"{rule.dimension_key}.{behavior.behavior_key} "
                    "does not map exactly to rubric.yaml"
                )


__all__ = [
    "ConfidencePolicy",
    "ConclusionPolicy",
    "DimensionEvidenceDelta",
    "DimensionMeasurementRule",
    "DimensionSlotSnapshot",
    "EvidenceDeltaType",
    "EvidencePolicy",
    "EventOpportunity",
    "InterviewBudget",
    "MeasurementContract",
    "MeasurementEvidenceItem",
    "SlotStatus",
    "load_measurement_contract",
    "validate_contract_against_rubric",
]
