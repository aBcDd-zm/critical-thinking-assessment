from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import yaml
from pydantic import ValidationError

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents.measurement_contract import (  # noqa: E402
    DimensionEvidenceDelta,
    DimensionSlotSnapshot,
    MeasurementContract,
    MeasurementEvidenceItem,
    load_measurement_contract,
    validate_contract_against_rubric,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
EXPECTED_DIMENSION_KEYS = {
    "problem_definition",
    "evidence_evaluation",
    "reasoning_argumentation",
    "multiple_perspectives",
    "integrative_decision",
    "dynamic_adjustment",
}
EXPECTED_EVENT_CODES = {
    "opening_context",
    "evidence_uncertainty",
    "stakeholder_conflict",
    "decision_pressure",
    "counter_evidence",
    "integration",
}
EXPECTED_BUDGET = {
    "min_total_user_turns": 9,
    "max_total_user_turns": 12,
    "max_probes_per_topic": 2,
    "max_consecutive_same_dimension": 2,
    "max_clarifications_per_answer": 1,
    "reserved_update_turns": 2,
    "reserved_closure_turns": 1,
}
EXPECTED_CONCLUSIONS = {
    "not_available": "insufficient_evidence",
    "not_started": "insufficient_evidence",
    "partial": "insufficient_evidence",
    "sufficient": "score",
    "blocked": "insufficient_evidence",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_rejected(label: str, factory: Callable[[], object]) -> None:
    try:
        factory()
    except (ValidationError, ValueError):
        return
    raise AssertionError(f"Expected invalid contract data to be rejected: {label}")


def check_contract_artifact(contract: MeasurementContract) -> None:
    require(
        set(contract.slot_statuses) == set(EXPECTED_CONCLUSIONS),
        "The frozen five slot states changed.",
    )

    conclusion = contract.conclusion_policy.model_dump()
    for status, expected in EXPECTED_CONCLUSIONS.items():
        require(
            conclusion[status] == expected,
            f"Invalid conclusion mapping for {status}.",
        )
        require(
            contract.slot_statuses[status].score_eligible
            is (status == "sufficient"),
            f"Invalid score eligibility for {status}.",
        )

    require(
        contract.conclusion_policy.blocked_is_low_score is False,
        "Blocked evidence must not be interpreted as low ability.",
    )
    require(
        contract.budget.model_dump() == EXPECTED_BUDGET,
        "The frozen interview budget changed.",
    )

    require(
        set(item.dimension_key for item in contract.dimensions)
        == EXPECTED_DIMENSION_KEYS,
        "The frozen six measurement dimensions changed.",
    )
    require(
        set(item.event_code for item in contract.events)
        == EXPECTED_EVENT_CODES,
        "The frozen opportunity events changed.",
    )

    event_by_code = {
        item.event_code: item
        for item in contract.events
    }
    dimension_by_key = {
        item.dimension_key: item
        for item in contract.dimensions
    }

    dynamic = dimension_by_key["dynamic_adjustment"]
    require(
        dynamic.initial_status == "not_available",
        "Dynamic adjustment must initially be unavailable.",
    )
    require(
        dynamic.becomes_available_on == ["counter_evidence"],
        "Dynamic adjustment must unlock only on actual counter evidence.",
    )
    require(
        "dynamic_adjustment"
        in event_by_code["counter_evidence"].unlock_dimensions,
        "Counter evidence must unlock dynamic adjustment.",
    )

    for event in contract.events:
        if event.event_code != "counter_evidence":
            require(
                "dynamic_adjustment" not in event.unlock_dimensions,
                f"{event.event_code} must not unlock dynamic adjustment.",
            )

    for dimension in contract.dimensions:
        if dimension.dimension_key != "dynamic_adjustment":
            require(
                dimension.initial_status == "not_started",
                f"{dimension.dimension_key} must initially be not_started.",
            )
            require(
                not dimension.becomes_available_on,
                f"{dimension.dimension_key} must not need an unlock event.",
            )

        event_side_opportunities = {
            event.event_code
            for event in contract.events
            if dimension.dimension_key in event.opportunity_dimensions
        }
        require(
            set(dimension.opportunity_event_codes)
            == event_side_opportunities,
            f"Opportunity declarations disagree for {dimension.dimension_key}.",
        )

        event_side_unlocks = {
            event.event_code
            for event in contract.events
            if dimension.dimension_key in event.unlock_dimensions
        }
        require(
            set(dimension.becomes_available_on) == event_side_unlocks,
            f"Unlock declarations disagree for {dimension.dimension_key}.",
        )

        behavior_keys = {
            item.behavior_key
            for item in dimension.behaviors
        }
        require(
            len(behavior_keys) == len(dimension.behaviors),
            f"Duplicate behavior keys in {dimension.dimension_key}.",
        )
        require(
            set(dimension.partial_when.any_of_behavior_keys)
            == behavior_keys,
            f"Partial coverage must consider all behaviors in "
            f"{dimension.dimension_key}.",
        )
        require(
            dimension.partial_when.min_observed_behaviors
            <= len(behavior_keys),
            f"Unreachable partial condition in {dimension.dimension_key}.",
        )

        positive = dimension.sufficient_positive_when
        require(
            bool(positive.all_of_behavior_keys)
            or bool(positive.any_of_behavior_groups),
            f"Empty positive sufficiency condition in "
            f"{dimension.dimension_key}.",
        )
        require(
            all(positive.any_of_behavior_groups),
            f"Empty positive behavior group in {dimension.dimension_key}.",
        )

        diagnostic = dimension.sufficient_diagnostic_low_when
        require(
            bool(diagnostic.required_response_signals),
            f"Diagnostic-low signals missing in {dimension.dimension_key}.",
        )
        require(
            bool(diagnostic.must_lack_behavior_keys),
            f"Diagnostic-low behavior boundary missing in "
            f"{dimension.dimension_key}.",
        )
        require(
            diagnostic.requires_substantive_response
            and diagnostic.requires_no_technical_failure
            and diagnostic.requires_no_prompt_echo_only,
            f"Diagnostic-low safeguards changed in "
            f"{dimension.dimension_key}.",
        )

    evidence_policy = contract.evidence_policy
    require(
        evidence_policy.source_speaker == "user"
        and evidence_policy.source_id_field == "dialogue_turn_id",
        "Evidence must remain traceable to original user turns.",
    )
    require(
        evidence_policy.allow_cross_node_accumulation
        and evidence_policy.allow_same_turn_multiple_dimensions,
        "Cross-node or same-turn multi-dimension evidence was disabled.",
    )
    require(
        evidence_policy.absence_is_low_ability is False,
        "Absence of evidence must not become low ability.",
    )
    require(
        evidence_policy.conflict_policy == "preserve_both",
        "Contradictory evidence must preserve both sides.",
    )

    confidence = contract.confidence_policy
    require(
        confidence.semantics
        == "evidence_classification_confidence_not_ability",
        "Confidence semantics changed.",
    )
    require(
        confidence.require_new_evidence_for_increase
        and confidence.repeated_evidence_delta == "zero"
        and confidence.contradictory_evidence_effect
        == "must_not_increase"
        and confidence.unchanged_without_evidence_delta,
        "Confidence delta safeguards changed.",
    )
    require(
        confidence.rounding_digits == 4,
        "Confidence delta rounding changed.",
    )


def check_rubric_mapping(contract: MeasurementContract) -> None:
    rubric_path = BACKEND_DIR / "seeds" / "rubric.yaml"
    rubric = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
    validate_contract_against_rubric(contract, rubric)

    rubric_by_key = {
        item["dimension_key"]: item
        for item in rubric["dimensions"]
    }
    for dimension in contract.dimensions:
        expected = rubric_by_key[dimension.dimension_key][
            "observable_behaviors"
        ]
        mapped = [
            item.rubric_behavior
            for item in dimension.behaviors
        ]
        require(
            len(mapped) == len(set(mapped)),
            f"Duplicate rubric behavior mapping in "
            f"{dimension.dimension_key}.",
        )
        require(
            set(mapped) == set(expected),
            f"Rubric behaviors are not mapped one-to-one for "
            f"{dimension.dimension_key}.",
        )


def check_runtime_schema_guards() -> None:
    repeated = DimensionEvidenceDelta(
        dimension_key="problem_definition",
        status_before="partial",
        status_after="partial",
        confidence_before=0.6,
        confidence_after=0.6,
        confidence_delta=0.0,
        delta_type="none",
        repeated_evidence_turn_ids=[7],
    )
    require(
        repeated.confidence_delta == 0
        and repeated.status_before == repeated.status_after,
        "Repeated evidence must preserve status and confidence.",
    )

    expect_rejected(
        "confidence arithmetic mismatch",
        lambda: DimensionEvidenceDelta(
            dimension_key="problem_definition",
            status_before="not_started",
            status_after="partial",
            confidence_before=0.4,
            confidence_after=0.7,
            confidence_delta=0.1,
            delta_type="new_partial",
            added_evidence_turn_ids=[8],
            added_behavior_keys=["distinguish_surface_and_decision"],
        ),
    )

    expect_rejected(
        "contradictory evidence increasing confidence",
        lambda: DimensionEvidenceDelta(
            dimension_key="problem_definition",
            status_before="partial",
            status_after="partial",
            confidence_before=0.5,
            confidence_after=0.7,
            confidence_delta=0.2,
            delta_type="contradictory",
            added_conflicting_turn_ids=[9],
        ),
    )

    expect_rejected(
        "not_available slot containing evidence",
        lambda: DimensionSlotSnapshot(
            dimension_key="dynamic_adjustment",
            status="not_available",
            evidence_turn_ids=[10],
            observed_behavior_keys=[
                "update_or_retain_judgment_with_reason"
            ],
            confidence=0.8,
        ),
    )

    expect_rejected(
        "contradictory evidence without prior reference",
        lambda: MeasurementEvidenceItem(
            evidence_id="evidence-11",
            dimension_key="dynamic_adjustment",
            behavior_key="explain_new_information_impact",
            dialogue_turn_id=11,
            quote="我仍按原方案。",
            char_start=0,
            char_end=7,
            validity="valid",
            source="event_response",
            novelty="contradictory",
            scaffold_level="none",
            node_code="counter_evidence",
            event_code="counter_evidence",
            rationale="回答与先前判断冲突。",
            extraction_confidence=0.8,
        ),
    )


def main() -> None:
    contract = load_measurement_contract()
    check_contract_artifact(contract)
    check_rubric_mapping(contract)
    check_runtime_schema_guards()

    print("Measurement contract semantic checks passed.")
    print(
        "states=5, dimensions=6, events=6, "
        "budget=9-12, dynamic_unlock=counter_evidence"
    )
    print(
        "IE mapping, diagnostic-low safeguards, rubric mapping, "
        "evidence traceability and confidence delta guards: passed"
    )


if __name__ == "__main__":
    main()
