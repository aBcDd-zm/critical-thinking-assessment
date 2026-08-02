from __future__ import annotations

import re
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any

from app.agents.interview_question_validator import InterviewQuestionValidator
from app.agents.humanistic_v11_intent_registry import (
    INTENT_REGISTRY_VERSION,
    IntentBinding,
    binding_intent_key,
    candidate_semantic_errors,
    resolve_intent_binding,
)
from app.agents.progressive_schemas import InterviewPlanOutput
from app.agents.schemas import AgentRuntimeContext
from app.agents.user_turn_intent import analyze_humanistic_authority_request


MICROSTRUCTURE_VERSION = "humanistic_microstructure_v1_1_ux5"
CANDIDATE_SELECTOR_VERSION = "deterministic_three_select_one_v2"
V11_OUTPUT_MARKER = "humanistic_v1_1_microstructure"
INTERACTION_BRIDGE_VERSION = "grounded_interaction_bridge_v1_1_ux5"

_SPOKEN_PARTICLE_RE = re.compile(r"(?:呀|啊|呢|吧|嘛|哦|啦|呐)+$")

_DOUBLE_SIDE_PATTERNS = (
    re.compile(r"一方面(?P<left>.+?)[，,；;]?\s*另一方面(?P<right>.+)"),
    re.compile(r"虽然(?P<left>.+?)[，,；;]?\s*(?:但是|但|却)(?P<right>.+)"),
    re.compile(
        r"既(?P<left>(?:想|希望|需要).+?)[，,；;]?\s*" r"(?:又怕|又担心|又不愿|同时又)(?P<right>.+)"
    ),
    re.compile(r"(?P<left>(?:想|希望|需要).+?)[，,；;]?\s*" r"(?:但是|但|不过|却)(?P<right>.+)"),
)
_CORRECTION_FOCUS_PATTERNS = (
    re.compile(
        r"(?:我的重点是|我想说的是|我的意思是|准确地说|应该理解为)" r"\s*(?P<focus>[^，,；;。.!！？?\n]{2,36})"
    ),
    re.compile(r"(?:而是|重点在于)\s*" r"(?P<focus>[^，,；;。.!！？?\n]{2,36})"),
    re.compile(
        r"^(?:我)?(?:都|已经)?(?:说了|讲了|回答了|答了)\s*"
        r"(?P<focus>[^，,；;。.!！？?\n]{2,36})"
    ),
    re.compile(
        r"^(?:不是)?(?:都)?(?:说过|讲过|回答过|答过)\s*"
        r"(?P<focus>[^，,；;。.!！？?\n]{2,36})"
    ),
)
_FINAL_QUESTION_RE = re.compile(r"(?P<question>[^。.!！？?\n]+[？?])\s*$")


def build_v11_microstructure(
    context: AgentRuntimeContext,
    plan: InterviewPlanOutput,
    *,
    previous_questions: list[str],
) -> dict[str, Any]:
    authority = analyze_humanistic_authority_request(
        context.latest_user_turn.content if context.latest_user_turn is not None else ""
    )
    pure_authority = authority is not None and authority.kind == "pure"
    mixed_authority = authority is not None and authority.kind == "mixed"
    latest_user_text = (
        context.latest_user_turn.content if context.latest_user_turn is not None else ""
    )
    binding = resolve_intent_binding(
        plan,
        pure_authority=pure_authority,
        latest_user_text=latest_user_text,
    )
    if binding is None:
        candidate_intent_key = "conclude_no_question"
        candidate_audit: list[dict[str, Any]] = []
        selected = {
            "candidate_id": "v11_conclude_no_question",
            "text": "",
            "selection_reason": "conclude_has_no_question",
            "fallback_reason": None,
        }
        mapping_source = "action_rule:conclude"
        mapping_fields = ["action", "question_intent"]
        mapping_fingerprint = ""
        compact_fallback = ""
    else:
        candidate_intent_key = binding_intent_key(plan, binding)
        candidate_audit = _evaluate_candidates(
            binding.family.candidates,
            action=plan.action,
            intent_key=candidate_intent_key,
            binding=binding,
            previous_questions=previous_questions,
        )
        selected = _select_candidate(candidate_audit, binding)
        mapping_source = binding.mapping_source
        mapping_fields = list(binding.mapping_fields)
        mapping_fingerprint = binding.fingerprint
        compact_fallback = binding.family.compact_fallback
    reflection = _reflection_audit(context, plan)
    if pure_authority:
        reflection = {
            "side_type": "none",
            "source_quotes": [],
            "mixed_authority_request": False,
        }
    return {
        "microstructure_version": MICROSTRUCTURE_VERSION,
        "candidate_selector_version": CANDIDATE_SELECTOR_VERSION,
        "intent_registry_version": INTENT_REGISTRY_VERSION,
        "candidate_intent_key": candidate_intent_key,
        "candidate_mapping_source": mapping_source,
        "candidate_mapping_fields": mapping_fields,
        "candidate_mapping_fingerprint": mapping_fingerprint,
        "latest_user_text": latest_user_text,
        "planner_question_intent": plan.question_intent,
        "planner_target_evidence": plan.target_evidence,
        "question_candidates": candidate_audit,
        "selected_candidate_id": selected["candidate_id"],
        "selected_question": selected["text"],
        "selected_candidate_intent_key": candidate_intent_key,
        "selection_reason": selected["selection_reason"],
        "selector_fallback_reason": selected.get("fallback_reason"),
        "semantic_compact_fallback": compact_fallback,
        "selected_question_semantic_groups": (
            [list(group) for group in binding.family.semantic_groups]
            if binding is not None
            else []
        ),
        "reflection_source_quotes": reflection["source_quotes"],
        "reflection_side_type": reflection["side_type"],
        "tentative_check": plan.delivery_mode == "summary_check",
        "interaction_bridge_version": INTERACTION_BRIDGE_VERSION,
        "interaction_bridge_mode": _interaction_bridge_mode(plan, reflection),
        "authority_request_kind": authority.kind if authority is not None else None,
        "pure_authority_request": pure_authority,
        "mixed_authority_request": mixed_authority,
        "autonomy_boundary": (
            "我不能替你作出这个决定"
            if pure_authority
            else "决定仍由你依据情境作出"
            if mixed_authority
            else None
        ),
    }


