from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.question_contract import DEFAULT_STAGE_CONTRACTS
from app.agents.scenario_design_agent import (
    GeneratedScenario,
    SCENARIO_PROMPT_VERSION,
    STAGE_MAIN_QUESTIONS,
    STAGE_TASK_CONTRACTS,
    build_mock_occupation_scenario,
    scenario_structure_fingerprint,
)
from app.agents.interview_blueprint import (
    BLUEPRINT_VERSION,
    GeneratedScenarioBlueprint,
    PRESENTATION_VERSION,
    blueprint_fingerprint,
    build_blueprint_from_generated,
)
from app.models.rubric import RubricDimension
from app.models.scenario import (
    Scenario,
    ScenarioStage,
    ScenarioStageDimension,
    StageDynamicInfo,
    StageDynamicInfoDimension,
    StageInterventionRule,
    StageInterventionRuleDimension,
)

TEMPLATE_SCENARIO_CODE = "product_launch_48h"
FALLBACK_SCENARIO_CODE = "general_cctst_fallback_v2"
FALLBACK_COPY_VERSION = "fallback_copy_v2_plain_20260731"

STAGE_GOALS = {
    "s1_problem_definition": "观察用户能否从给定事实中界定核心判断、边界和约束。",
    "s2_evidence_verification": "观察用户能否控制结论强度并评价证据来源、样本与局限。",
    "s3_stakeholder_perspectives": "观察用户能否比较多方目标、风险、冲突和优先依据。",
    "s4_reasoning_decision": "观察用户能否连接证据、假设和结论并识别推理漏洞。",
    "s5_dynamic_adjustment": "观察用户能否根据新证据说明保留、修改或推翻的判断部分。",
    "s6_integrated_plan": "观察用户能否形成有依据、有边界、有行动和调整条件的方案。",
}

EXIT_CRITERIA = {
    "s1_problem_definition": ["核心判断", "限制条件"],
    "s2_evidence_verification": ["现有结论", "证据局限", "补充信息"],
    "s3_stakeholder_perspectives": ["相关方目标", "风险冲突", "优先依据"],
    "s4_reasoning_decision": ["初步结论", "主要证据", "关键假设"],
    "s5_dynamic_adjustment": ["原判断", "新证据影响", "调整边界"],
    "s6_integrated_plan": ["最终结论", "行动安排", "不确定性", "调整条件"],
}

EVIDENCE_GUIDANCE = {
    "s1_problem_definition": {
        "核心判断": (
            "用户提出与情境直接相关、可以继续调查或作出判断的问题即可形成证据；"
            "原因诊断也属于有效的问题界定，不要求改写成预设的行动决策句式。"
            "表述具体清楚时为 covered，方向相关但仍较笼统时为 partial，"
            "没有提出可判断问题时才是 missing。"
        ),
        "限制条件": (
            "按不同限制条件的数量判断覆盖：没有提出为 missing，提出一项为 partial，"
            "提出两项或以上不同的现实限制为 covered。每次追问只补一项，不要求用户一轮列举两项。"
        ),
        "optional_scoring_evidence": (
            "判断边界不属于阶段完成条件；用户若主动说明对象、范围、时间或暂时不能确定的部分，"
            "仍保留在正式回答中供问题界定维度评分。"
        ),
        "measurement_note": (
            "回答质量留给评分环节判断；访谈不得为了得到高分句式而反复要求用户改写同一问题。"
        ),
    }
}

# Per-stage question contracts written into exit_criteria_json for scenarios
# materialized from now on (pre-existing rows fall back to the code defaults in
# app.agents.question_contract). S1 keeps its two fixed single-focus probes;
# every stage gets the structural constraint gate, including cross-stage
# question dedup (observed drift: S5's unanswered question re-asked verbatim
# in S6 on 2026-07-17).
_BASE_QUESTION_CONSTRAINTS = [
    "single_question_mark",
    "no_compound_request",
    "no_cross_stage_duplicate",
]

