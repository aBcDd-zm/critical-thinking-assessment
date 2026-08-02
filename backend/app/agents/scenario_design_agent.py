from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService

SCENARIO_PROMPT_VERSION = "occupation_cctst_v2_4"

STAGE_CODES = [
    "s1_problem_definition",
    "s2_evidence_verification",
    "s3_stakeholder_perspectives",
    "s4_reasoning_decision",
    "s5_dynamic_adjustment",
    "s6_integrated_plan",
]

STAGE_TASK_CONTRACTS = {
    "s1_problem_definition": "请用一句话指出现在最需要判断的核心问题，并列出影响判断的两项限制条件。",
    "s2_evidence_verification": "根据现有材料，目前最多能得出什么结论？还有什么不能确定？如果只能补充一项信息，你会补什么？",
    "s3_stakeholder_perspectives": "请比较至少三方的目标和风险，指出主要冲突，并说明你确定优先级的依据。",
    "s4_reasoning_decision": "请给出你的初步结论、两项主要证据和一个关键假设，并说明哪种情况会削弱这条推理。",
    "s5_dynamic_adjustment": "面对刚出现的新信息，你会保留、修改还是推翻原判断的哪些部分？请指出对应证据。",
    "s6_integrated_plan": "请按“结论—主要依据—仍不确定的信息—行动安排—调整触发条件”给出最终方案。",
}

STAGE_MAIN_QUESTIONS = {
    **STAGE_TASK_CONTRACTS,
    "s1_problem_definition": "看完这些信息，你觉得现在最需要先弄清楚的一件事是什么？",
}

EXPECTED_DYNAMIC_CODES = {
    "s1_problem_definition": [],
    "s2_evidence_verification": ["sample_bias_warning"],
    "s3_stakeholder_perspectives": ["support_capacity_warning"],
    "s4_reasoning_decision": ["competitor_launch_noise"],
    "s5_dynamic_adjustment": [
        "error_rate_increase",
        "key_user_positive_feedback",
    ],
    "s6_integrated_plan": ["limited_engineering_capacity"],
}

EXPECTED_DYNAMIC_FUNCTIONS = {
    "sample_bias_warning": "sample_limitation",
    "support_capacity_warning": "overlooked_stakeholder",
    "competitor_launch_noise": "unverified_risk_signal",
    "error_rate_increase": "counterevidence_risk",
    "key_user_positive_feedback": "counterevidence_benefit",
    "limited_engineering_capacity": "resource_constraint",
}

FORBIDDEN_VISIBLE_TERMS = {
    "问题界定",
    "证据评估",
    "推理论证",
    "多元视角",
    "整合决策",
    "动态调整",
    "评分维度",
    "高分答案",
}


class GeneratedDynamicInfo(BaseModel):
    info_code: str = Field(min_length=2, max_length=64)
    measurement_function: str = Field(min_length=2, max_length=64)
    title: str = Field(min_length=2, max_length=80)
    content: str = Field(min_length=10, max_length=500)


class GeneratedStageStructure(BaseModel):
    core_fact_ids: list[str] = Field(min_length=2, max_length=10)
    condition_relations: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_identifiers(self) -> "GeneratedStageStructure":
        if len(set(self.core_fact_ids)) != len(self.core_fact_ids):
            raise ValueError("core fact IDs must be unique within a stage")
        return self


class GeneratedStage(BaseModel):
    stage_code: str
    context: str = Field(min_length=20, max_length=1000)
    reference_points: list[str] = Field(min_length=2, max_length=6)
    structure: GeneratedStageStructure
    dynamic_infos: list[GeneratedDynamicInfo] = Field(default_factory=list)