def compose_v11_message(
    plan: InterviewPlanOutput,
    microstructure: dict[str, Any],
    *,
    event_fact: str | None = None,
) -> str:
    if plan.action == "CONCLUDE":
        return "谢谢，你的回答已经完整记录下来。接下来会基于整段访谈生成报告。"

    question = str(microstructure["selected_question"])
    reflection = _reflection_clause(microstructure)
    autonomy = microstructure.get("autonomy_boundary")
    prefix_parts = [item for item in (reflection, autonomy) if item]

    if plan.action == "RELEASE_EVENT" and event_fact:
        fact = event_fact.rstrip("。！？!?")
        prefix_parts.append(fact)

    if not prefix_parts:
        return question
    return "；".join(prefix_parts) + "；" + question


def compose_v11_correction_acknowledgement(
    user_text: str,
    routed_message: str,
    *,
    target_dimension: str | None = None,
    max_length: int = 90,
) -> tuple[str, str] | None:
    """Acknowledge an explicit correction with an exact user source span."""

    focus = ""
    for pattern in _CORRECTION_FOCUS_PATTERNS:
        match = pattern.search(user_text)
        if match:
            focus = match.group("focus").strip()
            break
    question_match = _FINAL_QUESTION_RE.search(routed_message)
    if not focus or question_match is None:
        return None

    question = question_match.group("question").strip()
    if any(marker in focus for marker in ("开会", "召集", "讨论", "沟通")):
        question = {
            "problem_definition": "如果先开会，你希望大家先弄清哪个问题？",
            "evidence_evaluation": "如果先开会，你希望大家先核实哪项信息？",
            "reasoning_argumentation": "如果先开会，你希望大家先说清哪项依据？",
            "multiple_perspectives": "如果先开会，你希望大家先说清哪处不同意见？",
            "integrative_decision": "如果先开会，你希望大家先确定哪项安排？",
            "dynamic_adjustment": "如果先开会，你希望看到什么结果后再调整？",
        }.get(target_dimension, "如果先开会，你希望大家先弄清什么？")
    while len(focus) >= 4:
        message = f"你说得对，刚才我没有接住“{focus}”；{question}"
        if len(message) <= max_length:
            return message, focus
        focus = focus[:-1].rstrip()
    return None


