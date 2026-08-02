from __future__ import annotations

"""Stage question contracts: config-driven probes and structural constraints.

A stage's question contract lives in ``exit_criteria_json["question_contract"]``:

    {
        "probes": [
            {
                "when": {"核心判断": ["partial", "covered"], "限制条件": ["missing"]},
                "question": "...",
                "evidence_gap": "...",
                "trigger_reason": "...",
                "target_dimensions": ["problem_definition"],
                "question_type": "open_followup",
            },
            ...
        ],
        "constraints": ["single_question_mark", "no_compound_request", ...],
        "fallback_question": null,
    }

Probes are an ordered ladder of fixed questions triggered by evidence-coverage
state; constraints are deterministic structural checks applied to model-worded
questions (DeepSeek stays the only semantic judge — nothing here calls a model).
Stages without an explicit contract fall back to ``DEFAULT_STAGE_CONTRACTS``
so scenarios materialized before this key existed keep today's behavior.
"""

import re
from typing import Any, Callable

from app.agents.schemas import AgentRuntimeContext, StageContext

# Content types that carry a formal AI question in persisted dialogue history.
# (dynamic_info_question turns are persisted as followup_question by the
# session service, so these two types cover every prior question verbatim.)
_QUESTION_CONTENT_TYPES = {"followup_question", "stage_question"}

GENERIC_FALLBACK_QUESTION = "可以具体说说这个判断背后的主要依据吗？"

# Built-in contracts keyed by stage_code, used for scenarios whose
# exit_criteria_json predates the question_contract key. Since the V2.4
# loosening, S1 probes run in strategy_guided mode: the model's own wording is
# adopted when it passes the constraint gate, and the fixed text below is the
# fallback whenever it does not.
DEFAULT_STAGE_CONTRACTS: dict[str, dict[str, Any]] = {
    "s1_problem_definition": {
        "probes": [
            {
                "when": {"核心判断": ["partial", "covered"], "限制条件": ["missing"]},
                "mode": "strategy_guided",
                "question": (
                    "你已经提出了一个要判断的问题。题面里哪一项现实条件最限制你作出这个判断？"
                ),
                "evidence_gap": "限制条件（第一项）",
                "trigger_reason": "核心判断已有证据，限制条件尚未出现，单点追问第一项限制。",
                "target_dimensions": ["problem_definition"],
                "question_type": "open_followup",
            },
            {
                "when": {"核心判断": ["partial", "covered"], "限制条件": ["partial"]},
                "mode": "strategy_guided",
                "question": (
                    "你已经指出了一项限制。除此之外，还有哪一项不同的现实条件也会改变你的判断？"
                ),
                "evidence_gap": "限制条件（第二项）",
                "trigger_reason": "已有一项限制条件，单点追问第二项不同限制。",
                "target_dimensions": ["problem_definition"],
                "question_type": "open_followup",
            },
        ],
        "constraints": [
            "no_reask_core",
            "single_question_mark",
            "no_compound_request",
            "no_cross_stage_duplicate",
        ],
        "fallback_question": None,
    }
}


def load_contract(stage: StageContext) -> dict[str, Any]:
    """Return the stage's question contract; config overrides code defaults."""
    exit_criteria = stage.exit_criteria if isinstance(stage.exit_criteria, dict) else {}
    configured = exit_criteria.get("question_contract")
    if isinstance(configured, dict):
        return configured
    return DEFAULT_STAGE_CONTRACTS.get(stage.stage_code, {})


def probe_coverage_real(resolved_evidence: list) -> dict[str, str]:
    """Coverage view for real mode: DeepSeek labels from validated evidence."""
    return {item.evidence_key: item.coverage for item in resolved_evidence}


def probe_coverage_mock(context: AgentRuntimeContext) -> dict[str, str]:
    """Coverage view for mock mode.

    Mock has no semantic judge, so it approximates the S1 ladder with the
    pre-refactor turn-count semantics: reaching the probe call site implies a
    substantive answer (核心判断 covered), and 限制条件 advances one state per
    formal followup already asked.
    """
    used = count_stage_followups(context)
    if used <= 0:
        constraint_state = "missing"
    elif used == 1:
        constraint_state = "partial"
    else:
        constraint_state = "covered"
    return {"核心判断": "covered", "限制条件": constraint_state}