QUESTION_CONTRACTS: dict[str, dict] = {
    "s1_problem_definition": {
        "probes": deepcopy(
            DEFAULT_STAGE_CONTRACTS["s1_problem_definition"]["probes"]
        ),
        "constraints": ["no_reask_core", *_BASE_QUESTION_CONSTRAINTS],
        "fallback_question": None,
    },
    **{
        stage_code: {
            "probes": [],
            "constraints": list(_BASE_QUESTION_CONSTRAINTS),
            "fallback_question": None,
        }
        for stage_code in (
            "s2_evidence_verification",
            "s3_stakeholder_perspectives",
            "s4_reasoning_decision",
            "s5_dynamic_adjustment",
            "s6_integrated_plan",
        )
    },
}

DYNAMIC_TRIGGER_OVERRIDES = {
    "sample_bias_warning": "用户只根据现有数量作判断，未考虑样本范围或代表性时释放。",
    "support_capacity_warning": "用户忽略执行或支持角色的承载限制时释放。",
    "competitor_launch_noise": "用户把未经核实的消息当成确定事实时释放。",
    "error_rate_increase": "用户倾向快速全面实施且没有处理关键风险时释放。",
    "key_user_positive_feedback": "用户倾向完全停止或推迟且没有考虑可逆试行时释放。",
    "limited_engineering_capacity": "用户最终方案缺少资源、责任或执行约束时释放。",
}

RULE_OVERRIDES: dict[str, dict[str, str]] = {
    "clarify_core_problem": {
        "trigger_condition": "用户只复述背景或直接给方案，没有指出需要判断的核心问题。",
        "strategy_direction": "只追问当前最需要判断的一件事。",
        "sample_question": "如果先不谈方案，眼下最需要判断的核心问题是什么？",
        "fallback_question": "请先用一句话说出最需要判断的问题。",
    },
    "trap_binary_decision": {
        "trigger_condition": "用户把问题简化为两个互斥选项，没有考虑条件化或分步安排。",
        "strategy_direction": "追问是否存在第三种或分阶段的处理方式，不暗示具体答案。",
        "sample_question": "除了这两个选项，是否还有带条件或分阶段的安排？",
        "fallback_question": "这件事是否只有这两种处理方式？",
    },
    "clarify_evidence_source": {
        "trigger_condition": "用户说要看信息或数据，但没有说明来源、范围或用途。",
        "strategy_direction": "只追问最需要核实的一类信息及其来源。",
        "sample_question": "如果只能先核实一项信息，你会查什么，又从哪里获得？",
        "fallback_question": "你最想先核实哪一项信息？",
    },
    "challenge_single_metric": {
        "trigger_condition": "用户只依据单一数字、单方意见或单次观察作判断。",
        "strategy_direction": "追问该依据可能遗漏的样本、条件或替代解释。",
        "sample_question": "只看这一项依据，最容易遗漏什么情况？",
        "fallback_question": "除了这一项，你还需要比较什么？",
    },
    "expand_stakeholder_view": {
        "trigger_condition": "用户只从一个角色的立场分析。",
        "strategy_direction": "只追问另一个直接受影响角色的目标和风险。",
        "sample_question": "除了这一方，还有谁会直接受到影响？",
        "fallback_question": "另一个直接相关的角色是谁？",
    },
    "trap_one_sided_view": {
        "trigger_condition": "用户列出多方但没有比较冲突或确定优先级依据。",
        "strategy_direction": "追问两方诉求冲突时采用什么判断标准。",
        "sample_question": "两方目标冲突时，你用什么标准决定先处理哪一项？",
        "fallback_question": "你确定优先级的依据是什么？",
    },
    "build_reasoning_chain": {
        "trigger_condition": "用户给出结论但没有把证据与结论连接起来。",
        "strategy_direction": "只追问支撑当前结论的一项主要证据。",
        "sample_question": "支撑你这个结论的最主要证据是什么？",
        "fallback_question": "你主要依据哪一条信息？",
    },
    "challenge_hidden_assumption": {
        "trigger_condition": "用户的方案依赖未说明或未经验证的假设。",
        "strategy_direction": "只追问方案成立所依赖的一个关键条件。",
        "sample_question": "这个判断成立，需要哪个条件同时成立？",
        "fallback_question": "你的结论依赖什么前提？",
    },
    "release_dynamic_risk_signal": {
        "trigger_condition": "用户已有初步判断，需要用方向相反的新证据检验调整能力。",
        "strategy_direction": "按用户初步立场释放一条反向信息，再追问判断的具体变化。",
        "sample_question": "这条新信息会改变你原判断的哪一部分？",
        "fallback_question": "你会保留、修改还是推翻原判断的哪些部分？",
    },
    "explain_adjustment_boundary": {
        "trigger_condition": "用户表示改变或坚持判断，但没有说明新证据影响了什么。",
        "strategy_direction": "追问新证据影响的前提、结论或行动中的一个部分。",
        "sample_question": "新信息具体改变了你的哪个前提或判断？",
        "fallback_question": "请指出调整对应的是哪条新证据。",
    },
    "complete_action_plan": {
        "trigger_condition": "最终方案缺少行动、责任、阈值或兜底中的关键一项。",
        "strategy_direction": "根据最明显的缺口只追问一个执行要素。",
        "sample_question": "如果明天开始执行，你安排的第一步是什么？",
        "fallback_question": "这个方案的第一步是什么？",
    },
    "advance_final_summary": {
        "trigger_condition": "用户已给出方案但没有说明什么情况会再次调整。",
        "strategy_direction": "只追问一个明确的调整或停止条件。",
        "sample_question": "出现什么信号时，你会改变或停止这个方案？",
        "fallback_question": "什么情况会让你调整方案？",
    },
}


class ScenarioMaterializationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def ensure_fallback(self) -> Scenario:
        existing = self.db.execute(
            select(Scenario).where(Scenario.scenario_code == FALLBACK_SCENARIO_CODE)
        ).scalar_one_or_none()
        if existing is not None:
            metadata = dict(existing.generation_metadata_json or {})
            blueprint_payload = metadata.get("interview_blueprint") or {}
            copy_needs_refresh = (
                metadata.get("fallback_copy_version") != FALLBACK_COPY_VERSION
            )
            if (
                copy_needs_refresh
                or
                not blueprint_payload
                or blueprint_payload.get("presentation_version")
                != PRESENTATION_VERSION
            ):
                generated = build_mock_occupation_scenario("通用", "协作参与者")
                blueprint = build_blueprint_from_generated(
                    generated,
                    occupation_category="待业/退休/其他",
                    occupation="协作参与者",
                )
                if copy_needs_refresh:
                    existing.title = generated.title
                    existing.background = generated.background
                existing.generation_metadata_json = {
                    **metadata,
                    "fallback_copy_version": FALLBACK_COPY_VERSION,
                    "generated_scenario": generated.model_dump(),
                    "structure_fingerprint": scenario_structure_fingerprint(
                        generated
                    ),
                    "interview_blueprint_version": BLUEPRINT_VERSION,
                    "interview_presentation_version": PRESENTATION_VERSION,
                    "interview_blueprint_fingerprint": blueprint_fingerprint(blueprint),
                    "interview_blueprint": blueprint.model_dump(mode="json"),
                }
                self.db.flush()
            return existing
        generated = build_mock_occupation_scenario("通用", "协作参与者")
        return self.materialize(
            generated,
            scenario_code=FALLBACK_SCENARIO_CODE,
            source_type="seeded_fallback",
            occupation_category=None,
            occupation_key=None,
            model_name="seeded",
            base_scenario_id=None,
            is_default=False,
        )

    def materialize(
        self,
        generated: GeneratedScenario,
        *,
        scenario_code: str | None,
        source_type: str,
        occupation_category: str | None,
        occupation_key: str | None,
        model_name: str | None,
        base_scenario_id: int | None,
        is_default: bool = False,
        blueprint_override: GeneratedScenarioBlueprint | None = None,
    ) -> Scenario:
        blueprint = blueprint_override or build_blueprint_from_generated(
            generated,
            occupation_category=occupation_category,
            occupation=(occupation_key or "").split(":", 1)[-1] or None,
        )
        template = self.db.execute(
            select(Scenario).where(Scenario.scenario_code == TEMPLATE_SCENARIO_CODE)
        ).scalar_one_or_none()
        if template is None:
            raise ValueError(
                f"Template scenario {TEMPLATE_SCENARIO_CODE} is missing; run seed_db.py"
            )
        code = scenario_code or f"{source_type}_{uuid4().hex[:20]}"
        scenario = Scenario(
            scenario_code=code,
            title=generated.title,
            background=generated.background,
            target_audience="general",
            scenario_type="occupation_adaptive_decision",
            difficulty_level="medium",
            estimated_minutes=template.estimated_minutes,
            rotation_weight=0,
            is_default=is_default,
            version=SCENARIO_PROMPT_VERSION,
            status="active",
            source_type=source_type,
            base_scenario_id=base_scenario_id,
            occupation_category=occupation_category,
            occupation_key=occupation_key,
            generation_prompt_version=SCENARIO_PROMPT_VERSION,
            generation_model=model_name,
            generation_metadata_json={
                "schema_version": generated.schema_version,
                **(
                    {"fallback_copy_version": FALLBACK_COPY_VERSION}
                    if code == FALLBACK_SCENARIO_CODE
                    else {}
                ),
                "validation_status": "passed",
                "central_decision": generated.central_decision,
                "structure_fingerprint": scenario_structure_fingerprint(generated),
                "generated_scenario": generated.model_dump(),
                "interview_blueprint_version": blueprint.schema_version,
                "interview_presentation_version": blueprint.presentation_version,
                "interview_blueprint_fingerprint": blueprint_fingerprint(blueprint),
                "interview_blueprint": blueprint.model_dump(mode="json"),
            },
            is_immutable=True,
        )
        self.db.add(scenario)
        self.db.flush()

        template_stages = self.db.execute(
            select(ScenarioStage)
            .where(ScenarioStage.scenario_id == template.id)
            .order_by(ScenarioStage.stage_order)
        ).scalars().all()
        generated_by_code = {stage.stage_code: stage for stage in generated.stages}
        for template_stage in template_stages:
            generated_stage = generated_by_code[template_stage.stage_code]
            main_question = STAGE_MAIN_QUESTIONS[template_stage.stage_code]
            stage = ScenarioStage(
                scenario_id=scenario.id,
                stage_code=template_stage.stage_code,
                stage_order=template_stage.stage_order,
                title=template_stage.title,
                stage_goal=STAGE_GOALS[template_stage.stage_code],
                context=generated_stage.context,
                main_question=main_question,
                context_generation_mode="occupation_adaptive",
                context_ai_weight=30,
                context_generation_constraints_json={
                    "cctst_task_contract": STAGE_TASK_CONTRACTS[
                        template_stage.stage_code
                    ],
                    "reference_points": generated_stage.reference_points,
                    "all_required_facts_must_be_visible": True,
                    "professional_knowledge_required": False,
                },
                max_followups=template_stage.max_followups,
                estimated_minutes=template_stage.estimated_minutes,
                exit_criteria_json={
                    "min_user_turns": 1,
                    "completion_mode": "bounded_followup",
                    "expected_evidence": EXIT_CRITERIA[template_stage.stage_code],
                    "evidence_guidance": EVIDENCE_GUIDANCE.get(
                        template_stage.stage_code,
                        {},
                    ),
                    "question_contract": deepcopy(
                        QUESTION_CONTRACTS.get(template_stage.stage_code, {})
                    ),
                },
                status="active",
            )
            self.db.add(stage)
            self.db.flush()
            self._clone_stage_dimensions(template_stage.id, stage.id)
            self._clone_dynamic_infos(
                template_stage.id,
                stage.id,
                generated_stage.dynamic_infos,
            )
            self._clone_rules(template_stage.id, stage.id)
        self.db.flush()
        return scenario
    def _clone_stage_dimensions(self, source_stage_id: int, target_stage_id: int) -> None:
        rows = self.db.execute(
            select(ScenarioStageDimension).where(
                ScenarioStageDimension.stage_id == source_stage_id
            )
        ).scalars().all()
        for row in rows:
            self.db.add(
                ScenarioStageDimension(
                    stage_id=target_stage_id,
                    dimension_id=row.dimension_id,
                    observe_role=row.observe_role,
                    weight=row.weight,
                )
            )

    def _clone_dynamic_infos(
        self,
        source_stage_id: int,
        target_stage_id: int,
        generated_infos: list,
    ) -> None:
        source_rows = self.db.execute(
            select(StageDynamicInfo).where(StageDynamicInfo.stage_id == source_stage_id)
        ).scalars().all()
        source_by_code = {row.info_code: row for row in source_rows}
        for generated in generated_infos:
            source = source_by_code.get(generated.info_code)
            if source is None:
                raise ValueError(
                    f"Template dynamic info missing: {generated.info_code}"
                )
            target = StageDynamicInfo(
                stage_id=target_stage_id,
                info_code=generated.info_code,
                title=generated.title,
                content=generated.content,
                info_type=generated.measurement_function,
                trigger_condition=DYNAMIC_TRIGGER_OVERRIDES.get(
                    generated.info_code, source.trigger_condition
                ),
                priority=source.priority,
                status="active",
            )
            self.db.add(target)
            self.db.flush()
            dimensions = self.db.execute(
                select(StageDynamicInfoDimension).where(
                    StageDynamicInfoDimension.dynamic_info_id == source.id
                )
            ).scalars().all()
            for dimension in dimensions:
                self.db.add(
                    StageDynamicInfoDimension(
                        dynamic_info_id=target.id,
                        dimension_id=dimension.dimension_id,
                        weight=dimension.weight,
                    )
                )

    def _clone_rules(self, source_stage_id: int, target_stage_id: int) -> None:
        rows = self.db.execute(
            select(StageInterventionRule).where(
                StageInterventionRule.stage_id == source_stage_id
            )
        ).scalars().all()
        for source in rows:
            override = RULE_OVERRIDES.get(source.rule_code, {})
            target = StageInterventionRule(
                stage_id=target_stage_id,
                rule_code=source.rule_code,
                rule_type=source.rule_type,
                trigger_condition=override.get(
                    "trigger_condition", source.trigger_condition
                ),
                strategy_direction=override.get(
                    "strategy_direction", source.strategy_direction
                ),
                sample_question=override.get("sample_question", source.sample_question),
                question_generation_mode=source.question_generation_mode,
                question_ai_weight=source.question_ai_weight,
                question_generation_constraints_json=deepcopy(
                    source.question_generation_constraints_json
                ),
                fallback_question=override.get(
                    "fallback_question", source.fallback_question
                ),
                exit_prompt=source.exit_prompt,
                priority=source.priority,
                max_use_count=source.max_use_count,
                status="active",
            )
            self.db.add(target)
            self.db.flush()
            dimensions = self.db.execute(
                select(StageInterventionRuleDimension).where(
                    StageInterventionRuleDimension.rule_id == source.id
                )
            ).scalars().all()
            for dimension in dimensions:
                self.db.add(
                    StageInterventionRuleDimension(
                        rule_id=target.id,
                        dimension_id=dimension.dimension_id,
                        weight=dimension.weight,
                    )
                )
__all__ = [
    "FALLBACK_SCENARIO_CODE",
    "ScenarioMaterializationService",
    "TEMPLATE_SCENARIO_CODE",
]
