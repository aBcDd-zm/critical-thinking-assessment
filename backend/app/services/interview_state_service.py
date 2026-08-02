from __future__ import annotations

from copy import deepcopy

from app.agents.interview_blueprint import GeneratedScenarioBlueprint
from app.agents.measurement_contract import load_measurement_contract
from app.agents.progressive_schemas import (
    DimensionSlotState,
    InterviewPlanOutput,
    InterviewState,
)
from app.core.runtime_interview_config import get_runtime_interview_settings
from app.models.assessment import AssessmentSession
from app.models.scenario import Scenario


class InterviewStateService:
    @staticmethod
    def load(session: AssessmentSession, scenario: Scenario) -> InterviewState:
        if session.interview_state_json:
            state = InterviewState.model_validate(session.interview_state_json)
            state.turn_latency_budget_ms = (
                get_runtime_interview_settings()
                .RUNTIME_CONSULTATIVE_TURN_TIMEOUT_SECONDS
                * 1000
            )
            return state
        return InterviewStateService.initialize(session, scenario)

    @staticmethod
    def initialize(
        session: AssessmentSession,
        scenario: Scenario,
    ) -> InterviewState:
        contract = load_measurement_contract()
        slots: dict[str, DimensionSlotState] = {}
        for rule in contract.dimensions:
            behavior_keys = [item.behavior_key for item in rule.behaviors]
            slots[rule.dimension_key] = DimensionSlotState(
                dimension_key=rule.dimension_key,
                status=rule.initial_status,
                missing_behavior_keys=behavior_keys,
            )
        blueprint = InterviewStateService.blueprint(scenario)
        turn_latency_budget_ms = (
            get_runtime_interview_settings()
            .RUNTIME_CONSULTATIVE_TURN_TIMEOUT_SECONDS
            * 1000
        )
        is_consultative = session.flow_version in {
            "progressive_v3_2", "progressive_v3_3"
        }
        is_v33 = session.flow_version == "progressive_v3_3"
        state = InterviewState(
            schema_version="interview_state_v3_3" if is_v33 else "interview_state_v3",
            current_node_code="s1_problem_definition",
            released_event_codes=[] if is_consultative else ["opening_context"],
            released_unit_codes=(
                []
                if is_consultative
                else [blueprint.event_cards[0].presentation_units[0].unit_code]
                if blueprint
                else []
            ),
            dimension_slots=slots,
            identity_constraints=(
                blueprint.identity_constraints.model_dump(mode="json")
                if blueprint and blueprint.identity_constraints
                else {}
            ),
            task_domain=blueprint.task_domain if blueprint else None,
            fact_envelope_codes=(blueprint.fact_envelope_codes if blueprint else []),
            opening_status="pending" if is_consultative else "saved",
            turn_latency_budget_ms=turn_latency_budget_ms,
            dimension_opportunity_counts={key: 0 for key in slots},
            dimension_opportunity_quality={key: 0 for key in slots},
            weak_evidence_turn_ids={key: [] for key in slots},
        )
        session.interview_state_json = state.model_dump(mode="json")
        session.state_version = max(session.state_version or 0, 1)
        return state

    @staticmethod
    def save(
        session: AssessmentSession,
        state: InterviewState,
        *,
        plan: InterviewPlanOutput | None = None,
    ) -> None:
        if plan is not None:
            state.last_plan = plan.model_dump(mode="json")
        session.interview_state_json = deepcopy(state.model_dump(mode="json"))
        session.state_version = (session.state_version or 0) + 1

    @staticmethod
    def blueprint(scenario: Scenario) -> GeneratedScenarioBlueprint | None:
        payload = (scenario.generation_metadata_json or {}).get(
            "interview_blueprint"
        )
        if not payload:
            return None
        return GeneratedScenarioBlueprint.model_validate(payload)


__all__ = ["InterviewStateService"]