class GeneratedScenario(BaseModel):
    schema_version: Literal["occupation_scenario_v2"] = "occupation_scenario_v2"
    professional_knowledge_required: Literal[False]
    contains_real_personal_data: Literal[False]
    title: str = Field(min_length=4, max_length=80)
    background: str = Field(min_length=40, max_length=1200)
    central_decision: str = Field(min_length=10, max_length=240)
    stages: list[GeneratedStage] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def validate_blueprint(self) -> "GeneratedScenario":
        codes = [stage.stage_code for stage in self.stages]
        if codes != STAGE_CODES:
            raise ValueError(f"stage codes must be exactly {STAGE_CODES}")
        for stage in self.stages:
            actual = [item.info_code for item in stage.dynamic_infos]
            expected = EXPECTED_DYNAMIC_CODES[stage.stage_code]
            if actual != expected:
                raise ValueError(
                    f"dynamic info codes for {stage.stage_code} must be {expected}"
                )
            for item in stage.dynamic_infos:
                expected_function = EXPECTED_DYNAMIC_FUNCTIONS[item.info_code]
                if item.measurement_function != expected_function:
                    raise ValueError(
                        f"measurement function for {item.info_code} must be "
                        f"{expected_function}"
                    )
        visible = "\n".join(
            [self.title, self.background, self.central_decision]
            + [stage.context for stage in self.stages]
            + [
                info.content
                for stage in self.stages
                for info in stage.dynamic_infos
            ]
        )
        leaked = sorted(term for term in FORBIDDEN_VISIBLE_TERMS if term in visible)
        if leaked:
            raise ValueError(f"visible content leaks assessment language: {leaked}")
        return self


@dataclass
class ScenarioAgentResult:
    success: bool
    scenario: GeneratedScenario | None
    raw_output: str
    model_name: str | None
    error_code: str | None = None
    error_reason: str | None = None
    payload: dict[str, Any] | None = None


def normalize_occupation_key(category: str, occupation: str) -> str:
    normalized = unicodedata.normalize("NFKC", occupation).strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return f"{category}:{normalized}"


