import json
import sys
from pathlib import Path

import yaml

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents import (
    AgentRuntimeContext,
    DialogueTurnContext,
    DynamicInfoContext,
    FollowupOutput,
    HostOutput,
    InterventionRuleContext,
    ParticipantContext,
    ReportOutput,
    RubricAnchorContext,
    RubricDimensionContext,
    ScenarioContext,
    ScoringOutput,
    SessionContext,
    StageContext,
    parse_agent_output,
)
from app.agents.measurement_contract import (
    load_measurement_contract,
    validate_contract_against_rubric,
)


def load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def check_seed_contract() -> dict[str, int]:
    seed_dir = Path(__file__).resolve().parents[1] / "seeds"
    rubric = load_yaml(seed_dir / "rubric.yaml")
    scenario = load_yaml(seed_dir / "scenario_product_48h.yaml")

    measurement_contract = load_measurement_contract()
    validate_contract_against_rubric(measurement_contract, rubric)

    dimensions = rubric.get("dimensions", [])
    dimension_keys = {item["dimension_key"] for item in dimensions}
    if len(dimensions) < 6:
        raise AssertionError("rubric.yaml must contain at least 6 dimensions.")

    for dimension in dimensions:
        if not dimension.get("invalid_evidence_desc"):
            raise AssertionError(f"Missing invalid_evidence_desc: {dimension['dimension_key']}")
        anchors = dimension.get("anchors", [])
        anchor_levels = {anchor["score_level"] for anchor in anchors}
        if anchor_levels != {1, 2, 3, 4, 5}:
            raise AssertionError(
                f"Rubric must define exactly 1-5 anchors: {dimension['dimension_key']}"
            )
        for anchor in anchors:
            if not anchor.get("level_name") or not anchor.get("behavior_desc"):
                raise AssertionError(
                    "Every rubric score needs a level name and behavior standard: "
                    f"{dimension['dimension_key']} score={anchor.get('score_level')}"
                )

    stages = scenario.get("stages", [])
    if len(stages) < 6:
        raise AssertionError("scenario_product_48h.yaml must contain at least 6 stages.")

    dynamic_info_count = 0
    rule_count = 0
    for stage in stages:
        if not stage.get("dimensions"):
            raise AssertionError(f"Stage has no dimensions: {stage['stage_code']}")
        if not 0 <= stage.get("context_ai_weight", 0) <= 100:
            raise AssertionError(f"Invalid context_ai_weight: {stage['stage_code']}")
        exit_criteria = stage.get("exit_criteria_json") or {}
        if exit_criteria.get("completion_mode") != "bounded_followup":
            raise AssertionError(
                f"Stage must use bounded followup completion: {stage['stage_code']}"
            )
        expected_evidence = exit_criteria.get("expected_evidence") or []
        evidence_guidance = exit_criteria.get("evidence_guidance") or {}
        if set(expected_evidence) != set(evidence_guidance):
            raise AssertionError(
                f"Evidence guidance mismatch: {stage['stage_code']}"
            )
        for item in stage.get("dimensions", []):
            if item["dimension_key"] not in dimension_keys:
                raise AssertionError(f"Unknown stage dimension: {item['dimension_key']}")

        for info in stage.get("dynamic_infos", []):
            dynamic_info_count += 1
            for item in info.get("dimensions", []):
                if item["dimension_key"] not in dimension_keys:
                    raise AssertionError(f"Unknown dynamic info dimension: {item['dimension_key']}")

        for rule in stage.get("intervention_rules", []):
            rule_count += 1
            if not rule.get("fallback_question"):
                raise AssertionError(f"Missing fallback_question: {rule['rule_code']}")
            if not 0 <= rule.get("question_ai_weight", 0) <= 100:
                raise AssertionError(f"Invalid question_ai_weight: {rule['rule_code']}")
            for item in rule.get("dimensions", []):
                if item["dimension_key"] not in dimension_keys:
                    raise AssertionError(f"Unknown rule dimension: {item['dimension_key']}")

    if dynamic_info_count < 4:
        raise AssertionError("scenario_product_48h.yaml must contain at least 4 dynamic infos.")
    if rule_count < 8:
        raise AssertionError("scenario_product_48h.yaml must contain at least 8 intervention rules.")

    return {
        "dimensions": len(dimensions),
        "stages": len(stages),
        "dynamic_infos": dynamic_info_count,
        "intervention_rules": rule_count,
        "measurement_dimensions": len(measurement_contract.dimensions),
        "measurement_events": len(measurement_contract.events),
        "min_interview_turns": measurement_contract.budget.min_total_user_turns,
        "max_interview_turns": measurement_contract.budget.max_total_user_turns,
    }


