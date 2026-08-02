from __future__ import annotations

from app.agents.schemas import AgentRuntimeContext


def collect_user_answer_texts(context: AgentRuntimeContext) -> list[str]:
    return [
        turn.content
        for turn in context.dialogue_history
        if turn.speaker == "user" and turn.content.strip()
    ]


def collect_professional_context(context: AgentRuntimeContext) -> list[str]:
    items = list(context.professional_context)
    for dimension in context.rubric_dimensions:
        items.append(f"{dimension.dimension_key}: {dimension.definition}")
    return items


def build_scoring_context_block(context: AgentRuntimeContext) -> str:
    sections = [
        f"Session: {context.session.session_uuid}",
        f"Scenario: {context.scenario.title}",
        f"Current stage: {context.stage.stage_code} - {context.stage.title}",
        "Rubric dimensions:",
    ]
    for dimension in context.rubric_dimensions:
        sections.append(
            f"- {dimension.dimension_key} | {dimension.name} | {dimension.definition} | "
            f"invalid_evidence={dimension.invalid_evidence_desc or ''}"
        )

    sections.append("Rubric anchors:")
    for anchor in context.rubric_anchors:
        examples = "; ".join(anchor.evidence_examples or [])
        counters = "; ".join(anchor.counter_examples or [])
        sections.append(
            f"- {anchor.dimension_key} level={anchor.score_level} "
            f"{anchor.level_name}: {anchor.behavior_desc}; examples={examples}; counters={counters}"
        )

    sections.append("Stage-to-dimension observation roles:")
    for binding in context.stage_dimension_bindings:
        sections.append(
            f"- stage={binding.stage_code} dimension={binding.dimension_key} "
            f"role={binding.observe_role} weight={binding.weight}"
        )

    sections.extend(
        [
            "Cross-stage semantic evidence rules:",
            "- Primary stages are preferred observation opportunities, not hard evidence boundaries.",
            "- Evaluate every dimension using all eligible substantive user turns across the entire dialogue.",
            "- Evidence from a later or secondary stage may support a dimension observed primarily elsewhere.",
            "- Missing evidence in a primary-stage snapshot lowers confidence or indicates limited measurement opportunity; it does not automatically mean IE or weak ability.",
            "- Mark insufficient_evidence only when no valid evidence for that dimension exists anywhere in the full dialogue.",
            "- Before declaring a behavior absent, search all user turns for explicit thresholds, monitoring, comparison criteria, pause conditions, rollback conditions, actions, and if-then rules.",
        ]
    )
    sections.append("Dialogue:")
    for turn in context.dialogue_history:
        sections.append(
            f"- turn_id={turn.turn_id} stage={turn.stage_code} speaker={turn.speaker} "
            f"type={turn.content_type} analysis={turn.analysis_json or {}}: {turn.content}"
        )
    return "\n".join(sections)


def build_report_context_block(context: AgentRuntimeContext) -> str:
    boundaries = [
        "Report boundaries:",
        "- Do not make clinical diagnosis.",
        "- Do not infer personality or mental illness.",
        "- Use cautious developmental language.",
        "- Evidence quotes must come from dialogue turns.",
    ]
    return build_scoring_context_block(context) + "\n" + "\n".join(boundaries)