def scenario_structure_fingerprint(scenario: GeneratedScenario) -> str:
    payload = {
        "scenario_numbers": re.findall(
            r"\d+(?:\.\d+)?%?", scenario.background + " " + scenario.central_decision
        ),
        "stage_codes": [stage.stage_code for stage in scenario.stages],
        "dynamic_codes": {
            stage.stage_code: [item.info_code for item in stage.dynamic_infos]
            for stage in scenario.stages
        },
        "dynamic_functions": {
            stage.stage_code: [
                item.measurement_function for item in stage.dynamic_infos
            ]
            for stage in scenario.stages
        },
        "core_fact_ids": {
            stage.stage_code: stage.structure.core_fact_ids
            for stage in scenario.stages
        },
        "condition_relations": {
            stage.stage_code: stage.structure.condition_relations
            for stage in scenario.stages
        },
        "numbers": {
            stage.stage_code: re.findall(
                r"\d+(?:\.\d+)?%?",
                stage.context
                + " "
                + " ".join(info.content for info in stage.dynamic_infos),
            )
            for stage in scenario.stages
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ScenarioDesignAgent:
    def __init__(self, gateway: ModelGatewayService | None = None) -> None:
        self.settings = get_settings()
        self.gateway = gateway or ModelGatewayService(self.settings)

    def generate_base(
        self,
        category: str,
        occupation: str,
        template_content: str | None = None,
    ) -> ScenarioAgentResult:
        if self.settings.MODEL_GATEWAY_MODE.lower() == "mock":
            scenario = build_mock_occupation_scenario(category, occupation)
            return ScenarioAgentResult(
                success=True,
                scenario=scenario,
                raw_output=scenario.model_dump_json(),
                model_name="mock",
            )
        prompt = _base_generation_prompt(category, occupation, template_content)
        return self._call_json(
            "scenario_design",
            prompt,
            temperature=0.35,
            max_tokens=5200,
            allow_invalid_payload=True,
        )

    def review_base(
        self,
        category: str,
        occupation: str,
        draft: GeneratedScenario | dict[str, Any],
        template_content: str | None = None,
    ) -> ScenarioAgentResult:
        if self.settings.MODEL_GATEWAY_MODE.lower() == "mock":
            scenario = (
                draft
                if isinstance(draft, GeneratedScenario)
                else GeneratedScenario.model_validate(draft)
            )
            return ScenarioAgentResult(
                success=True,
                scenario=scenario,
                raw_output=scenario.model_dump_json(),
                model_name="mock",
            )
        prompt = _review_prompt(category, occupation, draft, template_content)
        return self._call_json("scenario_review", prompt, temperature=0.1, max_tokens=5200)

    def adapt_for_profile(
        self,
        base: GeneratedScenario,
        profile: dict[str, Any],
        template_content: str | None = None,
    ) -> ScenarioAgentResult:
        if self.settings.MODEL_GATEWAY_MODE.lower() == "mock":
            return ScenarioAgentResult(
                success=True,
                scenario=base,
                raw_output=base.model_dump_json(),
                model_name="mock",
            )
        prompt = _adaptation_prompt(base, profile, template_content)
        result = self._call_json(
            "scenario_adaptation", prompt, temperature=0.2, max_tokens=5200
        )
        if result.success and result.scenario is not None:
            if scenario_structure_fingerprint(result.scenario) != scenario_structure_fingerprint(
                base
            ):
                return ScenarioAgentResult(
                    success=False,
                    scenario=None,
                    raw_output=result.raw_output,
                    model_name=result.model_name,
                    error_code="STRUCTURE_FINGERPRINT_CHANGED",
                    error_reason="adaptation changed stage, dynamic-info, or numeric structure",
                )
        return result

    def _call_json(
        self,
        agent_name: str,
        user_prompt: str,
        *,
        temperature: float,
        max_tokens: int,
        allow_invalid_payload: bool = False,
    ) -> ScenarioAgentResult:
        request = ModelChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是审辩式思维测评的情景设计器。只输出符合要求的 JSON；"
                        "不得输出 markdown，不得泄露评分维度或标准答案。"
                    ),
                ),
                ChatMessage(role="user", content=user_prompt),
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            thinking_enabled=False,
            reasoning_effort="low",
        )
        response = None
        payload: dict[str, Any] | None = None
        try:
            response = asyncio.run(self.gateway.chat(request))
            payload = _extract_json(response.content)
            scenario = GeneratedScenario.model_validate(payload)
            return ScenarioAgentResult(
                success=True,
                scenario=scenario,
                raw_output=response.content,
                model_name=response.model,
                payload=payload,
            )
        except Exception as exc:  # noqa: BLE001
            if allow_invalid_payload and payload is not None:
                return ScenarioAgentResult(
                    success=True,
                    scenario=None,
                    raw_output=response.content if response is not None else "",
                    model_name=response.model if response is not None else None,
                    error_code="DRAFT_REQUIRES_REVIEW",
                    error_reason=str(exc),
                    payload=payload,
                )
            return ScenarioAgentResult(
                success=False,
                scenario=None,
                raw_output=response.content if response is not None else "",
                model_name=response.model if response is not None else None,
                error_code=f"{agent_name.upper()}_ERROR",
                error_reason=str(exc),
                payload=payload,
            )


