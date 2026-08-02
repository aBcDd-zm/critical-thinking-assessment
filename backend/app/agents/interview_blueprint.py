from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.measurement_contract import load_measurement_contract


BLUEPRINT_VERSION = "occupation_interview_blueprint_v3"
PRESENTATION_VERSION = "consultative_progressive_v3_1"
SKELETON_VERSION = "occupation_interview_skeleton_v3_2"
SKELETON_PRESENTATION_VERSION = "consultative_progressive_v3_2"
SKELETON_V33_VERSION = "occupation_interview_skeleton_v3_3"
SKELETON_V33_PRESENTATION_VERSION = "consultative_progressive_v3_3"

NODE_LAYOUT: tuple[tuple[str, str, str], ...] = (
    ("s1_problem_definition", "opening_context", "opening"),
    ("s2_evidence_verification", "evidence_uncertainty", "exploration"),
    ("s3_stakeholder_perspectives", "stakeholder_conflict", "conflict"),
    ("s4_reasoning_decision", "decision_pressure", "decision"),
    ("s5_dynamic_adjustment", "counter_evidence", "update"),
    ("s6_integrated_plan", "integration", "closure"),
)


class BlueprintModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventReleaseCondition(BlueprintModel):
    after_event_codes: list[str] = Field(default_factory=list)
    requires_any_evidence: list[str] = Field(default_factory=list)
    requires_prior_decision: bool = False
    max_release_turn: int | None = Field(default=None, ge=1, le=12)
    reserved_turns_before_end: int = Field(default=0, ge=0, le=3)


class PresentationUnit(BlueprintModel):
    unit_code: str = Field(min_length=2, max_length=96)
    text: str = Field(min_length=2, max_length=70)
    prerequisite_unit_codes: list[str] = Field(default_factory=list)
    required: bool = False
    counterevidence_direction: Literal["risk", "benefit", "neutral"] = "neutral"


class GeneratedEventCard(BlueprintModel):
    event_code: str = Field(min_length=2, max_length=64)
    node_code: str = Field(min_length=2, max_length=64)
    node_role: Literal[
        "opening", "exploration", "conflict", "decision", "update", "closure"
    ]
    facts: list[str] = Field(min_length=1, max_length=6)
    presentation_units: list[PresentationUnit] = Field(default_factory=list, max_length=8)
    release_condition: EventReleaseCondition
    elicitation_opportunities: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def ensure_presentation_units(self) -> "GeneratedEventCard":
        if self.presentation_units:
            return self
        fallback_units: list[PresentationUnit] = []
        for fact_index, fact in enumerate(self.facts, start=1):
            for part_index, text in enumerate(_atomic_sentences(fact)[:2], start=1):
                fallback_units.append(
                    PresentationUnit(
                        unit_code=(
                            f"{self.event_code}_fact_{fact_index}_{part_index}"
                        ),
                        text=text,
                        required=fact_index == 1 and part_index == 1,
                    )
                )
        self.presentation_units = fallback_units[:8]
        return self


class GeneratedStoryNode(BlueprintModel):
    node_code: str = Field(min_length=2, max_length=64)
    event_code: str = Field(min_length=2, max_length=64)
    node_role: str = Field(min_length=2, max_length=32)
    stable_facts: list[str] = Field(min_length=1, max_length=8)
    question_goal: str = Field(min_length=2, max_length=240)


class ConversationBudget(BlueprintModel):
    min_total_user_turns: int = Field(ge=1, le=12)
    max_total_user_turns: int = Field(ge=1, le=12)
    max_probes_per_topic: int = Field(ge=0, le=4)
    max_consecutive_same_dimension: int = Field(ge=1, le=4)
    max_clarifications_per_answer: int = Field(ge=0, le=2)
    reserved_update_turns: int = Field(ge=0, le=3)
    reserved_closure_turns: int = Field(ge=0, le=2)


class IdentityConstraints(BlueprintModel):
    declared_identity: str = Field(min_length=1, max_length=100)
    allowed_roles: list[str] = Field(default_factory=list, max_length=12)
    forbidden_inferred_roles: list[str] = Field(default_factory=list, max_length=24)
    explicit_responsibilities: list[str] = Field(default_factory=list, max_length=12)
    common_tasks: list[str] = Field(default_factory=list, max_length=12)
    collaborators: list[str] = Field(default_factory=list, max_length=12)


