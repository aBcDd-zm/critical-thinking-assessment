from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents.dialogue_llm_client import DialogueLLMResult
from app.agents.dialogue_prompts import build_followup_messages
from app.agents.followup_agent import FollowupAgent
from app.agents.mock_dialogue import MockFollowupAgent
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    DynamicInfoContext,
    FollowupOutput,
    ParticipantContext,
    ResolvedEvidenceItem,
    ScenarioContext,
    SessionContext,
    StageContext,
)
from app.agents.user_turn_intent import analyze_user_turn, classify_user_turn
from app.services.session_service import SessionService


class StubLLMClient:
    def __init__(
        self,
        *,
        resolved_category: str,
        success: bool = True,
        include_unsafe_dynamic_info: bool = False,
        selected_dynamic_info_code: str | None = None,
        resolved_evidence: list[ResolvedEvidenceItem] | None = None,
    ) -> None:
        self.resolved_category = resolved_category
        self.success = success
        self.include_unsafe_dynamic_info = include_unsafe_dynamic_info
        self.selected_dynamic_info_code = selected_dynamic_info_code
        self.resolved_evidence = resolved_evidence or []
        self.call_count = 0

    def call_followup(self, context: AgentRuntimeContext) -> DialogueLLMResult:
        self.call_count += 1
        if not self.success:
            return DialogueLLMResult(
                success=False,
                output=None,
                raw_output="",
                error_code="MODEL_TIMEOUT",
                error_reason="stub timeout",
                model_name="stub",
            )
        output = FollowupOutput(
            question=f"自然回复：{context.latest_user_turn.content}",
            content_type="dynamic_info_question"
            if self.include_unsafe_dynamic_info
            else "followup_question",
            question_type="open_followup",
            resolved_response_category=self.resolved_category,
            resolved_evidence=self.resolved_evidence,
            selected_dynamic_info_code=(
                "unsafe_info"
                if self.include_unsafe_dynamic_info
                else self.selected_dynamic_info_code
            ),
            released_dynamic_info_text=(
                "不应释放的动态信息" if self.include_unsafe_dynamic_info else None
            ),
            target_dimensions=["analysis"],
            category_correction_reason="DeepSeek semantic classification",
            reason="stub structured response",
            next_action="ask_followup",
            generation_mode="ai_open",
            ai_generation_weight=100,
            confidence=0.9,
        )
        return DialogueLLMResult(
            success=True,
            output=output,
            raw_output=output.model_dump_json(),
            error_code=None,
            error_reason=None,
            model_name="stub",
        )


def build_context(text: str, *, prior_duplicate: bool = False) -> AgentRuntimeContext:
    latest = DialogueTurnContext(
        turn_id=2,
        turn_index=2,
        stage_id=1,
        stage_code="s1_problem_definition",
        speaker="user",
        content=text,
        content_type="scenario_answer",
    )
    history = []
    if prior_duplicate:
        history.append(
            DialogueTurnContext(
                turn_id=1,
                turn_index=1,
                stage_id=0,
                stage_code="prior_stage",
                speaker="user",
                content=text,
                content_type="scenario_answer",
            )
        )
    history.append(latest)
    context = AgentRuntimeContext(
        session=SessionContext(session_uuid="routing-check", assessment_mode="real"),
        participant=ParticipantContext(nickname="回归用户"),
        scenario=ScenarioContext(
            scenario_code="product_launch_48h",
            title="产品上线前 48 小时",
            background="产品将在 48 小时后上线。",
        ),
        stage=StageContext(
            stage_id=1,
            stage_code="s1_problem_definition",
            stage_order=1,
            title="初始问题界定",
            stage_goal="界定问题",
            context="当前需要明确上线决策边界。",
            main_question="现在最需要先决定什么？",
            max_followups=2,
            exit_criteria={
                "expected_evidence": ["核心问题", "约束条件", "决策边界"]
            },
        ),
        dialogue_history=history,
        latest_user_turn=latest,
        candidate_dynamic_infos=[
            DynamicInfoContext(
                dynamic_info_id=1,
                info_code="unsafe_info",
                title="测试信息",
                content="不应释放的动态信息",
                info_type="test",
                target_dimensions=["analysis"],
            )
        ],
    )
    analysis = analyze_user_turn(context, text)
    latest.analysis_json = analysis
    context.dialogue_history[-1].analysis_json = analysis
    return context


