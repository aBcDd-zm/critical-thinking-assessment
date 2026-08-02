"""DEV-AI-002B 真实模型回归测试。

在 MODEL_GATEWAY_MODE=real 下调用 HostAgent / FollowupAgent，
验证输出能通过 HostOutput / FollowupOutput schema 校验。

运行方式：
    cd backend
    MODEL_GATEWAY_MODE=real DEEPSEEK_API_KEY=xxx python scripts/check_dialogue_agent_real.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents.dialogue_llm_client import DialogueLLMClient, DialogueLLMResult
from app.agents.followup_agent import FollowupAgent
from app.agents.host_agent import HostAgent
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    DynamicInfoContext,
    FollowupOutput,
    HostOutput,
    InterventionRuleContext,
    ParticipantContext,
    ScenarioContext,
    ScoreGapSummary,
    SessionContext,
    StageContext,
)


def _build_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        session=SessionContext(session_id=1, session_uuid="real-test"),
        participant=ParticipantContext(participant_id=1, nickname="小秦"),
        scenario=ScenarioContext(
            scenario_id=1,
            scenario_code="product_launch_48h",
            title="产品上线前 48 小时",
            background="团队需要在上线窗口和质量风险之间做决策。",
        ),
        stage=StageContext(
            stage_id=1,
            stage_code="s1_problem_definition",
            stage_order=1,
            title="初始问题界定",
            stage_goal="观察用户能否界定核心问题。",
            context="产品预计 48 小时后发布，但存在同步失败反馈。",
            main_question="你认为当前最核心的决策问题是什么？",
            context_generation_mode="config_guided",
            context_ai_weight=30,
            max_followups=2,
            estimated_minutes=5,
        ),
        dialogue_history=[
            DialogueTurnContext(
                turn_id=1,
                turn_index=1,
                stage_id=1,
                stage_code="s1_problem_definition",
                speaker="ai",
                content="你认为当前最核心的决策问题是什么？",
                content_type="stage_question",
            ),
            DialogueTurnContext(
                turn_id=2,
                turn_index=2,
                stage_id=1,
                stage_code="s1_problem_definition",
                speaker="user",
                content="我觉得要按时上线，市场窗口不能错过。",
                content_type="scenario_answer",
            ),
        ],
        candidate_dynamic_infos=[
            DynamicInfoContext(
                dynamic_info_id=1,
                info_code="error_rate_increase",
                title="核心错误率升高",
                content="最新灰度日志显示，任务同步失败集中在核心协作链路，弱网环境下复现率明显升高。",
                info_type="data_update",
                trigger_condition="用户倾向按时上线且对核心风险关注不足时释放。",
                priority=10,
                target_dimensions=["dynamic_adjustment", "evidence_evaluation"],
            ),
        ],
        candidate_intervention_rules=[
            InterventionRuleContext(
                rule_id=1,
                rule_code="clarify_core_problem",
                rule_type="clarify",
                trigger_condition="用户回答过于笼统，只说上线或延期，没有说明核心问题。",
                strategy_direction="引导用户区分表面压力和真正需要解决的决策问题。",
                sample_question="你刚才提到上线压力，它和产品质量风险之间的关系是什么？",
                question_generation_mode="strategy_guided",
                question_ai_weight=40,
                fallback_question="你能把当前最需要被解决的问题，用一句更明确的话表达出来吗？",
                priority=10,
                max_use_count=1,
                target_dimensions=["problem_definition"],
            ),
            InterventionRuleContext(
                rule_id=10,
                rule_code="advance_final_summary",
                rule_type="advance",
                trigger_condition="用户已经给出完整最终方案。",
                strategy_direction="简短确认用户方案并推进到报告生成阶段。",
                sample_question="我已经记录你的最终方案，接下来将基于完整对话生成测评报告。",
                question_generation_mode="fixed_question",
                question_ai_weight=0,
                fallback_question="我已经记录你的最终方案，接下来将生成测评报告。",
                exit_prompt="我已经记录你的最终方案，接下来将基于完整对话生成测评报告。",
                priority=100,
                max_use_count=1,
                target_dimensions=["integrative_decision", "dynamic_adjustment"],
            ),
        ],
        latest_user_turn=DialogueTurnContext(
            turn_id=2,
            turn_index=2,
            stage_id=1,
            stage_code="s1_problem_definition",
            speaker="user",
            content="我觉得要按时上线，市场窗口不能错过。",
            content_type="scenario_answer",
        ),
        score_gap_summary=ScoreGapSummary(
            missing_dimensions=["problem_definition", "evidence_evaluation"],
            argument_issues=["缺少证据来源"],
            low_score_dimensions=[],
        ),
    )


def main() -> int:
    print("=" * 70)
    print("DEV-AI-002B 真实模型回归测试")
    print("=" * 70)
    print()

    context = _build_context()

    print("[1/4] HostAgent real 模式生成阶段问题...")
    host_agent = HostAgent()
    host_output = host_agent.generate(context)
    assert isinstance(host_output, HostOutput)
    assert host_output.status == "ok"
    assert host_output.agent_name == "host"
    assert bool(host_output.message)
    HostOutput.model_validate(host_output.model_dump())
    print(f"  content_type={host_output.content_type}")
    print(f"  generation_mode={host_output.generation_mode}")
    print(f"  next_action={host_output.next_action}")
    print(f"  message={host_output.message[:120]}...")
    print()

    print("[2/4] FollowupAgent real 模式生成追问...")
    followup_agent = FollowupAgent()
    followup_output = followup_agent.generate(context)
    assert isinstance(followup_output, FollowupOutput)
    assert followup_output.status == "ok"
    assert followup_output.agent_name == "followup"
    assert bool(followup_output.question)
    FollowupOutput.model_validate(followup_output.model_dump())
    print(f"  content_type={followup_output.content_type}")
    print(f"  question_type={followup_output.question_type}")
    print(f"  selected_rule_code={followup_output.selected_rule_code}")
    print(f"  selected_dynamic_info_code={followup_output.selected_dynamic_info_code}")
    print(f"  next_action={followup_output.next_action}")
    print(f"  fallback_used={followup_output.fallback_used}")
    print(f"  question={followup_output.question[:120]}...")
    print()

    print("[3/4] HostAgent 非 JSON 输出兜底...")

    class FakeHostLLMClient(DialogueLLMClient):
        def call_host(self, context: AgentRuntimeContext) -> DialogueLLMResult:
            return DialogueLLMResult(
                success=False,
                output=None,
                raw_output="这不是 JSON",
                error_code="INVALID_OUTPUT",
                error_reason="模拟非 JSON 输出",
                model_name="deepseek-v4-pro",
            )

    host_agent_fallback = HostAgent(llm_client=FakeHostLLMClient())
    host_fallback_output = host_agent_fallback.generate(context)
    assert isinstance(host_fallback_output, HostOutput)
    assert host_fallback_output.status == "ok"
    assert host_fallback_output.fallback_used is True
    assert "real model failed" in " ".join(host_fallback_output.warnings)
    print(f"  fallback_used={host_fallback_output.fallback_used}")
    print(f"  message={host_fallback_output.message[:80]}...")
    print()

    print("[4/4] FollowupAgent 字段缺失输出兜底...")

    class FakeFollowupLLMClient(DialogueLLMClient):
        def call_followup(self, context: AgentRuntimeContext) -> DialogueLLMResult:
            return DialogueLLMResult(
                success=False,
                output=None,
                raw_output='{"question": "缺少必要字段的 JSON"}',
                error_code="INVALID_OUTPUT",
                error_reason="模拟字段缺失",
                model_name="deepseek-v4-pro",
            )

    followup_agent_fallback = FollowupAgent(llm_client=FakeFollowupLLMClient())
    followup_fallback_output = followup_agent_fallback.generate(context)
    assert isinstance(followup_fallback_output, FollowupOutput)
    assert followup_fallback_output.status == "ok"
    assert followup_fallback_output.fallback_used is True
    assert "real model failed" in " ".join(followup_fallback_output.warnings)
    print(f"  fallback_used={followup_fallback_output.fallback_used}")
    print(f"  question={followup_fallback_output.question[:80]}...")
    print()

    print("=" * 70)
    print("DEV-AI-002B 真实模型回归测试通过")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