def build_mock_occupation_scenario(category: str, occupation: str) -> GeneratedScenario:
    task = f"{occupation}日常工作中的一项协作流程"
    return GeneratedScenario(
        professional_knowledge_required=False,
        contains_real_personal_data=False,
        title=f"{occupation}的协作安排调整",
        background=(
            f"你以{occupation}的身份参与{task}。团队准备在五天后启用一套更快的安排方式，"
            "无需运用专业规则，只需根据随后给出的事实，"
            "判断是否、何时以及如何调整安排。"
        ),
        central_decision="是否在五天后启用新安排，以及需要设置哪些限制和检查条件。",
        stages=[
            GeneratedStage(
                stage_code="s1_problem_definition",
                context=(
                    "现行流程最近处理了60项任务，其中12项出现延迟。新安排希望把交接步骤"
                    "从4步减为2步，但团队每天只能拿出2小时准备，且不能降低基本服务质量。"
                ),
                reference_points=["核心判断", "时间约束", "质量边界"],
                structure=GeneratedStageStructure(
                    core_fact_ids=["s1_delay_count", "s1_step_change", "s1_capacity"],
                    condition_relations=[
                        "step_reduction_may_reduce_delay",
                        "limited_preparation_constrains_rollout",
                        "quality_floor_must_be_preserved",
                    ],
                ),
            ),
            GeneratedStage(
                stage_code="s2_evidence_verification",
                context=(
                    "一名同事询问了20名经常参与该流程的人，其中14人支持新安排；另有记录"
                    "显示最近8次延迟中有5次发生在交接环节。目前不知道不常参与者的看法，"
                    "也没有对照过任务难度。"
                ),
                reference_points=["样本范围", "证据局限", "补充信息"],
                structure=GeneratedStageStructure(
                    core_fact_ids=["s2_support_sample", "s2_delay_records", "s2_unknown_group"],
                    condition_relations=[
                        "familiar_participant_sample_limits_generalization",
                        "task_difficulty_is_uncontrolled_alternative_explanation",
                    ],
                ),
                dynamic_infos=[
                    GeneratedDynamicInfo(
                        info_code="sample_bias_warning",
                        measurement_function="sample_limitation",
                        title="样本范围提示",
                        content="被询问的20人都属于最熟悉现行流程的一组，新参与者没有被覆盖。",
                    )
                ],
            ),
            GeneratedStage(
                stage_code="s3_stakeholder_perspectives",
                context=(
                    "直接使用流程的人希望减少等待，负责交接的同事担心短期工作量增加，"
                    "提供后续支持的人担心问题集中出现。管理者希望五天后看到改善，但没有"
                    "要求必须全面启用。"
                ),
                reference_points=["多方目标", "风险冲突", "优先依据"],
                structure=GeneratedStageStructure(
                    core_fact_ids=["s3_user_goal", "s3_handoff_load", "s3_support_risk"],
                    condition_relations=[
                        "reduced_wait_conflicts_with_transition_load",
                        "support_capacity_constrains_rollout_scope",
                    ],
                ),
                dynamic_infos=[
                    GeneratedDynamicInfo(
                        info_code="support_capacity_warning",
                        measurement_function="overlooked_stakeholder",
                        title="支持能力限制",
                        content="启用当天预计求助量可能达到平时的3倍，但只能安排1人处理。",
                    )
                ],
            ),
            GeneratedStage(
                stage_code="s4_reasoning_decision",
                context=(
                    "团队可以选择全面启用、先在小范围试用、推迟，或提出其他组合安排。"
                    "已知小范围试用可覆盖15项任务，全面启用会覆盖60项任务，准备时间仍为5天。"
                ),
                reference_points=["结论", "两项证据", "关键假设"],
                structure=GeneratedStageStructure(
                    core_fact_ids=["s4_option_set", "s4_pilot_scope", "s4_full_scope"],
                    condition_relations=[
                        "pilot_scope_reduces_exposure",
                        "fixed_preparation_window_constrains_all_options",
                    ],
                ),
                dynamic_infos=[
                    GeneratedDynamicInfo(
                        info_code="competitor_launch_noise",
                        measurement_function="unverified_risk_signal",
                        title="未经核实的消息",
                        content="有人听说另一支团队很快会采用类似安排，但消息来源尚未确认。",
                    )
                ],
            ),
            GeneratedStage(
                stage_code="s5_dynamic_adjustment",
                context="你刚形成初步判断，团队又收到一条可能改变原判断的新信息。",
                reference_points=["原判断", "新证据", "调整幅度"],
                structure=GeneratedStageStructure(
                    core_fact_ids=["s5_prior_judgment", "s5_risk_counterevidence", "s5_benefit_counterevidence"],
                    condition_relations=[
                        "risk_signal_should_weaken_aggressive_rollout",
                        "benefit_signal_should_weaken_total_rejection",
                    ],
                ),
                dynamic_infos=[
                    GeneratedDynamicInfo(
                        info_code="error_rate_increase",
                        measurement_function="counterevidence_risk",
                        title="风险反证",
                        content="最新试用记录显示，关键交接错误从5%升至18%，且集中在最重要的任务。",
                    ),
                    GeneratedDynamicInfo(
                        info_code="key_user_positive_feedback",
                        measurement_function="counterevidence_benefit",
                        title="收益反证",
                        content="最新试用记录显示，等待时间减少40%，关键交接错误仍保持在5%。",
                    ),
                ],
            ),
            GeneratedStage(
                stage_code="s6_integrated_plan",
                context=(
                    "现在需要综合已确认的信息形成最终安排。接下来5天只有2名同事能够参与准备，"
                    "启用后第一天只能安排1人提供支持。"
                ),
                reference_points=["最终结论", "行动步骤", "不确定性", "调整条件"],
                structure=GeneratedStageStructure(
                    core_fact_ids=["s6_preparation_staff", "s6_support_staff", "s6_final_plan"],
                    condition_relations=[
                        "preparation_staff_limits_parallel_work",
                        "support_staff_limits_first_day_scope",
                    ],
                ),
                dynamic_infos=[
                    GeneratedDynamicInfo(
                        info_code="limited_engineering_capacity",
                        measurement_function="resource_constraint",
                        title="资源约束",
                        content="准备期只能保证2人参与，无法同时处理全面调整和大量临时问题。",
                    )
                ],
            ),
        ],
    )