def build_stage2_context(text: str) -> AgentRuntimeContext:
    context = build_context(text)
    context.stage = context.stage.model_copy(
        update={
            "stage_code": "s2_evidence_verification",
            "stage_order": 2,
            "title": "证据核实",
            "stage_goal": "核实证据质量",
            "context": "86条反馈中有19条同步失败。",
            "main_question": "为了判断问题有多严重，你最想核实什么？",
            "exit_criteria": {
                "expected_evidence": ["证据来源", "样本范围", "可靠性判断"]
            },
        }
    )
    context.latest_user_turn.stage_code = context.stage.stage_code
    context.dialogue_history[-1].stage_code = context.stage.stage_code
    context.candidate_dynamic_infos = [
        DynamicInfoContext(
            dynamic_info_id=2,
            info_code="sample_bias_warning",
            title="样本偏差提示",
            content="内测用户主要来自活跃社群，低端设备和弱网样本不足。",
            info_type="counter_evidence",
            trigger_condition="用户只根据现有反馈数量作判断，未考虑样本代表性时释放。",
            priority=10,
            target_dimensions=["evidence_evaluation"],
        )
    ]
    analysis = analyze_user_turn(context, context.latest_user_turn.content)
    context.latest_user_turn.analysis_json = analysis
    context.dialogue_history[-1].analysis_json = analysis
    return context


def check_classification() -> None:
    expected = {
        "我不明白": "clarification_request",
        "没懂": "clarification_request",
        "灰度是什么意思": "term_definition_request",
        "不知道": "low_information",
        "你好": "irrelevant",
        "延期": "substantive_answer",
    }
    for text, intent in expected.items():
        actual = classify_user_turn(text)
        assert actual == intent, (text, actual, intent)

    duplicate = build_context("延期", prior_duplicate=True)
    assert duplicate.latest_user_turn.analysis_json["response_category"] == "redirect"
    boundary = build_stage2_context("我没有别的线索，无法判断")
    assert boundary.latest_user_turn.analysis_json["response_category"] == "assess_answer"
    assert "可靠性判断" in boundary.latest_user_turn.analysis_json["evidence_keys"]


def check_one_call_and_safety() -> None:
    cases = {
        "我认为需要先决定延期": "assess_answer",
        "我不明白": "clarify_question",
        "灰度是什么意思": "explain_term",
        "不知道": "encourage_answer",
    }
    expected_content_types = {
        "assess_answer": "followup_question",
        "clarify_question": "clarification_response",
        "explain_term": "term_explanation",
        "encourage_answer": "guidance_response",
        "redirect": "redirect_response",
    }
    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        for text, resolved in cases.items():
            context = build_context(text)
            stub = StubLLMClient(
                resolved_category=resolved,
                include_unsafe_dynamic_info=resolved != "assess_answer",
            )
            output = FollowupAgent(llm_client=stub).generate(context)
            assert stub.call_count == 1, (text, stub.call_count)
            assert output.resolved_response_category == resolved
            assert output.content_type == expected_content_types[resolved]
            if resolved != "assess_answer":
                assert output.selected_dynamic_info_code is None
                assert output.released_dynamic_info_text is None
                assert output.target_dimensions == []

        corrected_context = build_context("我不明白")
        corrected_stub = StubLLMClient(resolved_category="explain_term")
        corrected = FollowupAgent(llm_client=corrected_stub).generate(corrected_context)
        assert corrected.resolved_response_category == "explain_term"
        assert corrected.category_correction_reason

        prompt_text = "\n".join(
            message["content"] for message in build_followup_messages(corrected_context)
        )
        assert "用户输入分析" not in prompt_text
        assert "本地 response_category" not in prompt_text
        assert "本地 evidence_keys" not in prompt_text

        transition_context = build_context("48小时后先决定是否按期上线")
        transition_stub = StubLLMClient(resolved_category="assess_answer")
        transition = FollowupAgent(llm_client=transition_stub).generate(
            transition_context
        )
        assert transition_stub.call_count == 1
        assert transition.next_action == "advance_stage"
        assert transition.content_type == "advance_prompt"


