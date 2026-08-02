from __future__ import annotations

import json
from collections.abc import Iterator

from app.agents.schemas import (
    AgentRuntimeContext,
    DynamicInfoContext,
    FollowupOutput,
    InterventionRuleContext,
)
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService


class DialogueTextStreamer:
    def __init__(self, model_gateway_service: ModelGatewayService | None = None) -> None:
        self.model_gateway_service = model_gateway_service or ModelGatewayService(
            get_settings()
        )

    def stream_followup_text(
        self,
        context: AgentRuntimeContext,
        draft_output: FollowupOutput,
    ) -> Iterator[str]:
        request = ModelChatRequest(
            messages=[
                ChatMessage(role="system", content=_FOLLOWUP_TEXT_SYSTEM_PROMPT),
                ChatMessage(
                    role="user",
                    content=_build_followup_text_user_prompt(context, draft_output),
                ),
            ],
            temperature=0.2,
            max_tokens=512,
            json_mode=False,
            thinking_enabled=False,
            reasoning_effort="low",
        )
        streamed = False
        for delta in self.model_gateway_service.stream_chat_text(request):
            streamed = True
            yield delta
        if not streamed and draft_output.selected_dynamic_info_code:
            yield "基于这条新增信息，你会如何调整前面的判断？请说明你的调整依据。"


_FOLLOWUP_TEXT_SYSTEM_PROMPT = """
You are the user-facing AI assessor in an interactive critical-thinking assessment.
Generate only the next message that the participant should see.

Hard rules:
- Reply in Simplified Chinese.
- Output plain text only. Do not output JSON, markdown, headings, labels, scores, rubrics, or internal reasoning.
- Keep the message natural, concise, interview-like, and assessment-oriented.
- Do not reveal scoring standards or the selected rule code.
- The reference follow-up and selected strategy are only candidates. Rewrite or ignore them if they do not match the participant's latest answer.
- Do not say "you mentioned", "you said", or similar wording unless that idea is explicitly present in the latest participant answer.
- If the participant's latest answer is empty, perfunctory, or irrelevant, ask them to provide a concrete answer instead of inferring their position.
- If new scenario information is provided, it is displayed as a separate message. Do not repeat, rephrase, summarize, or bold that information. Only ask the follow-up question that builds on it.
- Ask one clear follow-up question unless the reference question explicitly needs two connected parts.
- Use a humanistic interview style: briefly acknowledge the participant's expressed idea, then ask one evidence-focused probe.
- The idea that there is no single standard answer is stated once in the session opening. Never repeat it or any close paraphrase in a follow-up.
- Do not do counseling, diagnosis, treatment advice, personality judgment, or emotion analysis.
- Do not hint at a high-score answer. Ask "你会如何判断/核实/权衡/调整" instead of telling the participant what to include.
""".strip()


def _build_followup_text_user_prompt(
    context: AgentRuntimeContext,
    draft_output: FollowupOutput,
) -> str:
    selected_rule = _find_rule(context, draft_output.selected_rule_code)
    selected_info = _find_dynamic_info(context, draft_output.selected_dynamic_info_code)
    latest_answer = context.latest_user_turn.content if context.latest_user_turn else ""
    history = "\n".join(
        f"- {turn.speaker}/{turn.content_type}: {turn.content}"
        for turn in context.dialogue_history[-8:]
    )
    dimensions = "\n".join(
        f"- {dimension.name} ({dimension.dimension_key}): {dimension.definition}"
        for dimension in context.rubric_dimensions[:8]
    )

    parts = [
        f"Participant nickname: {context.participant.nickname or '受测者'}",
        f"Scenario title: {context.scenario.title}",
        f"Scenario background: {context.scenario.background}",
        f"Current stage: {context.stage.title} ({context.stage.stage_code})",
        f"Stage goal: {context.stage.stage_goal}",
        f"Stage context: {context.stage.context}",
        f"Main question: {context.stage.main_question}",
        f"Latest participant answer: {latest_answer}",
        "Recent dialogue:",
        history or "- none",
        "Relevant ability dimensions:",
        dimensions or "- none",
        "Candidate follow-up generated from system configuration. Adapt it to the latest answer and ignore it if it conflicts with the answer:",
        draft_output.question,
        f"Reference question type: {draft_output.question_type}",
        f"Draft reflection summary: {draft_output.reflection_summary or ''}",
        f"Draft evidence gap: {draft_output.evidence_gap or ''}",
        f"Draft target dimensions: {draft_output.target_dimensions}",
        f"Draft trigger reason: {draft_output.trigger_reason or ''}",
        f"Draft humanistic steps: {_json_dump(draft_output.humanistic_steps.model_dump(mode='json') if draft_output.humanistic_steps else None)}",
        f"Generation mode: {draft_output.generation_mode or 'strategy_guided'}",
        f"AI generation weight: {draft_output.ai_generation_weight}",
        f"Draft reason: {draft_output.reason}",
        "Visible text requirement:",
        "- Compress the four-step humanistic structure into 1-2 natural Chinese sentences.",
        "- The visible message should feel like an AI interviewer inviting the participant to say more, not like an exam question.",
        "- Ask only one core evidence-gap question.",
    ]

    if selected_rule is not None:
        parts.extend(
            [
                "Selected intervention strategy:",
                f"- type: {selected_rule.rule_type}",
                f"- strategy direction: {selected_rule.strategy_direction}",
                f"- trigger condition: {selected_rule.trigger_condition or ''}",
                f"- sample question: {selected_rule.sample_question or ''}",
                f"- fallback question: {selected_rule.fallback_question or ''}",
                f"- constraints: {_json_dump(selected_rule.question_generation_constraints_json)}",
                f"- target dimensions: {selected_rule.target_dimensions}",
            ]
        )

    if selected_info is not None:
        parts.extend(
            [
                "New scenario information displayed to the participant as a separate message. Do not repeat it; use it only as context for the follow-up question:",
                f"- title: {selected_info.title}",
                f"- content: {selected_info.content}",
                f"- type: {selected_info.info_type}",
                f"- target dimensions: {selected_info.target_dimensions}",
            ]
        )

    return "\n".join(parts)


def _json_dump(value: object) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def sanitize_followup_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline >= 0:
            cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()

    # Defensive: strip repeated dynamic-info prefixes that the model may have added.
    for prefix in ("现在补充一条新信息：", "补充信息：", "补充信息:", "新信息：", "新信息:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()

    # Defensive: strip markdown bold/italic wrappers that leak into plain text.
    while cleaned.startswith("**") and cleaned.endswith("**") and len(cleaned) > 4:
        cleaned = cleaned[2:-2].strip()
    while cleaned.startswith("*") and cleaned.endswith("*") and len(cleaned) > 2:
        cleaned = cleaned[1:-1].strip()

    return cleaned


def _find_rule(
    context: AgentRuntimeContext,
    rule_code: str | None,
) -> InterventionRuleContext | None:
    if rule_code is None:
        return None
    return next(
        (rule for rule in context.candidate_intervention_rules if rule.rule_code == rule_code),
        None,
    )


def _find_dynamic_info(
    context: AgentRuntimeContext,
    info_code: str | None,
) -> DynamicInfoContext | None:
    if info_code is None:
        return None
    return next(
        (info for info in context.candidate_dynamic_infos if info.info_code == info_code),
        None,
    )


__all__ = ["DialogueTextStreamer", "sanitize_followup_text"]