class GeneratedScenarioBlueprint(BlueprintModel):
    schema_version: Literal[
        "occupation_interview_blueprint_v3", "occupation_interview_skeleton_v3_2",
        "occupation_interview_skeleton_v3_3",
    ] = BLUEPRINT_VERSION
    presentation_version: Literal[
        "consultative_progressive_v3_1", "consultative_progressive_v3_2",
        "consultative_progressive_v3_3",
    ] = PRESENTATION_VERSION
    occupation_category: str = Field(min_length=1, max_length=64)
    occupation: str = Field(min_length=1, max_length=64)
    user_role: str = Field(min_length=2, max_length=100)
    title: str = Field(min_length=2, max_length=128)
    core_dilemma: str = Field(min_length=5, max_length=500)
    decision_goal: str = Field(min_length=5, max_length=240)
    opening_event_code: Literal["opening_context"] = "opening_context"
    story_nodes: list[GeneratedStoryNode] = Field(min_length=6, max_length=6)
    event_cards: list[GeneratedEventCard] = Field(min_length=6, max_length=6)
    conversation_budget: ConversationBudget
    identity_constraints: IdentityConstraints | None = None
    task_domain: str | None = Field(default=None, max_length=120)
    fact_envelope_codes: list[str] = Field(default_factory=list, max_length=48)
    current_arrangement: str | None = Field(default=None, max_length=240)
    new_arrangement: str | None = Field(default=None, max_length=240)
    pilot_arrangement: str | None = Field(default=None, max_length=240)
    stakeholder_conflict: str | None = Field(default=None, max_length=300)
    decision_required: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def validate_fixed_structure(self) -> "GeneratedScenarioBlueprint":
        expected_nodes = [item[0] for item in NODE_LAYOUT]
        expected_events = [item[1] for item in NODE_LAYOUT]
        if [item.node_code for item in self.story_nodes] != expected_nodes:
            raise ValueError("progressive v3 story nodes changed")
        if [item.event_code for item in self.event_cards] != expected_events:
            raise ValueError("progressive v3 event functions changed")
        if self.schema_version == SKELETON_V33_VERSION and not all(
            (
                self.current_arrangement,
                self.new_arrangement,
                self.pilot_arrangement,
                self.stakeholder_conflict,
                self.decision_required,
            )
        ):
            raise ValueError("v3.3 skeleton requires concrete arrangements and conflict")
        return self


def build_blueprint_from_generated(
    generated: object,
    *,
    occupation_category: str | None,
    occupation: str | None,
    user_role: str | None = None,
    identity_constraints: IdentityConstraints | None = None,
    task_domain: str | None = None,
    skeleton_v3_2: bool = False,
    skeleton_v3_3: bool = False,
    current_arrangement: str | None = None,
    new_arrangement: str | None = None,
    pilot_arrangement: str | None = None,
    stakeholder_conflict: str | None = None,
    decision_required: str | None = None,
) -> GeneratedScenarioBlueprint:
    contract = load_measurement_contract()
    event_map = {item.event_code: item for item in contract.events}
    stages = {item.stage_code: item for item in generated.stages}
    nodes: list[GeneratedStoryNode] = []
    cards: list[GeneratedEventCard] = []
    previous_event: str | None = None

    for index, (node_code, event_code, node_role) in enumerate(NODE_LAYOUT):
        stage = stages[node_code]
        facts = [stage.context.strip()]
        facts.extend(item.content.strip() for item in stage.dynamic_infos)
        presentation_units = _presentation_units(event_code, stage)
        opportunities = event_map[event_code].opportunity_dimensions
        condition = EventReleaseCondition(
            after_event_codes=[previous_event] if previous_event else [],
            requires_any_evidence=(
                ["problem_definition"] if event_code == "evidence_uncertainty" else []
            ),
            requires_prior_decision=event_code == "counter_evidence",
            max_release_turn=6 if event_code == "stakeholder_conflict" else None,
            reserved_turns_before_end=2 if event_code == "counter_evidence" else 0,
        )
        nodes.append(
            GeneratedStoryNode(
                node_code=node_code,
                event_code=event_code,
                node_role=node_role,
                stable_facts=facts,
                question_goal=_question_goal(event_code),
            )
        )
        cards.append(
            GeneratedEventCard(
                event_code=event_code,
                node_code=node_code,
                node_role=node_role,
                facts=facts,
                presentation_units=presentation_units,
                release_condition=condition,
                elicitation_opportunities=list(opportunities),
            )
        )
        previous_event = event_code

    budget = contract.budget
    blueprint = GeneratedScenarioBlueprint(
        schema_version=(
            SKELETON_V33_VERSION
            if skeleton_v3_3
            else SKELETON_VERSION if skeleton_v3_2 else BLUEPRINT_VERSION
        ),
        presentation_version=(
            SKELETON_V33_PRESENTATION_VERSION
            if skeleton_v3_3
            else SKELETON_PRESENTATION_VERSION if skeleton_v3_2 else PRESENTATION_VERSION
        ),
        occupation_category=occupation_category or "待业/退休/其他",
        occupation=occupation or "当前身份",
        user_role=user_role or f"{occupation or '参与者'}所在团队的项目协调人",
        title=generated.title,
        core_dilemma=generated.central_decision,
        decision_goal="基于逐步出现的信息形成可执行、可调整的决定。",
        story_nodes=nodes,
        event_cards=cards,
        conversation_budget=ConversationBudget(**budget.model_dump()),
        identity_constraints=identity_constraints,
        task_domain=task_domain,
        current_arrangement=current_arrangement,
        new_arrangement=new_arrangement,
        pilot_arrangement=pilot_arrangement,
        stakeholder_conflict=stakeholder_conflict,
        decision_required=decision_required,
    )
    if not (skeleton_v3_2 or skeleton_v3_3):
        return blueprint
    return blueprint.model_copy(
        update={
            "fact_envelope_codes": [
                unit.unit_code
                for event in blueprint.event_cards
                for unit in event.presentation_units
            ]
        }
    )