def check_repeated_clarification_and_failure() -> None:
    context = build_context("我不明白")
    for index in range(3):
        output = MockFollowupAgent().generate(context)
        assert output.content_type == "clarification_response"
        assert "已经说明过两次" not in output.question
        context.dialogue_history.append(
            DialogueTurnContext(
                turn_id=10 + index,
                turn_index=10 + index,
                stage_id=1,
                stage_code=context.stage.stage_code,
                speaker="ai",
                content=output.question,
                content_type=output.content_type,
            )
        )

    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        failed_stub = StubLLMClient(
            resolved_category="clarify_question",
            success=False,
        )
        fallback = FollowupAgent(llm_client=failed_stub).generate(build_context("我不明白"))
        assert failed_stub.call_count == 1
        assert fallback.fallback_used
        assert fallback.content_type == "system_message"
        assert fallback.resolved_response_category is None
        assert fallback.resolved_evidence == []
        assert "重新发送" in fallback.question
        assert any(item.startswith("real model failed:") for item in fallback.warnings)
        assert (
            SessionService._followup_fallback_error_code(fallback)
            == "FOLLOWUP_MODEL_FALLBACK"
        )


def check_s1_single_focus_v23() -> None:
    def v23_context(text: str) -> AgentRuntimeContext:
        context = build_context(text)
        context.stage = context.stage.model_copy(
            update={
                "main_question": (
                    "看完这些信息，你觉得现在最需要先弄清楚的一件事是什么？"
                ),
                "exit_criteria": {
                    "min_user_turns": 1,
                    "completion_mode": "bounded_followup",
                    "expected_evidence": ["核心判断", "限制条件"],
                    "evidence_guidance": {
                        "限制条件": "0项=missing、1项=partial、2项或以上不同限制=covered。",
                        "optional_scoring_evidence": "判断边界可评分但非必答。",
                    },
                },
            }
        )
        context.candidate_dynamic_infos = []
        return context

    def evidence(constraint_coverage: str) -> list[ResolvedEvidenceItem]:
        return [
            ResolvedEvidenceItem(
                evidence_key="核心判断",
                coverage="covered",
                supporting_turn_indexes=[2],
                reason="用户提出了明确的原因诊断问题。",
            ),
            ResolvedEvidenceItem(
                evidence_key="限制条件",
                coverage=constraint_coverage,
                supporting_turn_indexes=[2]
                if constraint_coverage != "missing"
                else [],
                reason="按用户已提出的不同限制数量判断覆盖。",
            ),
        ]

    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        # V2.4 loosening: compliant model wording is adopted for the probe turn.
        first = FollowupAgent(
            llm_client=StubLLMClient(
                resolved_category="assess_answer",
                resolved_evidence=evidence("missing"),
            )
        ).generate(v23_context("为什么活跃度下降"))
        assert first.evidence_gap == "限制条件（第一项）"
        assert first.question == "自然回复：为什么活跃度下降"
        assert first.generation_mode == "strategy_guided"
        assert "两项" not in first.question

        # Violating wording (two question marks) falls back to the fixed probe text.
        rejected = FollowupAgent(
            llm_client=StubLLMClient(
                resolved_category="assess_answer",
                resolved_evidence=evidence("missing"),
            )
        ).generate(v23_context("为什么活跃度下降？效率也在降？"))
        assert rejected.evidence_gap == "限制条件（第一项）"
        assert "哪一项现实条件" in rejected.question
        assert "两项" not in rejected.question
        assert any(
            "probe_wording_kept_fixed_text" in warning
            for warning in rejected.warnings
        ), rejected.warnings

        second = FollowupAgent(
            llm_client=StubLLMClient(
                resolved_category="assess_answer",
                resolved_evidence=evidence("partial"),
            )
        ).generate(v23_context("问卷只覆盖了白天登录的人"))
        assert second.evidence_gap == "限制条件（第二项）"
        assert second.question == "自然回复：问卷只覆盖了白天登录的人"
        assert second.generation_mode == "strategy_guided"

        complete = FollowupAgent(
            llm_client=StubLLMClient(
                resolved_category="assess_answer",
                resolved_evidence=evidence("covered"),
            )
        ).generate(v23_context("时间范围和问卷样本都有限"))
        assert complete.next_action == "advance_stage"
        assert complete.content_type == "advance_prompt"

    mock_context = v23_context("为什么活跃度下降")
    mock_first = MockFollowupAgent().generate(mock_context)
    assert mock_first.evidence_gap == "限制条件（第一项）"
    assert "哪一项现实条件" in mock_first.question
    mock_context.dialogue_history.append(
        DialogueTurnContext(
            turn_id=3,
            turn_index=3,
            stage_id=1,
            stage_code="s1_problem_definition",
            speaker="ai",
            content=mock_first.question,
            content_type=mock_first.content_type,
        )
    )
    first_constraint = DialogueTurnContext(
        turn_id=4,
        turn_index=4,
        stage_id=1,
        stage_code="s1_problem_definition",
        speaker="user",
        content="现有问卷样本范围有限。",
        content_type="scenario_answer",
    )
    first_constraint.analysis_json = analyze_user_turn(
        mock_context,
        first_constraint.content,
    )
    mock_context.dialogue_history.append(first_constraint)
    mock_context.latest_user_turn = first_constraint
    mock_second = MockFollowupAgent().generate(mock_context)
    assert mock_second.evidence_gap == "限制条件（第二项）"
    assert "还有哪一项" in mock_second.question

    mock_context.dialogue_history.append(
        DialogueTurnContext(
            turn_id=5,
            turn_index=5,
            stage_id=1,
            stage_code="s1_problem_definition",
            speaker="ai",
            content=mock_second.question,
            content_type=mock_second.content_type,
        )
    )
    second_constraint = DialogueTurnContext(
        turn_id=6,
        turn_index=6,
        stage_id=1,
        stage_code="s1_problem_definition",
        speaker="user",
        content="可用于核实的时间也有限。",
        content_type="scenario_answer",
    )
    second_constraint.analysis_json = analyze_user_turn(
        mock_context,
        second_constraint.content,
    )
    mock_context.dialogue_history.append(second_constraint)
    mock_context.latest_user_turn = second_constraint
    mock_advance = MockFollowupAgent().generate(mock_context)
    assert mock_advance.next_action == "advance_stage"
    assert mock_advance.content_type == "advance_prompt"