def fit_v11_length_budget(
    plan: InterviewPlanOutput,
    microstructure: dict[str, Any],
    *,
    event_fact: str | None = None,
    max_length: int = 90,
) -> dict[str, Any]:
    """Fit exact-source reflection to the visible limit without changing intent."""

    fitted = deepcopy(microstructure)
    if len(compose_v11_message(plan, fitted, event_fact=event_fact)) <= max_length:
        return fitted

    if plan.action == "RELEASE_EVENT":
        fitted["compact_event_fact"] = True
        fitted["event_presentation_adjustment"] = "removed_transition_for_length"

    eligible = [item for item in fitted["question_candidates"] if item.get("eligible")]
    if eligible:
        shortest = min(
            eligible,
            key=lambda item: (len(str(item["text"])), int(item["stable_order"])),
        )
        fitted["selected_candidate_id"] = shortest["candidate_id"]
        fitted["selected_question"] = shortest["text"]
        fitted["selection_reason"] = "length_budget_then_stable_order"

    if len(compose_v11_message(plan, fitted, event_fact=event_fact)) > max_length:
        fitted["selected_candidate_id"] = "v11_semantic_length_safe_fallback"
        fitted["selected_question"] = (
            fitted.get("semantic_compact_fallback") or fitted["selected_question"]
        )
        if plan.action == "RELEASE_EVENT":
            fitted["selection_reason"] = "deterministic_length_safe_fallback"
            fitted["selector_fallback_reason"] = "event_message_length_budget"

    if (
        fitted.get("autonomy_boundary")
        and len(compose_v11_message(plan, fitted, event_fact=event_fact)) > max_length
    ):
        fitted["autonomy_boundary"] = "决定由你作出"
        fitted["autonomy_boundary_adjustment"] = "shortened_for_length"

    quotes = list(fitted.get("reflection_source_quotes") or [])
    if len(quotes) > 1:
        fitted["reflection_source_quotes"] = [quotes[0]]
        fitted["reflection_side_type"] = "single"
        fitted["reflection_adjustment_reason"] = "double_to_single_length_budget"

    while len(compose_v11_message(plan, fitted, event_fact=event_fact)) > max_length:
        quotes = fitted.get("reflection_source_quotes") or []
        if not quotes:
            break
        quote = str(quotes[0]["quote"])
        if len(quote) <= 4:
            break
        quotes[0]["quote"] = quote[:-1].rstrip()
        fitted["reflection_adjustment_reason"] = "exact_quote_shortened_for_length"

    if len(compose_v11_message(plan, fitted, event_fact=event_fact)) > max_length:
        fitted["reflection_source_quotes"] = []
        fitted["reflection_side_type"] = "none"
        fitted["reflection_adjustment_reason"] = "omitted_for_length"

    return fitted


def _evaluate_candidates(
    candidates: tuple[str, str, str],
    *,
    action: str,
    intent_key: str,
    binding: IntentBinding,
    previous_questions: list[str],
) -> list[dict[str, Any]]:
    validator = InterviewQuestionValidator()
    normalized_previous = [
        validator._normalize_question(item)  # noqa: SLF001
        for item in previous_questions[-8:]
    ]
    prefix = re.sub(
        r"[^a-z0-9_]+",
        "_",
        intent_key,
    ).strip("_")
    rows: list[dict[str, Any]] = []
    for index, text in enumerate(candidates):
        errors = validator.message_errors(
            text,
            enforce_humanistic_safety=True,
        )
        semantic_errors = candidate_semantic_errors(
            text,
            binding=binding,
            stable_order=index,
        )
        errors.extend(semantic_errors)
        question_count = text.count("？") + text.count("?")
        if action != "CONCLUDE" and question_count != 1:
            errors.append("question_count")
        if len(text) > 90:
            errors.append("too_long")
        normalized = validator._normalize_question(text)  # noqa: SLF001
        similarities = [
            SequenceMatcher(None, normalized, previous).ratio()
            for previous in normalized_previous
            if previous
        ]
        max_similarity = max(similarities, default=0.0)
        if normalized and any(
            normalized in previous or previous in normalized
            for previous in normalized_previous
            if previous
        ):
            errors.append("duplicate_question")
        elif normalized and any(
            validator._semantically_similar(normalized, previous)  # noqa: SLF001
            for previous in normalized_previous
        ):
            errors.append("semantic_duplicate_question")
        errors = list(dict.fromkeys(errors))
        rows.append(
            {
                "candidate_id": f"v11_{prefix}_{index + 1}",
                "text": text,
                "intent_key": intent_key,
                "intent_family": binding.family.family_id,
                "mapping_fingerprint": binding.fingerprint,
                "semantic_contract_codes": semantic_errors,
                "validation_codes": errors,
                "similarity": round(max_similarity, 6),
                "eligible": not errors,
                "stable_order": index,
            }
        )
    return rows


def _select_candidate(
    rows: list[dict[str, Any]],
    binding: IntentBinding,
) -> dict[str, Any]:
    eligible = [row for row in rows if row["eligible"]]
    if eligible:
        selected = min(
            eligible,
            key=lambda row: (-float(1 - row["similarity"]), row["stable_order"]),
        )
        return {
            **selected,
            "selection_reason": "highest_novelty_then_stable_order",
            "fallback_reason": None,
        }

    fallback = binding.family.compact_fallback
    return {
        "candidate_id": "v11_deterministic_safe_fallback",
        "text": fallback,
        "selection_reason": "deterministic_fail_closed_fallback",
        "fallback_reason": "all_three_candidates_ineligible",
    }