def _base_generation_prompt(
    category: str,
    occupation: str,
    template_content: str | None,
) -> str:
    return f"""
已启用的版本化模板：
{template_content or '使用内置 occupation_cctst_v2_4 模板。'}

为职业大类“{category}”、具体职业“{occupation}”设计一个低风险、低专业门槛、可仅凭题内信息作答的连续情景。
输出必须符合 GeneratedScenario JSON 结构，schema_version 固定为 occupation_scenario_v2。
title、background、central_decision、stages 等字段必须直接位于 JSON 顶层；不要添加 scenario、data、result 等包裹层。
{_schema_contract_text()}
professional_knowledge_required 和 contains_real_personal_data 必须都是 false。
stages 必须严格按以下顺序：{json.dumps(STAGE_CODES, ensure_ascii=False)}。
各阶段 dynamic info code 必须严格为：{json.dumps(EXPECTED_DYNAMIC_CODES, ensure_ascii=False)}。
dynamic info 的 measurement_function 必须严格为：{json.dumps(EXPECTED_DYNAMIC_FUNCTIONS, ensure_ascii=False)}。
每阶段 context 给出自包含的事实；reference_points 给出2至6个仅供系统使用的证据点。
每阶段 context 由短而完整的事实句组成，每句只表达一个主要信息关系，便于后续逐轮释放；完整事实不得删减。
每阶段 structure.core_fact_ids 为稳定事实ID，structure.condition_relations 用稳定英文短码记录条件关系；审查和适配不得随意改动。
使用清楚的数字、来源、样本或限制制造推理任务，但不得要求职业法规、SOP、诊疗、诉讼或投资知识。
避免真实单位、人物、地点和敏感经历。不得在可见文本出现能力维度名、评分规则或答案提示。
S5的两条信息必须分别能够增强风险与增强收益，并可能合理改变不同初始立场。
只输出 JSON。
""".strip()


def _review_prompt(
    category: str,
    occupation: str,
    draft: GeneratedScenario | dict[str, Any],
    template_content: str | None,
) -> str:
    return f"""
已启用的版本化模板：
{template_content or '使用内置 occupation_cctst_v2_4 审查模板。'}

审查并修复下面的职业情景。职业大类：{category}；具体职业：{occupation}。
{_schema_contract_text()}
必须保持六阶段顺序、事实关系、规定 dynamic info code 和 measurement_function；错误字段名必须改成上述正式字段名，不能照抄错误格式。确保所有必要信息题内给出、术语可理解、数字一致、S5包含双向反证。将每阶段 context 修复为短而完整的事实句，每句只表达一个主要信息关系。
删除专业知识依赖、真实个人信息、评分维度名称和答案暗示。输出完整修复后的 GeneratedScenario JSON，不要输出审查说明。
草稿：
{_scenario_payload_json(draft)}
""".strip()