def check_semantic_evidence_and_followup_cap() -> None:
    context = build_context("提高完成的效率")
    stage_code = context.stage.stage_code
    prior_user = DialogueTurnContext(
        turn_id=2,
        turn_index=2,
        stage_id=1,
        stage_code=stage_code,
        speaker="user",
        content="太赶了，做不完。",
        content_type="scenario_answer",
    )
    prior_user.analysis_json = analyze_user_turn(context, prior_user.content)
    second_user = DialogueTurnContext(
        turn_id=4,
        turn_index=4,
        stage_id=1,
        stage_code=stage_code,
        speaker="user",
        content="看看怎么压缩，可以在规定时间完成。",
        content_type="scenario_answer",
    )
    second_user.analysis_json = analyze_user_turn(context, second_user.content)
    latest = context.latest_user_turn
    latest.turn_id = 6
    latest.turn_index = 6
    context.dialogue_history = [
        prior_user,
        DialogueTurnContext(
            turn_id=3,
            turn_index=3,
            stage_id=1,
            stage_code=stage_code,
            speaker="ai",
            content="追问一",
            content_type="followup_question",
        ),
        second_user,
        DialogueTurnContext(
            turn_id=5,
            turn_index=5,
            stage_id=1,
            stage_code=stage_code,
            speaker="ai",
            content="追问二",
            content_type="followup_question",
        ),
        latest,
    ]
    semantic_evidence = [
        ResolvedEvidenceItem(
            evidence_key="核心问题",
            coverage="partial",
            supporting_turn_indexes=[4, 6],
            reason="用户提出压缩工作并提高效率，但尚未明确最终决策对象。",
            confidence=0.82,
        ),
        ResolvedEvidenceItem(
            evidence_key="约束条件",
            coverage="covered",
            supporting_turn_indexes=[2, 4],
            reason="用户明确表达时间紧迫和规定时间限制。",
            confidence=0.93,
        ),
        ResolvedEvidenceItem(
            evidence_key="决策边界",
            coverage="missing",
            supporting_turn_indexes=[],
            reason="尚未说明上线时间或范围边界。",
            confidence=0.88,
        ),
    ]
    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        stub = StubLLMClient(
            resolved_category="assess_answer",
            resolved_evidence=semantic_evidence,
        )
        output = FollowupAgent(llm_client=stub).generate(context)

    assert stub.call_count == 1
    assert output.resolved_response_category == "assess_answer"
    assert output.resolved_evidence == semantic_evidence
    assert output.content_type == "advance_prompt"
    assert output.next_action == "advance_stage"
    assert output.transition_reason == "followup_limit_reached"
    assert "继续补充" not in output.question and "跳过本题" not in output.question

    invalid_context = build_context("提高完成的效率")
    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        invalid = FollowupAgent(
            llm_client=StubLLMClient(
                resolved_category="assess_answer",
                resolved_evidence=[
                    ResolvedEvidenceItem(
                        evidence_key="不存在的证据",
                        coverage="covered",
                        supporting_turn_indexes=[2],
                        reason="非法证据",
                        confidence=0.9,
                    ),
                    ResolvedEvidenceItem(
                        evidence_key="核心问题",
                        coverage="covered",
                        supporting_turn_indexes=[999],
                        reason="非法消息引用",
                        confidence=0.9,
                    ),
                ],
            )
        ).generate(invalid_context)
    assert invalid.resolved_evidence == []
    assert any("ignored semantic evidence" in item for item in invalid.warnings)


