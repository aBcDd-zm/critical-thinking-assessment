from __future__ import annotations

"""Unit checks for the stage question-contract engine (no DB, no model).

Covers probe ladder resolution (S1 boundary cases + vocabulary gate +
max_followups guard), both coverage views, every structural constraint
checker, the fallback chain, and contract loading precedence.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents.question_contract import (  # noqa: E402
    DEFAULT_STAGE_CONTRACTS,
    GENERIC_FALLBACK_QUESTION,
    adopt_probe_wording,
    asked_questions,
    enforce_constraints,
    load_contract,
    probe_coverage_mock,
    probe_coverage_real,
    resolve_probe,
)
from app.agents.schemas import ResolvedEvidenceItem  # noqa: E402

V2_EXPECTED = ["核心判断", "限制条件"]
S1_CONTRACT = DEFAULT_STAGE_CONTRACTS["s1_problem_definition"]

FIRST_PROBE_QUESTION = (
    "你已经提出了一个要判断的问题。题面里哪一项现实条件最限制你作出这个判断？"
)
SECOND_PROBE_QUESTION = (
    "你已经指出了一项限制。除此之外，还有哪一项不同的现实条件也会改变你的判断？"
)


def _ai_turn(content: str, *, stage_code: str = "s1_problem_definition",
             content_type: str = "followup_question") -> SimpleNamespace:
    return SimpleNamespace(
        speaker="ai", stage_code=stage_code, content_type=content_type, content=content
    )


def _context(
    *,
    stage_code: str = "s1_problem_definition",
    exit_criteria: dict | None = None,
    max_followups: int = 2,
    history: list | None = None,
    rules: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        stage=SimpleNamespace(
            stage_code=stage_code,
            exit_criteria=exit_criteria or {},
            max_followups=max_followups,
        ),
        dialogue_history=history or [],
        candidate_intervention_rules=rules or [],
    )


def check_probe_ladder() -> None:
    # 1) core partial + constraint missing -> first probe
    first = resolve_probe(
        S1_CONTRACT,
        {"核心判断": "partial", "限制条件": "missing"},
        expected_evidence=V2_EXPECTED,
        followups_used=0,
        max_followups=2,
    )
    assert first.get("evidence_gap") == "限制条件（第一项）", first
    assert first.get("question") == FIRST_PROBE_QUESTION
    assert "哪一项" in str(first.get("question"))
    assert "两项" not in str(first.get("question"))
    assert first.get("generation_mode") == "strategy_guided"
    assert first.get("selected_dynamic_info_code") is None

    # 2) core covered + constraint partial -> second probe
    second = resolve_probe(
        S1_CONTRACT,
        {"核心判断": "covered", "限制条件": "partial"},
        expected_evidence=V2_EXPECTED,
        followups_used=1,
        max_followups=2,
    )
    assert second.get("evidence_gap") == "限制条件（第二项）", second
    assert second.get("question") == SECOND_PROBE_QUESTION
    assert "还有哪一项" in str(second.get("question"))

    # 3) everything covered -> no probe
    assert resolve_probe(
        S1_CONTRACT,
        {"核心判断": "covered", "限制条件": "covered"},
        expected_evidence=V2_EXPECTED,
        followups_used=1,
        max_followups=2,
    ) == {}

    # 4) core still missing -> no probe (the model keeps asking for the core)
    assert resolve_probe(
        S1_CONTRACT,
        {"核心判断": "missing", "限制条件": "missing"},
        expected_evidence=V2_EXPECTED,
        followups_used=0,
        max_followups=2,
    ) == {}

    # 5) seeded-scenario vocabulary -> probes must never fire
    assert resolve_probe(
        S1_CONTRACT,
        {"核心判断": "partial", "限制条件": "missing"},
        expected_evidence=["核心问题", "约束条件", "决策边界"],
        followups_used=0,
        max_followups=2,
    ) == {}

    # 6) followup budget exhausted -> no probe
    assert resolve_probe(
        S1_CONTRACT,
        {"核心判断": "partial", "限制条件": "missing"},
        expected_evidence=V2_EXPECTED,
        followups_used=2,
        max_followups=2,
    ) == {}

    # 7) empty contract -> never probes
    assert resolve_probe(
        {},
        {"核心判断": "partial", "限制条件": "missing"},
        expected_evidence=V2_EXPECTED,
        followups_used=0,
        max_followups=2,
    ) == {}
    print("probe ladder: ok")


def check_coverage_views() -> None:
    real = probe_coverage_real(
        [
            ResolvedEvidenceItem(
                evidence_key="核心判断",
                coverage="partial",
                supporting_turn_indexes=[3],
                reason="用户提出了核心问题。",
            ),
            ResolvedEvidenceItem(
                evidence_key="限制条件",
                coverage="missing",
                reason="尚未提及限制。",
            ),
        ]
    )
    assert real == {"核心判断": "partial", "限制条件": "missing"}, real

    base = _context()
    assert probe_coverage_mock(base)["限制条件"] == "missing"
    one_used = _context(history=[_ai_turn("追问一？")])
    assert probe_coverage_mock(one_used)["限制条件"] == "partial"
    two_used = _context(history=[_ai_turn("追问一？"), _ai_turn("追问二？")])
    assert probe_coverage_mock(two_used)["限制条件"] == "covered"
    # AI turns from other stages or non-question types do not count.
    noise = _context(
        history=[
            _ai_turn("上一阶段追问？", stage_code="s0_intro"),
            _ai_turn("这是动态信息", content_type="dynamic_info"),
        ]
    )
    assert probe_coverage_mock(noise)["限制条件"] == "missing"
    print("coverage views: ok")


def check_constraints() -> None:
    contract = {
        "constraints": [
            "single_question_mark",
            "no_compound_request",
            "no_reask_core",
            "no_cross_stage_duplicate",
        ],
        "fallback_question": None,
    }
    ctx = _context()
    armed = {"核心判断": "partial", "限制条件": "missing"}

    # clean question passes untouched
    clean = "你刚提到进度压力。题面里哪一项现实条件最限制你作出这个判断？"
    question, warnings = enforce_constraints(contract, clean, ctx, coverage=armed)
    assert question == clean and warnings == [], (question, warnings)

    # compound request -> fallback
    question, warnings = enforce_constraints(
        contract, "影响你判断的哪两项限制条件？", ctx, coverage=armed
    )
    assert question == GENERIC_FALLBACK_QUESTION
    assert "question_contract_violation:no_compound_request" in warnings, warnings
    assert any(
        item.startswith("question_contract_rejected_question:") for item in warnings
    ), warnings

    # referring back to the user's own "两项" is NOT a compound request
    reference = "你提到这两项风险同时出现时很难取舍，你会根据什么判断先应对哪一个？"
    question, warnings = enforce_constraints(contract, reference, ctx, coverage=armed)
    assert question == reference and warnings == [], (question, warnings)

    # re-asking the core (the V2.2 drift bug, verbatim) -> fallback when armed
    reask = "那眼下最需要先判断清楚的核心问题是什么？"
    question, warnings = enforce_constraints(contract, reask, ctx, coverage=armed)
    assert question == GENERIC_FALLBACK_QUESTION
    assert "question_contract_violation:no_reask_core" in warnings, warnings
    # ...but passes while the core judgement is still missing
    question, warnings = enforce_constraints(
        contract, reask, ctx, coverage={"核心判断": "missing"}
    )
    assert question == reask and warnings == []

    # more than one question mark -> fallback
    question, warnings = enforce_constraints(
        contract, "这是为什么？你会怎么做？", ctx, coverage=armed
    )
    assert "question_contract_violation:single_question_mark" in warnings

    # cross-stage duplicate: the exact S5->S6 repetition observed on 2026-07-17
    s5_question = (
        "你提到用实时错误率和投诉率作为触发条件，如果灰度期间错误率没超阈值但用户反馈中"
        "频繁出现“数据同步延迟”，你会怎么看待这个信号？"
    )
    s6_question = (
        "你提到分阶段灰度上线，我想了解：如果灰度期间核心错误率没超阈值，但用户反馈中"
        "频繁出现“数据同步延迟”，你会怎么看待这个信号？"
    )
    history_ctx = _context(
        history=[_ai_turn(s5_question, stage_code="s5_dynamic_adjustment")]
    )
    question, warnings = enforce_constraints(
        contract, s6_question, history_ctx, coverage=armed
    )
    assert question == GENERIC_FALLBACK_QUESTION, question
    assert "question_contract_violation:no_cross_stage_duplicate" in warnings, warnings
    # a genuinely different question passes
    fresh = "如果明天开始执行，你安排的第一步是什么？"
    question, warnings = enforce_constraints(contract, fresh, history_ctx, coverage=armed)
    assert question == fresh and warnings == []

    # no constraints configured -> gate off entirely
    question, warnings = enforce_constraints({}, reask, history_ctx, coverage=armed)
    assert question == reask and warnings == []
    print("constraints: ok")


def check_fallback_chain() -> None:
    contract = {"constraints": ["no_compound_request"], "fallback_question": "合同兜底问句？"}
    bad = "请列出其中两项限制。"
    rule = SimpleNamespace(rule_code="clarify_core_problem", fallback_question="规则兜底问句？")
    ctx = _context(rules=[rule])

    question, _ = enforce_constraints(
        contract, bad, ctx, selected_rule_code="clarify_core_problem"
    )
    assert question == "规则兜底问句？", question

    question, _ = enforce_constraints(contract, bad, ctx, selected_rule_code=None)
    assert question == "合同兜底问句？", question

    question, _ = enforce_constraints(
        {"constraints": ["no_compound_request"]}, bad, ctx
    )
    assert question == GENERIC_FALLBACK_QUESTION, question
    print("fallback chain: ok")


def check_contract_loading() -> None:
    configured = {"probes": [], "constraints": ["single_question_mark"]}
    with_config = _context(exit_criteria={"question_contract": configured})
    assert load_contract(with_config.stage) == configured

    without_config = _context(exit_criteria={"expected_evidence": V2_EXPECTED})
    assert load_contract(without_config.stage) == S1_CONTRACT

    other_stage = _context(stage_code="s3_stakeholder_perspectives")
    assert load_contract(other_stage.stage) == {}
    print("contract loading: ok")


def check_asked_questions() -> None:
    ctx = _context(
        history=[
            _ai_turn("第一问？", stage_code="s1_problem_definition"),
            _ai_turn("阶段开场问题？", content_type="stage_question", stage_code="s2_evidence_verification"),
            _ai_turn("动态信息正文", content_type="dynamic_info"),
            SimpleNamespace(
                speaker="user",
                stage_code="s2_evidence_verification",
                content_type="scenario_answer",
                content="用户回答",
            ),
        ]
    )
    assert asked_questions(ctx) == ["第一问？", "阶段开场问题？"]
    print("asked questions: ok")


def check_adopt_probe_wording() -> None:
    ctx = _context()
    armed = {"核心判断": "partial", "限制条件": "missing"}
    probe = resolve_probe(
        S1_CONTRACT,
        armed,
        expected_evidence=V2_EXPECTED,
        followups_used=0,
        max_followups=2,
    )
    assert probe.get("generation_mode") == "strategy_guided"

    # compliant model wording is adopted over the fixed text
    clean = "你刚提到教学进度的担忧。题面里哪一项现实条件最限制你作这个判断？"
    overrides, warnings = adopt_probe_wording(
        S1_CONTRACT, probe, clean, ctx, coverage=armed
    )
    assert overrides == {"question": clean}, overrides
    assert warnings == []

    # violating wording keeps the fixed text and explains why
    overrides, warnings = adopt_probe_wording(
        S1_CONTRACT, probe, "有哪两项限制？影响是什么？", ctx, coverage=armed
    )
    assert overrides == {}, overrides
    assert "probe_wording_kept_fixed_text" in warnings, warnings
    assert any("question_contract_violation:" in item for item in warnings)

    # empty model question keeps the fixed text
    overrides, warnings = adopt_probe_wording(
        S1_CONTRACT, probe, "", ctx, coverage=armed
    )
    assert overrides == {}
    assert warnings == ["probe_wording_kept_fixed_text:empty_model_question"]

    # fixed_question probes never adopt model wording
    fixed_probe = dict(probe, generation_mode="fixed_question")
    overrides, warnings = adopt_probe_wording(
        S1_CONTRACT, fixed_probe, clean, ctx, coverage=armed
    )
    assert overrides == {} and warnings == []

    # no probe fired -> no-op
    overrides, warnings = adopt_probe_wording(S1_CONTRACT, {}, clean, ctx, coverage=armed)
    assert overrides == {} and warnings == []
    print("adopt probe wording: ok")


def main() -> int:
    check_probe_ladder()
    check_coverage_views()
    check_constraints()
    check_fallback_chain()
    check_contract_loading()
    check_asked_questions()
    check_adopt_probe_wording()
    print("Question contract engine checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