def _adaptation_prompt(
    base: GeneratedScenario,
    profile: dict[str, Any],
    template_content: str | None,
) -> str:
    return f"""
已启用的版本化模板：
{template_content or '使用内置 occupation_cctst_v2_4 适配模板。'}

根据用户画像对职业基础情景做轻量表层适配。只允许调整任务名称、角色称谓和通俗措辞。
{_schema_contract_text()}
禁止改变任何数字、阶段代码、dynamic info code、measurement_function、structure、证据方向、条件关系或推理难度。
不得加入单位名、地点、真实人物或敏感案例。输出完整 GeneratedScenario JSON。
用户画像：{json.dumps(profile, ensure_ascii=False)}
基础情景：{base.model_dump_json()}
""".strip()


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    value = json.loads(stripped.strip())
    if not isinstance(value, dict):
        raise ValueError("scenario output must be a JSON object")
    return normalize_scenario_payload(value)


def normalize_scenario_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize common model-added envelopes and field aliases before validation."""
    required_content_fields = {"title", "background", "central_decision", "stages"}
    normalized = value
    if not required_content_fields.issubset(value):
        for key in ("scenario", "generated_scenario", "result", "data"):
            nested = value.get(key)
            if not isinstance(nested, dict) or not required_content_fields.issubset(
                nested
            ):
                continue
            normalized = dict(nested)
            for metadata_key in (
                "schema_version",
                "professional_knowledge_required",
                "contains_real_personal_data",
            ):
                if metadata_key not in normalized and metadata_key in value:
                    normalized[metadata_key] = value[metadata_key]
            break

    normalized = dict(normalized)
    stages = normalized.get("stages")
    if not isinstance(stages, list):
        return normalized
    normalized_stages: list[Any] = []
    for raw_stage in stages:
        if not isinstance(raw_stage, dict):
            normalized_stages.append(raw_stage)
            continue
        stage = dict(raw_stage)
        if "stage_code" not in stage and isinstance(stage.get("stage_id"), str):
            stage["stage_code"] = stage.pop("stage_id")
        if "dynamic_infos" not in stage and "dynamic_info" in stage:
            alternate = stage.pop("dynamic_info")
            if isinstance(alternate, list):
                stage["dynamic_infos"] = alternate
            elif isinstance(alternate, dict) and isinstance(
                alternate.get("code"), str
            ):
                stage["dynamic_infos"] = [alternate]
            elif EXPECTED_DYNAMIC_CODES.get(stage.get("stage_code"), []) == []:
                stage["dynamic_infos"] = []
        infos = stage.get("dynamic_infos")
        if isinstance(infos, list):
            normalized_infos: list[Any] = []
            for raw_info in infos:
                if not isinstance(raw_info, dict):
                    normalized_infos.append(raw_info)
                    continue
                info = dict(raw_info)
                if "info_code" not in info and isinstance(info.get("code"), str):
                    info["info_code"] = info.pop("code")
                normalized_infos.append(info)
            stage["dynamic_infos"] = normalized_infos
        normalized_stages.append(stage)
    normalized["stages"] = normalized_stages
    return normalized


def _schema_contract_text() -> str:
    return """
严格字段合同：顶层只能直接使用 schema_version、professional_knowledge_required、contains_real_personal_data、title、background、central_decision、stages。
stages 必须是数组；每个阶段对象必须使用 stage_code、context、reference_points、structure、dynamic_infos，禁止使用 stage_id 或 dynamic_info。
structure 必须使用 core_fact_ids 和 condition_relations，二者都是字符串数组。
dynamic_infos 必须是数组；每项必须完整使用 info_code、measurement_function、title、content，禁止用 code 代替 info_code。
没有动态信息的阶段也必须写 dynamic_infos: []。
""".strip()


def _scenario_payload_json(draft: GeneratedScenario | dict[str, Any]) -> str:
    if isinstance(draft, GeneratedScenario):
        return draft.model_dump_json()
    return json.dumps(draft, ensure_ascii=False)


__all__ = [
    "EXPECTED_DYNAMIC_CODES",
    "EXPECTED_DYNAMIC_FUNCTIONS",
    "GeneratedScenario",
    "SCENARIO_PROMPT_VERSION",
    "STAGE_CODES",
    "STAGE_MAIN_QUESTIONS",
    "STAGE_TASK_CONTRACTS",
    "ScenarioAgentResult",
    "ScenarioDesignAgent",
    "build_mock_occupation_scenario",
    "normalize_scenario_payload",
    "normalize_occupation_key",
    "scenario_structure_fingerprint",
]