def check_dynamic_info_server_fallback() -> None:
    context = build_stage2_context("19条很多，说明问题很严重")

    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        stub = StubLLMClient(
            resolved_category="assess_answer",
            selected_dynamic_info_code="sample_bias_warning",
        )
        output = FollowupAgent(llm_client=stub).generate(context)

    assert stub.call_count == 1
    assert output.selected_dynamic_info_code == "sample_bias_warning"
    assert output.released_dynamic_info_text
    assert output.content_type == "dynamic_info_question"
    assert output.question.startswith("自然回复：")
    assert output.question.count("？") <= 1
    assert "实际来自哪些用户" not in output.question
    assert not any("server selected" in item for item in output.warnings)

    boundary_context = build_stage2_context("我不知道，我也没有别的线索判断啊")
    with patch(
        "app.agents.followup_agent.get_settings",
        return_value=SimpleNamespace(MODEL_GATEWAY_MODE="real"),
    ):
        boundary_stub = StubLLMClient(
            resolved_category="assess_answer",
            resolved_evidence=[
                ResolvedEvidenceItem(
                    evidence_key="可靠性判断",
                    coverage="covered",
                    supporting_turn_indexes=[2],
                    reason="DeepSeek 判断用户明确表达了证据边界。",
                    confidence=0.95,
                )
            ],
        )
        boundary_output = FollowupAgent(llm_client=boundary_stub).generate(
            boundary_context
        )
    assert boundary_stub.call_count == 1
    assert boundary_output.resolved_response_category == "assess_answer"
    reliability = next(
        item
        for item in boundary_output.resolved_evidence
        if item.evidence_key == "可靠性判断"
    )
    assert reliability.coverage == "covered"
    assert boundary_output.content_type == "followup_question"
    assert boundary_output.category_correction_reason == "DeepSeek semantic classification"


def main() -> int:
    check_classification()
    check_one_call_and_safety()
    check_repeated_clarification_and_failure()
    check_s1_single_focus_v23()
    check_semantic_evidence_and_followup_cap()
    check_dynamic_info_server_fallback()
    print("Intelligent dialogue routing checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