def count_stage_followups(context: AgentRuntimeContext) -> int:
    return sum(
        1
        for turn in context.dialogue_history
        if turn.speaker == "ai"
        and turn.stage_code == context.stage.stage_code
        and turn.content_type in {"followup_question", "dynamic_info_question"}
    )


def resolve_probe(
    contract: dict[str, Any],
    coverage: dict[str, str],
    *,
    expected_evidence: list[str],
    followups_used: int,
    max_followups: int,
) -> dict[str, object]:
    """Return probe updates for the first matching ladder step, or {}.

    The updates keep the exact shape the pre-refactor S1 router produced so
    downstream merging (advance override, dynamic-info section) is unchanged.
    """
    probes = [probe for probe in (contract.get("probes") or []) if isinstance(probe, dict)]
    if not probes:
        return {}
    if followups_used >= max_followups:
        return {}
    expected = set(expected_evidence or [])
    for probe in probes:
        when = probe.get("when") or {}
        if not when or not isinstance(when, dict):
            continue
        # Probes only apply to stages that actually measure these evidence keys
        # (e.g. the seeded scenario's S1 uses a different vocabulary and must
        # keep its legacy behavior of never probing).
        if not set(when).issubset(expected):
            continue
        if all(coverage.get(key) in set(states or []) for key, states in when.items()):
            return {
                "question": str(probe.get("question") or ""),
                "question_type": str(probe.get("question_type") or "open_followup"),
                "selected_rule_code": None,
                "selected_dynamic_info_code": None,
                "released_dynamic_info_text": None,
                "target_dimensions": list(probe.get("target_dimensions") or []),
                "evidence_gap": probe.get("evidence_gap"),
                "trigger_reason": probe.get("trigger_reason"),
                "generation_mode": str(probe.get("mode") or "fixed_question"),
                "ai_generation_weight": 0,
            }
    return {}


def adopt_probe_wording(
    contract: dict[str, Any],
    probe_updates: dict[str, object],
    model_question: str | None,
    context: AgentRuntimeContext,
    *,
    coverage: dict[str, str] | None = None,
) -> tuple[dict[str, object], list[str]]:
    """For strategy_guided probes, prefer compliant model wording.

    Returns ``(overrides, warnings)``: non-empty overrides replace the probe's
    fixed text with the model's own question; otherwise the fixed text stays
    and the warnings explain why the model wording was rejected.
    """
    if not probe_updates:
        return {}, []
    if probe_updates.get("generation_mode") != "strategy_guided":
        return {}, []
    candidate = (model_question or "").strip()
    if not candidate:
        return {}, ["probe_wording_kept_fixed_text:empty_model_question"]
    _, violations = enforce_constraints(
        contract, candidate, context, coverage=coverage
    )
    if violations:
        return {}, [*violations, "probe_wording_kept_fixed_text"]
    return {"question": candidate}, []


def asked_questions(context: AgentRuntimeContext) -> list[str]:
    """Every formal AI question asked so far, across all stages, in order."""
    return [
        turn.content
        for turn in context.dialogue_history
        if turn.speaker == "ai"
        and turn.content_type in _QUESTION_CONTENT_TYPES
        and turn.content
    ]


def enforce_constraints(
    contract: dict[str, Any],
    question: str,
    context: AgentRuntimeContext,
    *,
    coverage: dict[str, str] | None = None,
    selected_rule_code: str | None = None,
) -> tuple[str, list[str]]:
    """Validate a model-worded question against the contract's constraints.

    Returns ``(final_question, violation_warnings)``. When any constraint is
    violated the question is replaced through the fallback chain: selected
    rule's fallback_question -> contract fallback_question -> generic fallback.
    Checks are purely structural (regex/count/string comparison).
    """
    names = [str(name) for name in (contract.get("constraints") or [])]
    if not names or not question:
        return question, []
    active_coverage = coverage or {}
    violations: list[str] = []
    for name in names:
        checker = CONSTRAINT_CHECKS.get(name)
        if checker is None:
            continue
        if checker(question, context, active_coverage):
            violations.append(f"question_contract_violation:{name}")
    if not violations:
        return question, []
    violations.append(f"question_contract_rejected_question:{question[:60]}")
    return _fallback_question(contract, context, selected_rule_code), violations