def blueprint_fingerprint(blueprint: GeneratedScenarioBlueprint) -> str:
    stable = {
        "schema_version": blueprint.schema_version,
        "presentation_version": blueprint.presentation_version,
        "events": [item.event_code for item in blueprint.event_cards],
        "roles": [item.node_role for item in blueprint.event_cards],
        "facts": [item.facts for item in blueprint.event_cards],
        "presentation_units": [
            [unit.model_dump(mode="json") for unit in item.presentation_units]
            for item in blueprint.event_cards
        ],
        "budget": blueprint.conversation_budget.model_dump(),
    }
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _question_goal(event_code: str) -> str:
    return {
        "opening_context": "了解用户最先注意到的问题和判断边界",
        "evidence_uncertainty": "了解用户如何核实信息与评价证据",
        "stakeholder_conflict": "了解用户如何比较不同角色的目标和风险",
        "decision_pressure": "促使用户形成有依据的初步安排",
        "counter_evidence": "观察用户如何解释新信息并调整或保留判断",
        "integration": "形成可执行、可验证、可调整的最终方案",
    }[event_code]


def _presentation_units(event_code: str, stage: object) -> list[PresentationUnit]:
    units: list[PresentationUnit] = []
    context_parts = _atomic_sentences(stage.context)
    for index, text in enumerate(context_parts[:2], start=1):
        units.append(
            PresentationUnit(
                unit_code=f"{event_code}_context_{index}",
                text=text,
                required=index == 1 and event_code != "counter_evidence",
            )
        )
    for info in stage.dynamic_infos:
        direction = {
            "error_rate_increase": "risk",
            "key_user_positive_feedback": "benefit",
        }.get(info.info_code, "neutral")
        for index, text in enumerate(_atomic_sentences(info.content)[:2], start=1):
            units.append(
                PresentationUnit(
                    unit_code=(
                        info.info_code if index == 1 else f"{info.info_code}_{index}"
                    ),
                    text=text,
                    prerequisite_unit_codes=(
                        [f"{event_code}_context_1"]
                        if event_code != "counter_evidence" and context_parts
                        else []
                    ),
                    required=event_code == "counter_evidence",
                    counterevidence_direction=direction,
                )
            )
    if not units:
        units.append(
            PresentationUnit(
                unit_code=f"{event_code}_context_1",
                text="请结合刚出现的情况继续判断。",
                required=True,
            )
        )
    return units[:8]


def _atomic_sentences(value: str) -> list[str]:
    normalized = re.sub(r"\s+", "", value.strip())
    sentences = [
        item.strip("，,；;。 ")
        for item in re.split(r"(?<=[。！？!?；;])", normalized)
        if item.strip("，,；;。 ")
    ]
    units: list[str] = []
    for sentence in sentences or [normalized]:
        if len(sentence) <= 50:
            units.append(sentence.rstrip("。！？!?") + "。")
            continue
        clauses = [item for item in re.split(r"[，,；;]", sentence) if item]
        buffer = ""
        for clause in clauses:
            candidate = f"{buffer}，{clause}" if buffer else clause
            if len(candidate) <= 50:
                buffer = candidate
            else:
                if buffer:
                    units.append(buffer.rstrip("。！？!?") + "。")
                buffer = ""
                remaining = clause
                while len(remaining) > 50:
                    units.append(remaining[:50].rstrip("。！？!?") + "。")
                    remaining = remaining[50:]
                buffer = remaining
        if buffer:
            units.append(buffer.rstrip("。！？!?") + "。")
    return units


__all__ = [
    "BLUEPRINT_VERSION",
    "GeneratedEventCard",
    "IdentityConstraints",
    "PresentationUnit",
    "PRESENTATION_VERSION",
    "SKELETON_PRESENTATION_VERSION",
    "SKELETON_V33_PRESENTATION_VERSION",
    "SKELETON_V33_VERSION",
    "SKELETON_VERSION",
    "GeneratedScenarioBlueprint",
    "GeneratedStoryNode",
    "blueprint_fingerprint",
    "build_blueprint_from_generated",
]