def _reflection_audit(
    context: AgentRuntimeContext,
    plan: InterviewPlanOutput,
) -> dict[str, Any]:
    if plan.delivery_mode not in {
        "reflective_probe",
        "summary_check",
        "event_link",
        "perspective_shift",
    }:
        return {
            "side_type": "none",
            "source_quotes": [],
            "mixed_authority_request": False,
        }

    allowed_ids = set(plan.reflection_basis_turn_ids)
    source = next(
        (
            item
            for item in reversed(context.dialogue_history)
            if item.speaker == "user"
            and item.turn_id is not None
            and item.turn_id in allowed_ids
        ),
        None,
    )
    if source is None or source.turn_id is None:
        return {
            "side_type": "none",
            "source_quotes": [],
            "mixed_authority_request": False,
        }

    text = source.content.strip().replace("\n", " ")
    authority = analyze_humanistic_authority_request(text)
    mixed_authority = authority is not None and authority.kind == "mixed"
    substantive = (
        authority.substantive_text
        if mixed_authority and authority.substantive_text
        else text
    )
    double = _double_side_quotes(substantive)
    if double:
        quotes = [{"turn_id": source.turn_id, "quote": quote} for quote in double]
        return {
            "side_type": "double",
            "source_quotes": quotes,
            "mixed_authority_request": mixed_authority,
        }

    quote = _short_exact_quote(substantive)
    return {
        "side_type": "single" if quote else "none",
        "source_quotes": (
            [{"turn_id": source.turn_id, "quote": quote}] if quote else []
        ),
        "mixed_authority_request": mixed_authority,
    }


def _double_side_quotes(text: str) -> tuple[str, str] | None:
    for pattern in _DOUBLE_SIDE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        left = _clean_side(match.group("left"))
        right = _clean_side(match.group("right"))
        if left and right and left in text and right in text:
            return left, right
    return None


def _clean_side(value: str) -> str:
    return normalize_spoken_focus(value)[:24]


def normalize_spoken_focus(text: str) -> str:
    """Remove conversational wrappers without changing the user's topic."""

    focus = text.strip(" ，,；;。！？!?“”\"'")
    for prefix in (
        "我想先确认",
        "我想先了解",
        "我想先弄清",
        "我想知道",
        "我会优先看",
        "我会先看",
        "我会先",
        "先确认",
        "先查",
        "就是",
    ):
        if focus.startswith(prefix):
            remainder = focus[len(prefix) :].strip(" ：:，,；;。！？!?")
            if remainder:
                focus = remainder
            break
    focus = _SPOKEN_PARTICLE_RE.sub("", focus).strip()
    return focus.strip(" ，,；;。！？!?“”\"'")


def _short_exact_quote(text: str) -> str:
    stripped = normalize_spoken_focus(text)
    if not stripped:
        return ""
    if len(stripped) <= 24:
        return stripped
    for marker in ("。", "；", "！", "？", "，", ";", "!", "?", ","):
        if marker in stripped[:30]:
            candidate = stripped.split(marker, 1)[0].strip()
            if candidate:
                return candidate[:24]
    return stripped[:24]


def _reflection_clause(microstructure: dict[str, Any]) -> str:
    quotes = microstructure.get("reflection_source_quotes") or []
    if not quotes:
        return ""
    if microstructure.get("reflection_side_type") == "double" and len(quotes) >= 2:
        clause = f"你同时在考虑{quotes[0]['quote']}和{quotes[1]['quote']}"
        return f"我先确认一下：{clause}" if microstructure.get("tentative_check") else clause
    quote = quotes[0]["quote"]
    mode = microstructure.get("interaction_bridge_mode")
    if mode == "tentative_check":
        return f"我先确认一下：你说的是{quote}"
    if mode == "event_link":
        return ""
    if mode == "perspective_shift":
        return f"换个角度看{quote}"
    # A grounded reflection is optional in live v1.1.  Omitting the stock
    # acknowledgement is more natural than quoting every short answer.
    return ""


def _interaction_bridge_mode(
    plan: InterviewPlanOutput,
    reflection: dict[str, Any],
) -> str:
    if not reflection.get("source_quotes"):
        return "none"
    if plan.delivery_mode == "summary_check":
        return "tentative_check"
    if plan.delivery_mode == "event_link":
        return "event_link"
    if plan.delivery_mode == "perspective_shift":
        return "perspective_shift"
    return "grounded_acknowledgement"


__all__ = [
    "CANDIDATE_SELECTOR_VERSION",
    "MICROSTRUCTURE_VERSION",
    "INTERACTION_BRIDGE_VERSION",
    "V11_OUTPUT_MARKER",
    "build_v11_microstructure",
    "compose_v11_correction_acknowledgement",
    "compose_v11_message",
    "fit_v11_length_budget",
    "normalize_spoken_focus",
]