def build_context() -> AgentRuntimeContext:
    user_turn = DialogueTurnContext(
        turn_id=2,
        turn_index=2,
        stage_code="s1_problem_definition",
        speaker="user",
        content="我认为核心问题是质量风险和上线窗口之间的取舍。",
        content_type="scenario_answer",
    )
    return AgentRuntimeContext(
        session=SessionContext(session_id=1, session_uuid="demo-session"),
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
        ),
        dialogue_history=[
            DialogueTurnContext(
                turn_id=1,
                turn_index=1,
                stage_code="s1_problem_definition",
                speaker="ai",
                content="你认为当前最核心的决策问题是什么？",
                content_type="stage_question",
            ),
            user_turn,
        ],
        latest_user_turn=user_turn,
        rubric_dimensions=[
            RubricDimensionContext(
                dimension_key="problem_definition",
                name="问题界定",
                definition="识别核心问题、约束和边界。",
                observable_behaviors=["区分表面压力和核心问题"],
                invalid_evidence_desc="只复述背景不能作为高分证据。",
            )
        ],
        rubric_anchors=[
            RubricAnchorContext(
                dimension_key="problem_definition",
                score_level=3,
                level_name="中",
                behavior_desc="能指出主要问题，但边界说明不完整。",
            )
        ],
        candidate_dynamic_infos=[
            DynamicInfoContext(
                info_code="error_rate_increase",
                title="核心错误率升高",
                content="最新灰度日志显示核心链路错误率升高。",
                info_type="data_update",
                target_dimensions=["dynamic_adjustment"],
            )
        ],
        candidate_intervention_rules=[
            InterventionRuleContext(
                rule_code="clarify_core_problem",
                rule_type="clarify",
                trigger_condition="用户回答过于笼统。",
                strategy_direction="引导用户明确核心问题。",
                fallback_question="你能把当前最需要解决的问题说得更明确一些吗？",
                question_generation_constraints_json={
                    "humanistic_protocol": [
                        "listen",
                        "reflect",
                        "safety_prompt",
                        "evidence_probe",
                    ],
                    "forbidden": ["心理诊断", "暗示标准答案"],
                },
                target_dimensions=["problem_definition"],
            )
        ],
    )


def main() -> None:
    seed_stats = check_seed_contract()
    context = build_context()
    outputs = [
        HostOutput(
            stage_code="s1_problem_definition",
            message="小秦，我们先看这个上线前 48 小时的决策情境。你认为核心问题是什么？",
            reason="根据阶段主问题生成开场问题。",
        ),
        FollowupOutput(
            question=(
                "我听到你正在权衡质量风险和上线窗口，这里没有唯一标准答案。"
                "你会用哪些边界条件来判断这个取舍是否还能接受？"
            ),
            question_type="clarify",
            selected_rule_code="clarify_core_problem",
            target_dimensions=["problem_definition"],
            trigger_reason="用户已经指出冲突，但边界条件仍不明确。",
            reflection_summary="用户正在权衡质量风险和上线窗口。",
            evidence_gap="缺少判断边界条件。",
            humanistic_steps={
                "listening_acknowledgement": "接住用户对质量风险和上线窗口的权衡。",
                "reflective_clarification": "用户已经识别冲突，但还没有说明判断边界。",
                "safety_prompt": "这里没有唯一标准答案，重点是理解判断过程。",
                "evidence_probe": "你会用哪些边界条件来判断这个取舍是否还能接受？",
            },
            generation_mode="strategy_guided",
            ai_generation_weight=40,
            reason="用户已经指出冲突，但边界条件仍不明确。",
            confidence=0.82,
        ),
        ScoringOutput(
            snapshot_type="stage",
            summary="用户能初步界定主要冲突，但边界条件说明仍可补充。",
            scores=[
                {
                    "dimension_key": "problem_definition",
                    "score": 3,
                    "confidence": 0.75,
                    "reason": "用户指出了质量风险和上线窗口的取舍。",
                    "evidence": [
                        {
                            "text": "我认为核心问题是质量风险和上线窗口之间的取舍。",
                            "evidence_type": "user_quote",
                            "explanation": "体现了对核心冲突的初步界定。",
                            "dialogue_turn_id": 2,
                        }
                    ],
                }
            ],
            detected_score_gaps=["决策边界和约束条件仍不够清楚"],
        ),
        ReportOutput(
            summary="本次表现显示你能识别主要冲突，但证据核实和动态调整仍有提升空间。",
            overall_level="中等",
            dimension_reports=[
                {
                    "dimension_key": "problem_definition",
                    "dimension_name": "问题界定",
                    "score": 3,
                    "level_label": "中",
                    "strength": "能够识别上线窗口与质量风险之间的冲突。",
                    "weakness": "对边界条件和受影响对象说明不足。",
                    "evidence_quotes": ["我认为核心问题是质量风险和上线窗口之间的取舍。"],
                    "suggestion": "后续可先列出约束条件、受影响角色和决策边界。",
                }
            ],
            advantages=["能较快抓住主要冲突"],
            improvement_suggestions=["补充证据来源和边界条件"],
            development_plan=["复杂决策前先区分事实、假设和观点。"],
            disclaimer="本报告仅基于本次情境对话表现生成，不作为临床诊断或高风险选拔结论。",
        ),
    ]

    for output in outputs:
        dumped = output.model_dump()
        json.dumps(dumped, ensure_ascii=False)
        parse_agent_output(type(output), dumped)

    print(
        "Agent contract check passed: "
        f"context_fields={len(context.model_dump())}, outputs={len(outputs)}, "
        f"seed={seed_stats}"
    )


if __name__ == "__main__":
    main()