def _fallback_question(
    contract: dict[str, Any],
    context: AgentRuntimeContext,
    selected_rule_code: str | None,
) -> str:
    if selected_rule_code:
        for rule in context.candidate_intervention_rules:
            if rule.rule_code == selected_rule_code and rule.fallback_question:
                return rule.fallback_question
    contract_fallback = contract.get("fallback_question")
    if contract_fallback:
        return str(contract_fallback)
    return GENERIC_FALLBACK_QUESTION


_COMPOUND_PATTERNS = (
    # Request-shaped only: mentioning "两项" while referring back to the user's
    # own items is legitimate ("这两项风险同时出现时…"), asking for two is not.
    re.compile(r"哪两"),
    re.compile(r"两项.{0,4}(是什么|有哪些)"),
    re.compile(r"(列出|说出|给出).{0,6}两"),
    re.compile(r"分别(是什么|有哪些|说明)"),
)

_REASK_CORE_PATTERNS = (
    re.compile(r"核心问题是什么"),
    re.compile(r"最需要.{0,8}(解决|判断|弄清).{0,6}是什么"),
    re.compile(r"要判断的问题是什么"),
)


def _violates_single_question_mark(
    question: str, context: AgentRuntimeContext, coverage: dict[str, str]
) -> bool:
    return question.count("？") + question.count("?") > 1


def _violates_no_compound_request(
    question: str, context: AgentRuntimeContext, coverage: dict[str, str]
) -> bool:
    return any(pattern.search(question) for pattern in _COMPOUND_PATTERNS)


def _violates_no_reask_core(
    question: str, context: AgentRuntimeContext, coverage: dict[str, str]
) -> bool:
    # Only armed once the core judgement already has evidence on record.
    if coverage.get("核心判断") not in {"partial", "covered"}:
        return False
    return any(pattern.search(question) for pattern in _REASK_CORE_PATTERNS)


def _violates_no_cross_stage_duplicate(
    question: str, context: AgentRuntimeContext, coverage: dict[str, str]
) -> bool:
    normalized = _normalize_question(question)
    if len(normalized) < 8:
        return False
    for previous in asked_questions(context):
        candidate = _normalize_question(previous)
        if len(candidate) < 8:
            continue
        if normalized in candidate or candidate in normalized:
            return True
        if _bigram_overlap(normalized, candidate) >= 0.6:
            return True
    return False


def _normalize_question(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-鿿]", "", text or "")


def _bigram_overlap(left: str, right: str) -> float:
    """Overlap coefficient (intersection / smaller set) on character bigrams.

    Chosen over Jaccard because observed drift keeps the core question and only
    swaps the acknowledgement prefix: the 2026-07-17 real S5/S6 repetition
    scores 0.74 here (Jaccard only 0.57) while legitimate same-topic follow-ups
    stay below 0.35.
    """
    left_grams = _bigrams(left)
    right_grams = _bigrams(right)
    if not left_grams or not right_grams:
        return 0.0
    smaller = min(len(left_grams), len(right_grams))
    return len(left_grams & right_grams) / smaller


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


CONSTRAINT_CHECKS: dict[
    str, Callable[[str, AgentRuntimeContext, dict[str, str]], bool]
] = {
    "single_question_mark": _violates_single_question_mark,
    "no_compound_request": _violates_no_compound_request,
    "no_reask_core": _violates_no_reask_core,
    "no_cross_stage_duplicate": _violates_no_cross_stage_duplicate,
}

__all__ = [
    "DEFAULT_STAGE_CONTRACTS",
    "GENERIC_FALLBACK_QUESTION",
    "CONSTRAINT_CHECKS",
    "adopt_probe_wording",
    "asked_questions",
    "count_stage_followups",
    "enforce_constraints",
    "load_contract",
    "probe_coverage_mock",
    "probe_coverage_real",
    "resolve_probe",
]
