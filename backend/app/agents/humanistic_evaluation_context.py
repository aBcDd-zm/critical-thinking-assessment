from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.interview_blueprint import (
    ConversationBudget,
    EventReleaseCondition,
    GeneratedEventCard,
    GeneratedScenarioBlueprint,
    GeneratedStoryNode,
    PresentationUnit,
)
from app.agents.interviewer_output_contract import (
    INTERVIEWER_OUTPUT_CONTRACT_SHA256,
    INTERVIEWER_OUTPUT_CONTRACT_VERSION,
)
from app.agents.progressive_schemas import InterviewPlanOutput
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    ParticipantContext,
    ScenarioContext,
    SessionContext,
    StageContext,
)


CONTEXT_SCHEMA_VERSION = "humanistic_pilot_context_v1"
MANIFEST_SCHEMA_VERSION = "humanistic_context_manifest_v1"
CANONICAL_DIMENSION_KEYS = frozenset(
    {
        "problem_definition",
        "evidence_evaluation",
        "reasoning_argumentation",
        "multiple_perspectives",
        "integrative_decision",
        "dynamic_adjustment",
    }
)
EXPECTED_SPLIT_COUNTS = Counter({"train": 32, "dev": 8, "locked_test": 8})
EXPECTED_CATEGORY_COUNTS = Counter(
    {
        "opening": 4,
        "probe": 12,
        "event": 12,
        "clarify": 6,
        "repair": 6,
        "integrate_close": 8,
    }
)
EXPECTED_DIMENSION_COUNTS = Counter(
    {
        "problem_definition": 5,
        "evidence_evaluation": 13,
        "reasoning_argumentation": 3,
        "multiple_perspectives": 3,
        "integrative_decision": 14,
        "dynamic_adjustment": 10,
    }
)
EXPECTED_LOCKED_CONTEXT_IDS_V1 = frozenset(
    {
        "HIV1-O05",
        "HIV1-P13",
        "HIV1-P14",
        "HIV1-E13",
        "HIV1-E14",
        "HIV1-C07",
        "HIV1-R07",
        "HIV1-I09",
    }
)
EXPECTED_RETIRED_LOCKED_CONTEXT_IDS_V1 = frozenset(
    {
        "HIV1-O04",
        "HIV1-P11",
        "HIV1-P12",
        "HIV1-E11",
        "HIV1-E12",
        "HIV1-C06",
        "HIV1-R06",
        "HIV1-I08",
    }
)
EXPECTED_CONTEXT_ASSET_PATHS = {
    "development_contexts": (
        "backend/tests/fixtures/humanistic_interviewer/"
        "pilot_contexts_development_v1.jsonl"
    ),
    "locked_test_contexts": (
        "backend/tests/fixtures/humanistic_interviewer/"
        "pilot_contexts_locked_v1.jsonl"
    ),
    "review_examples": (
        "backend/tests/fixtures/humanistic_interviewer/"
        "review_examples_v1.jsonl"
    ),
}
REQUIRED_FREEZE_ARTIFACT_PATHS = {
    "humanistic_style_policy_v1": (
        "docs/humanistic_interviewer/humanistic_style_policy_v1.yaml"
    ),
    "humanistic_source_notes_v1": (
        "docs/humanistic_interviewer/humanistic_source_notes_v1.md"
    ),
    "prompt_seed_registry": "backend/seeds/prompts.yaml",
    "interviewer_agent": "backend/app/agents/interviewer_agent.py",
    "interviewer_output_contract_v1": (
        "backend/app/agents/interviewer_output_contract.py"
    ),
    "interview_question_validator": (
        "backend/app/agents/interview_question_validator.py"
    ),
    "humanistic_evaluation_context_adapter": (
        "backend/app/agents/humanistic_evaluation_context.py"
    ),
    "humanistic_release_evaluator": (
        "backend/scripts/evaluate_humanistic_interviewer_v1.py"
    ),
    "humanistic_candidate_generator_v1": (
        "backend/app/agents/humanistic_candidate_generation.py"
    ),
    "humanistic_candidate_generator_cli_v1": (
        "backend/scripts/generate_humanistic_blind_candidates_v1.py"
    ),
    "humanistic_candidate_base_smoke_v1": (
        "backend/scripts/smoke_humanistic_candidate_arms_v1.py"
    ),
    "humanistic_candidate_event_smoke_v1": (
        "backend/scripts/smoke_humanistic_event_candidate_arms_v1.py"
    ),
    "humanistic_candidate_action_matrix_smoke_v1": (
        "backend/scripts/smoke_humanistic_action_matrix_v1.py"
    ),
    "model_gateway_service": "backend/app/services/model_gateway_service.py",
    "generation_runtime_config": "backend/app/core/config.py",
}
EXPECTED_FREEZE_GATE_IDS = frozenset(
    {f"FREEZE-{letter}" for letter in "ABCDEFGH"}
)
GENERATION_CONTRACT_AMENDMENT_ID = "generation_contract_amendment_v1"
PREVIOUS_CONTEXT_MANIFEST_SHA256 = (
    "58f05adbff250ec7688f5309d23460099f26ede614092994730b1261da07f951"
)
EXPECTED_GENERATION_CONTRACT_CHANGE_IDS = frozenset(
    {
        "SCHEMA-A",
        "SCHEMA-B",
        "SCHEMA-C",
        "STRICT-A",
        "STRICT-B",
        "EVID-A",
        "META-A",
        "FLAGS-A",
        "FAIR-A",
        "LIVE-A",
        "ISO-A",
    }
)
EXPECTED_AMENDMENT_GATE_IDS = frozenset(
    {f"AMEND-{letter}" for letter in "ABCDEFGH"}
)
EXPECTED_AMENDMENT_REJECTION_TEST_IDS = frozenset(
    {f"AM-RJ-{index:02d}" for index in range(1, 11)}
)
EXPECTED_AMENDED_ARTIFACT_ACTIONS = {
    "prompt_seed_registry": "updated",
    "interviewer_agent": "updated",
    "interviewer_output_contract_v1": "added",
    "humanistic_evaluation_context_adapter": "updated",
}
PREVIOUS_AMENDED_ARTIFACT_SHA256 = {
    "prompt_seed_registry": (
        "39fd0a9ab1958e17ebb678b2de6ae70e9c857b7b0762692b383e207e47d1e60a"
    ),
    "interviewer_agent": (
        "56080c8a51fbb75017613a6cab6d84ea4e2be64f0552ac5768d78727913c5e59"
    ),
    "humanistic_evaluation_context_adapter": (
        "c99bf1fc5aed077d80e00876ed749a9766f2953c5970b5029d09294bd84793d7"
    ),
}
APPROVED_SMOKE_PREFLIGHT_SHA256 = (
    "eeb7fa1133c1f698637ebe501000ea2ee8e765d44472338c7fed8e102b09a999"
)
APPROVED_SMOKE_AUDIT_SHA256 = (
    "d79aef928a25817c271b9dab5859a56ea65fa790fd80e27b44b53d902e69f8bb"
)
GENERATION_RELIABILITY_AMENDMENT_ID = (
    "generation_reliability_amendment_v1"
)
RELIABILITY_PREVIOUS_MANIFEST_SHA256 = (
    "453e9a47abdedff11e4d6a778f25fc0345727be898265a1fa8c9a2ae2a78fb9d"
)
RELIABILITY_PREVIOUS_PREFLIGHT_SHA256 = (
    "c79b52ae92dd301b6e6afea4b49ea5ac76e9d63e6ac336d2a4feca45bc4b3a45"
)
RELIABILITY_BLOCKED_RUN_ID = "run_6fc581a22201eaddc8bed593f1e0ca68"
RELIABILITY_BLOCKED_MANIFEST_SHA256 = (
    "61c8c145b9178f649b7d2d8b7e0589730c34723da1cfa43fb2015209b6a08735"
)
RELIABILITY_BLOCKED_PROVENANCE_SHA256 = (
    "b573a4d18567a4fd0a7b61190375013145a1ec734da8fcd20328090e6944fc92"
)
RELIABILITY_BLOCKED_FAILURES_SHA256 = (
    "5656947b6681d0f96b89d96ca9b47e57407a757ff5090048f0b7b66af3390f7d"
)
RELIABILITY_EVENT_SMOKE_PREFLIGHT_SHA256 = (
    "b95f05ff40e0f6a0d4ba6e9069f1adcfa6379fbaa09673eebfb11c8896ef2199"
)
RELIABILITY_EVENT_SMOKE_AUDIT_SHA256 = (
    "62700c37ebad5e4fe49a651a7909375b470fd68e4e7ccbdee330ba2503bdec16"
)
EXPECTED_RELIABILITY_ARTIFACT_ACTIONS = {
    "interviewer_agent": "updated",
    "humanistic_evaluation_context_adapter": "updated",
    "humanistic_candidate_generator_v1": "updated",
    "humanistic_candidate_generator_cli_v1": "added",
    "humanistic_candidate_base_smoke_v1": "added",
    "humanistic_candidate_event_smoke_v1": "added",
    "model_gateway_service": "added",
    "generation_runtime_config": "updated",
}
PREVIOUS_RELIABILITY_ARTIFACT_SHA256 = {
    "interviewer_agent": (
        "334a932b4214726720a845b2706ee1a363a0e43a895426ff024a85e74c6cbc58"
    ),
    "humanistic_evaluation_context_adapter": (
        "34db073f6394347e544cd81d90304adb840bb992702e488a8a4ecc14820f5faa"
    ),
    "humanistic_candidate_generator_v1": (
        "47036688f6dd21e637ab7bd2e161242fc6cd591393b04b4996322408da0db164"
    ),
    "generation_runtime_config": (
        "6d84d1775f7f40b2725e972f96a593a66e4b10d3675837722deb42dfd00d223c"
    ),
}
GENERATION_RELIABILITY_AMENDMENT_V2_ID = (
    "generation_reliability_amendment_v2"
)
RELIABILITY_V2_PREVIOUS_MANIFEST_SHA256 = (
    "78eb62393fe2879f36bdcd5a9954c6d6fbe48ba90138fa00faba956ae2de3927"
)
RELIABILITY_V2_PREVIOUS_PREFLIGHT_SHA256 = (
    "89dcf2a87d7ab79de99bb391addaea1ac27a3d087472cf95f31956f0aeef2501"
)
RELIABILITY_V2_BLOCKED_RUN_ID = "run_da8660481bfb07df6795c9a93b8252b7"
RELIABILITY_V2_BLOCKED_MANIFEST_SHA256 = (
    "6fbd3c95e5d075787c430e0d6e649214ac620d57bab08ca5d2c3848b39ca900a"
)
RELIABILITY_V2_BLOCKED_PROVENANCE_SHA256 = (
    "3c4ed26156320cb2865c8accd16695a8f073afc0e7b04a75ffdd2ee01bc78671"
)
RELIABILITY_V2_BLOCKED_FAILURES_SHA256 = (
    "83f3010de4226580aadd94db3e336183cc986a1e308b3962e172a648b149e1af"
)
RELIABILITY_V2_MATRIX_PREFLIGHT_SHA256 = (
    "1ae2a9f9f1bd53514af44cfb1a796f8d7aab26c022825994549c6554deb0771a"
)
RELIABILITY_V2_MATRIX_AUDIT_SHA256 = (
    "998025208332d896b6eee760a2866862e06606c662c18a784f1cd390c66bef53"
)
EXPECTED_RELIABILITY_V2_ACTIONS = frozenset(
    {"PROBE", "CHALLENGE", "RELEASE_EVENT", "CLARIFY", "INTEGRATE", "CONCLUDE"}
)
EXPECTED_RELIABILITY_V2_ARTIFACT_ACTIONS = {
    "interviewer_agent": "updated",
    "humanistic_evaluation_context_adapter": "updated",
    "humanistic_candidate_generator_v1": "updated",
    "humanistic_candidate_action_matrix_smoke_v1": "added",
}
PREVIOUS_RELIABILITY_V2_ARTIFACT_SHA256 = {
    "interviewer_agent": (
        "7154ae85682cf287485beb330fbe2b316a5066589e8d328413ee147a5e5273f9"
    ),
    "humanistic_evaluation_context_adapter": (
        "a00ff8fc5d0d755dcfce85f56fbce0b72fe0ffde6b77887077d0c04dc3e292b5"
    ),
    "humanistic_candidate_generator_v1": (
        "10dce2a4043c1f2aa630b301358bc364c2a0ad0d08108e1b7651c276cbb9dbe8"
    ),
}
GENERATION_RELIABILITY_AMENDMENT_V3_ID = (
    "generation_reliability_amendment_v3"
)
RELIABILITY_V3_PREVIOUS_MANIFEST_SHA256 = (
    "707aa76469c65b509fbd62266ac3df0a70c89f98ce5befc35832cb463923fd60"
)
RELIABILITY_V3_PREVIOUS_PREFLIGHT_SHA256 = (
    "6ee1c04f378f9f4dbc05bdaab7f4a2c756fedc1b87256158d0fd5f84b2ed4cf5"
)
RELIABILITY_V3_BLOCKED_RUN_ID = "run_ff45cbfc3d9e82b2d054db5cdceb2238"
RELIABILITY_V3_BLOCKED_MANIFEST_SHA256 = (
    "c173adcd2088cc551e0973ce41d9f30fa8152b68951cc33694adcb19d49e864f"
)
RELIABILITY_V3_BLOCKED_PROVENANCE_SHA256 = (
    "69084738cf3ce5c7ff6cfe294ca6f7c724ef1c79d3be35d0f5f0248c30f9dc4c"
)
RELIABILITY_V3_BLOCKED_FAILURES_SHA256 = (
    "8265af045e29245d28a44c29959be5ce93142f7531c5d502573c21172d58d7b4"
)
RELIABILITY_V3_CORRECTIVE_INTERVIEWER_SHA256 = (
    "0357feccbf5b0a7b78bd833c6479751a53917d2e055925f2ff76990e6a10febc"
)
RELIABILITY_V3_CORRECTIVE_PREFLIGHT_SHA256 = (
    "6024653741c5f0ee7616d44b026b2aba9020e6099ada3e8a6685e6d7f32181ad"
)
RELIABILITY_V3_CORRECTIVE_AUDIT_SHA256 = (
    "34cf250d9fc1da19de338deec39e7cf35e27c167f126119e2066f19f252334f2"
)
RELIABILITY_V3_MATRIX_PREFLIGHT_SHA256 = (
    "20b6561f6770522172ca2637f9568da882ec500a951627941103fb1ac777c645"
)
RELIABILITY_V3_MATRIX_AUDIT_SHA256 = (
    "230b6fab54d56527d4e43f154cb72f3346b30bd3dfaefe7abced4526466ec665"
)
EXPECTED_RELIABILITY_V3_GATE_IDS = frozenset(
    {f"V3-{letter}" for letter in "ABCDEFGHI"}
)
EXPECTED_RELIABILITY_V3_REJECTION_TEST_IDS = frozenset(
    {f"V3-RJ-{index:02d}" for index in range(1, 13)}
)
EXPECTED_RELIABILITY_V3_ARTIFACT_ACTIONS = {
    "interviewer_agent": "updated",
    "humanistic_evaluation_context_adapter": "updated",
    "humanistic_candidate_generator_v1": "updated",
    "humanistic_candidate_base_smoke_v1": "updated",
}
PREVIOUS_RELIABILITY_V3_ARTIFACT_SHA256 = {
    "interviewer_agent": (
        "3767d559c3c824d24d544ff7da7d18a7e18a6a0121ddd2f2e6108d8bf4ea6253"
    ),
    "humanistic_evaluation_context_adapter": (
        "d2ed8fbf2df2acd32ee20b2aa85d6f1b7d686db36219243d4d1048a75306dd25"
    ),
    "humanistic_candidate_generator_v1": (
        "91ded88c547ac981ab172f65744c27a568bf814c415fca6cc6c4615cda16dba7"
    ),
    "humanistic_candidate_base_smoke_v1": (
        "6531a195c5af88fcdc112289c9e55a23a56dfa48786bdb14cfd2afbda51253cc"
    ),
}
GENERATION_RELIABILITY_AMENDMENT_V4_ID = (
    "generation_reliability_amendment_v4"
)
RELIABILITY_V4_PREVIOUS_MANIFEST_SHA256 = (
    "269e4e94e1a2305b3730df9964bf9aeb2b23f45554172e8df0c97e843d66cecb"
)
RELIABILITY_V4_PREVIOUS_PREFLIGHT_SHA256 = (
    "7c64a2995564f10ae32204a09756bf62094671a64ae6dc93562453c349723079"
)
RELIABILITY_V4_BLOCKED_RUN_ID = "run_4b4be488a9479c338f80cfd7cb68e6c1"
RELIABILITY_V4_BLOCKED_MANIFEST_SHA256 = (
    "9e8eb18a732f8fe24607c88b6f29ad3c7813ae291852f24ae8905bdb166400f1"
)
RELIABILITY_V4_BLOCKED_PROVENANCE_SHA256 = (
    "828aa8a3ecc00bc9bb44eb4cf50e926946b38fb260719f6430b4b83088129d6e"
)
RELIABILITY_V4_BLOCKED_FAILURES_SHA256 = (
    "33700030ce6861dbe76c6e76f81e8618181b56d88ecb34ecc2ae93b459e71b88"
)
RELIABILITY_V4_MATRIX_PREFLIGHT_SHA256 = (
    "a85aa7424a3663b0042d13f3d3ce1ade82819165e47d352268f405e517280cf0"
)
RELIABILITY_V4_EVENT_PREFLIGHT_SHA256 = (
    "14529a5975c656e8243acae49c1817f7f371c95d528018f798a399c5e587ada4"
)
RELIABILITY_V4_MATRIX_AUDIT_SHA256 = (
    "9eebe19e5fdd8708029c2cdad48f38062327a7596510d0316aac024d86bf3461"
)
RELIABILITY_V4_EVENT_AUDIT_SHA256 = frozenset(
    {
        "941ec52a96c5b12f34253c9bdb8b7de1081b1c6e359cb81c6000aad2a5c93e9c",
        "ac1276b2cd0fb918c9f517681c4f3860777cdadef89268ba35aa4802578c71d2",
        "5934685abb075dfc44aeef978340b8fad4e11d6fc1c3f1b469f7a5245494f2c6",
    }
)
EXPECTED_RELIABILITY_V4_GATE_IDS = frozenset(
    {f"V4-{letter}" for letter in "ABCDEFGH"}
)
EXPECTED_RELIABILITY_V4_REJECTION_TEST_IDS = frozenset(
    {f"V4-RJ-{index:02d}" for index in range(1, 13)}
)
EXPECTED_RELIABILITY_V4_ARTIFACT_ACTIONS = {
    "interviewer_agent": "updated",
    "humanistic_evaluation_context_adapter": "updated",
}
PREVIOUS_RELIABILITY_V4_ARTIFACT_SHA256 = {
    "interviewer_agent": (
        "fac875c70348a9d2f7a5280c7d12bc031d62693f61d0d9abf4d1a3ec480e6127"
    ),
    "humanistic_evaluation_context_adapter": (
        "0c5f5be12aa5a7dba1217d69d07dadef22753bdcca4e0d3a633d6676f367a55c"
    ),
}
GENERATION_RELIABILITY_AMENDMENT_V5_ID = (
    "generation_reliability_amendment_v5"
)
RELIABILITY_V5_PREVIOUS_MANIFEST_SHA256 = (
    "30335ecc61687d97854605d719bcae7f84db5b10172add81f1ffe11ff1fbcd68"
)
RELIABILITY_V5_PREVIOUS_PREFLIGHT_SHA256 = (
    "8ae77ce691b4e935ef6392023c320effc87af8b469ca765bfb81e0d1a8f2976d"
)
RELIABILITY_V5_BLOCKED_RUN_ID = "run_ac943a9038dd6887628d2c9c77c6a3ee"
RELIABILITY_V5_BLOCKED_MANIFEST_SHA256 = (
    "7a3c69c5aeb5da8648099efb41ccc1c180b46ee20e05c0039518665760c182e4"
)
RELIABILITY_V5_BLOCKED_PROVENANCE_SHA256 = (
    "ee585ec9193b8ecfb8e2cd40cc419691c7c18617be401890e2114b2ab1931e75"
)
RELIABILITY_V5_BLOCKED_CASE_KEY_SHA256 = (
    "aedba75b3a6b137c0708f63131b8df003c7bf023403603cfa9f6564976ebf67f"
)
RELIABILITY_V5_BLOCKED_FAILURES_SHA256 = (
    "d638ff51287bed0012ad4ce59595e5ef1d390f153a49290e95a0a5bb7a8d85ab"
)
EXPECTED_RELIABILITY_V5_GATE_IDS = frozenset(
    {f"V5-{letter}" for letter in "ABCDEFGH"}
)
EXPECTED_RELIABILITY_V5_REJECTION_TEST_IDS = frozenset(
    {f"V5-RJ-{index:02d}" for index in range(1, 13)}
)
EXPECTED_RELIABILITY_V5_ARTIFACT_ACTIONS = {
    "humanistic_candidate_generator_v1": "updated",
    "humanistic_release_evaluator": "updated",
    "humanistic_evaluation_context_adapter": "updated",
}
PREVIOUS_RELIABILITY_V5_ARTIFACT_SHA256 = {
    "humanistic_candidate_generator_v1": (
        "fbcfc01e806948cabe1a77b3c20e9bfe8f1ff5cfcb0f268f9a42b8fc8dcbe133"
    ),
    "humanistic_release_evaluator": (
        "4ac7709e7f84501f7938c1a5ce6785b1c141c6cce7e92aeb6dfa1a6db1e76af3"
    ),
    "humanistic_evaluation_context_adapter": (
        "9652607a5ac7870c767dd763573764e31024d0f9747f94417bbb1973f58adc71"
    ),
}
EXPECTED_REJECTION_TEST_IDS = frozenset(
    {f"RJ-{index:02d}" for index in range(1, 13)}
)

_STAGE_BY_DIMENSION = {
    "problem_definition": ("s1_problem_definition", 1, "问题界定"),
    "evidence_evaluation": ("s2_evidence_verification", 2, "证据核实"),
    "multiple_perspectives": ("s3_stakeholder_perspectives", 3, "多元视角"),
    "reasoning_argumentation": ("s4_reasoning_decision", 4, "推理论证"),
    "dynamic_adjustment": ("s5_dynamic_adjustment", 5, "动态调整"),
    "integrative_decision": ("s6_integrated_plan", 6, "整合决策"),
}
_STAGE_BY_CATEGORY = {
    "opening": ("s1_problem_definition", 1, "问题界定"),
    "event": ("s5_dynamic_adjustment", 5, "动态调整"),
    "integrate_close": ("s6_integrated_plan", 6, "整合决策"),
}
_EVENT_LAYOUT = (
    ("s1_problem_definition", "opening_context", "opening"),
    ("s2_evidence_verification", "evidence_uncertainty", "exploration"),
    ("s3_stakeholder_perspectives", "stakeholder_conflict", "conflict"),
    ("s4_reasoning_decision", "decision_pressure", "decision"),
    ("s5_dynamic_adjustment", "counter_evidence", "update"),
    ("s6_integrated_plan", "integration", "closure"),
)
_EVENT_PLACEHOLDERS = {
    "opening_context": "这是离线评测使用的固定开场背景。",
    "evidence_uncertainty": "当前信息仍有一项需要核实。",
    "stakeholder_conflict": "相关方对当前安排存在不同考虑。",
    "decision_pressure": "当前需要形成一项初步安排。",
    "counter_evidence": "刚出现了一项可能影响原判断的新信息。",
    "integration": "当前需要整合前述信息形成最终安排。",
}
_EVENT_OPPORTUNITIES = {
    "opening_context": ["problem_definition"],
    "evidence_uncertainty": ["evidence_evaluation"],
    "stakeholder_conflict": ["multiple_perspectives"],
    "decision_pressure": ["reasoning_argumentation", "integrative_decision"],
    "counter_evidence": ["dynamic_adjustment"],
    "integration": ["integrative_decision"],
}


def _stage_contract(
    category: str,
    target_dimension: str,
) -> tuple[str, int, str]:
    return _STAGE_BY_CATEGORY.get(
        category,
        _STAGE_BY_DIMENSION[target_dimension],
    )


def _validate_repo_relative_path(value: str) -> str:
    pure_path = PurePosixPath(value)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise ValueError("manifest paths must be repository-relative without '..'")
    if value != pure_path.as_posix() or value in {"", "."}:
        raise ValueError("manifest paths must use normalized POSIX form")
    return value


def _validate_iso_date(value: str, label: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO calendar date") from exc
    return value


class EvaluationContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrozenEventUnit(EvaluationContractModel):
    event_code: Literal["counter_evidence"]
    node_code: Literal["s5_dynamic_adjustment"]
    unit_code: str = Field(min_length=2, max_length=96)
    text: str = Field(min_length=2, max_length=70)
    counterevidence_direction: Literal["risk", "benefit", "neutral"]


class ReflectionReview(EvaluationContractModel):
    turn_ids: list[int] = Field(min_length=1, max_length=4)
    supported_summary: str = Field(min_length=1, max_length=500)
    unsupported_inferences: list[str] = Field(min_length=1, max_length=12)


class ManifestAsset(EvaluationContractModel):
    repo_relative_path: str = Field(min_length=3)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_path(self) -> "ManifestAsset":
        _validate_repo_relative_path(self.repo_relative_path)
        return self


class FreezeArtifact(EvaluationContractModel):
    artifact_id: str = Field(min_length=2)
    repo_relative_path: str = Field(min_length=3)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_path(self) -> "FreezeArtifact":
        _validate_repo_relative_path(self.repo_relative_path)
        return self


class GenerationContractArtifactChange(EvaluationContractModel):
    artifact_id: str = Field(min_length=2)
    action: Literal["updated", "added"]
    previous_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    current_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationContractAmendment(EvaluationContractModel):
    amendment_id: Literal["generation_contract_amendment_v1"]
    amended_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    previous_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_change_ids: list[str] = Field(min_length=11, max_length=11)
    approved_amendment_gate_ids: list[str] = Field(min_length=8, max_length=8)
    approved_rejection_test_ids: list[str] = Field(min_length=10, max_length=10)
    contexts_unchanged: Literal[True]
    development_contexts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locked_test_contexts_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_examples_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_examples_handling: Literal["carried_forward_without_file_read"]
    artifact_changes: list[GenerationContractArtifactChange] = Field(
        min_length=4,
        max_length=4,
    )
    output_contract_version: str = Field(min_length=1)
    output_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    smoke_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    smoke_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    smoke_status: Literal["pass"]
    candidate_generation_started: Literal[False]

    @model_validator(mode="after")
    def validate_amendment_approval(self) -> "GenerationContractAmendment":
        _validate_iso_date(self.amended_at, "generation_contract_amendment.amended_at")
        if self.previous_manifest_sha256 != PREVIOUS_CONTEXT_MANIFEST_SHA256:
            raise ValueError("generation amendment previous manifest SHA-256 drift")
        if set(self.approved_change_ids) != EXPECTED_GENERATION_CONTRACT_CHANGE_IDS:
            raise ValueError("generation amendment approved change IDs are incomplete")
        if set(self.approved_amendment_gate_ids) != EXPECTED_AMENDMENT_GATE_IDS:
            raise ValueError("generation amendment must approve AMEND-A through AMEND-H")
        if (
            set(self.approved_rejection_test_ids)
            != EXPECTED_AMENDMENT_REJECTION_TEST_IDS
        ):
            raise ValueError("generation amendment rejection-test IDs are incomplete")
        if self.output_contract_version != INTERVIEWER_OUTPUT_CONTRACT_VERSION:
            raise ValueError("generation amendment output contract version drift")
        if self.output_contract_sha256 != INTERVIEWER_OUTPUT_CONTRACT_SHA256:
            raise ValueError("generation amendment output contract SHA-256 drift")
        if self.smoke_preflight_sha256 != APPROVED_SMOKE_PREFLIGHT_SHA256:
            raise ValueError("generation amendment smoke preflight SHA-256 drift")
        if self.smoke_audit_sha256 != APPROVED_SMOKE_AUDIT_SHA256:
            raise ValueError("generation amendment smoke audit SHA-256 drift")

        changes = {item.artifact_id: item for item in self.artifact_changes}
        if len(changes) != len(self.artifact_changes):
            raise ValueError("generation amendment artifact change IDs must be unique")
        if {
            artifact_id: item.action for artifact_id, item in changes.items()
        } != EXPECTED_AMENDED_ARTIFACT_ACTIONS:
            raise ValueError("generation amendment artifact changes are not minimal")
        for artifact_id, item in changes.items():
            expected_previous = PREVIOUS_AMENDED_ARTIFACT_SHA256.get(artifact_id)
            if item.previous_sha256 != expected_previous:
                raise ValueError(
                    f"generation amendment previous artifact SHA-256 drift: {artifact_id}"
                )
            if item.action == "updated" and item.current_sha256 == item.previous_sha256:
                raise ValueError(
                    f"generation amendment updated artifact did not change: {artifact_id}"
                )
        return self


class GenerationReliabilityAmendment(EvaluationContractModel):
    amendment_id: Literal["generation_reliability_amendment_v1"]
    amended_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    authorization_scope: Literal[
        "explicit_informed_generation_reliability_amendment"
    ]
    previous_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    triggering_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_failures_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stop_reason: Literal["paired_rounds_exhausted"]
    stop_context_id: Literal["HIV1-E04"]
    attempted_context_count: Literal[1]
    remote_call_count: Literal[6]
    candidate_count: Literal[0]
    model_call_exception_count: Literal[4]
    validator_rejected_count: Literal[2]
    contexts_unchanged: Literal[True]
    review_examples_handling: Literal["carried_forward_without_file_read"]
    style_policy_unchanged: Literal[True]
    validator_unchanged: Literal[True]
    scoring_unchanged: Literal[True]
    output_contract_unchanged: Literal[True]
    previous_candidate_timeout_seconds: Literal[3]
    candidate_timeout_seconds: Literal[15]
    shared_event_structure_constraint: Literal[True]
    applies_identically_to_arms: list[str] = Field(min_length=2, max_length=2)
    artifact_changes: list[GenerationContractArtifactChange] = Field(
        min_length=8,
        max_length=8,
    )
    event_smoke_context_id: Literal["HIV1-S98"]
    event_smoke_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_smoke_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_smoke_status: Literal["pass"]
    generation_restart_authorized: Literal[True]
    formal_candidate_generation_started_after_amendment: Literal[False]

    @model_validator(mode="after")
    def validate_reliability_amendment(
        self,
    ) -> "GenerationReliabilityAmendment":
        _validate_iso_date(
            self.amended_at,
            "generation_reliability_amendment.amended_at",
        )
        exact_locks = {
            "previous manifest": (
                self.previous_manifest_sha256,
                RELIABILITY_PREVIOUS_MANIFEST_SHA256,
            ),
            "previous preflight": (
                self.previous_preflight_sha256,
                RELIABILITY_PREVIOUS_PREFLIGHT_SHA256,
            ),
            "blocked run": (
                self.triggering_run_id,
                RELIABILITY_BLOCKED_RUN_ID,
            ),
            "blocked manifest": (
                self.triggering_manifest_sha256,
                RELIABILITY_BLOCKED_MANIFEST_SHA256,
            ),
            "blocked provenance": (
                self.triggering_provenance_sha256,
                RELIABILITY_BLOCKED_PROVENANCE_SHA256,
            ),
            "blocked failures": (
                self.triggering_failures_sha256,
                RELIABILITY_BLOCKED_FAILURES_SHA256,
            ),
            "event smoke preflight": (
                self.event_smoke_preflight_sha256,
                RELIABILITY_EVENT_SMOKE_PREFLIGHT_SHA256,
            ),
            "event smoke audit": (
                self.event_smoke_audit_sha256,
                RELIABILITY_EVENT_SMOKE_AUDIT_SHA256,
            ),
        }
        for label, (actual, expected) in exact_locks.items():
            if actual != expected:
                raise ValueError(f"generation reliability {label} drift")
        if set(self.applies_identically_to_arms) != {"baseline", "humanistic"}:
            raise ValueError(
                "generation reliability amendment must apply identically to both arms"
            )

        changes = {item.artifact_id: item for item in self.artifact_changes}
        if len(changes) != len(self.artifact_changes):
            raise ValueError(
                "generation reliability artifact change IDs must be unique"
            )
        if {
            artifact_id: item.action for artifact_id, item in changes.items()
        } != EXPECTED_RELIABILITY_ARTIFACT_ACTIONS:
            raise ValueError(
                "generation reliability artifact changes are not minimal"
            )
        for artifact_id, item in changes.items():
            expected_previous = PREVIOUS_RELIABILITY_ARTIFACT_SHA256.get(
                artifact_id
            )
            if item.previous_sha256 != expected_previous:
                raise ValueError(
                    "generation reliability previous artifact SHA-256 drift: "
                    f"{artifact_id}"
                )
            if item.action == "updated" and item.current_sha256 == expected_previous:
                raise ValueError(
                    "generation reliability updated artifact did not change: "
                    f"{artifact_id}"
                )
        return self


class GenerationReliabilityAmendmentV2(EvaluationContractModel):
    amendment_id: Literal["generation_reliability_amendment_v2"]
    amended_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    authorization_scope: Literal[
        "explicit_informed_generation_reliability_amendment_v2"
    ]
    previous_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    triggering_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_failures_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stop_reason: Literal["paired_rounds_exhausted"]
    stop_context_id: Literal["HIV1-I04"]
    attempted_context_count: Literal[1]
    remote_call_count: Literal[6]
    candidate_count: Literal[0]
    validator_rejected_count: Literal[6]
    sole_validation_code: Literal["question_count"]
    contexts_unchanged: Literal[True]
    review_examples_handling: Literal["carried_forward_without_file_read"]
    style_policy_unchanged: Literal[True]
    validator_unchanged: Literal[True]
    scoring_unchanged: Literal[True]
    output_contract_unchanged: Literal[True]
    candidate_timeout_seconds: Literal[15]
    approved_plan_actions: list[str] = Field(min_length=6, max_length=6)
    shared_plan_action_structure_mapping: Literal[True]
    retry_selection_policy: Literal[
        "first_valid_per_arm_across_paired_rounds"
    ]
    paired_arm_calls_remain_symmetric: Literal[True]
    max_paired_rounds: Literal[3]
    model_repair_enabled: Literal[False]
    model_failure_substitutes_fallback: Literal[False]
    artifact_changes: list[GenerationContractArtifactChange] = Field(
        min_length=4,
        max_length=4,
    )
    action_matrix_context_count: Literal[6]
    action_matrix_remote_call_count: Literal[12]
    action_matrix_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_matrix_audit_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_matrix_status: Literal["pass"]
    generation_restart_authorized: Literal[True]
    formal_candidate_generation_started_after_amendment: Literal[False]

    @model_validator(mode="after")
    def validate_reliability_amendment_v2(
        self,
    ) -> "GenerationReliabilityAmendmentV2":
        _validate_iso_date(
            self.amended_at,
            "generation_reliability_amendment_v2.amended_at",
        )
        exact_locks = {
            "previous manifest": (
                self.previous_manifest_sha256,
                RELIABILITY_V2_PREVIOUS_MANIFEST_SHA256,
            ),
            "previous preflight": (
                self.previous_preflight_sha256,
                RELIABILITY_V2_PREVIOUS_PREFLIGHT_SHA256,
            ),
            "blocked run": (
                self.triggering_run_id,
                RELIABILITY_V2_BLOCKED_RUN_ID,
            ),
            "blocked manifest": (
                self.triggering_manifest_sha256,
                RELIABILITY_V2_BLOCKED_MANIFEST_SHA256,
            ),
            "blocked provenance": (
                self.triggering_provenance_sha256,
                RELIABILITY_V2_BLOCKED_PROVENANCE_SHA256,
            ),
            "blocked failures": (
                self.triggering_failures_sha256,
                RELIABILITY_V2_BLOCKED_FAILURES_SHA256,
            ),
            "matrix preflight": (
                self.action_matrix_preflight_sha256,
                RELIABILITY_V2_MATRIX_PREFLIGHT_SHA256,
            ),
            "matrix audit": (
                self.action_matrix_audit_sha256,
                RELIABILITY_V2_MATRIX_AUDIT_SHA256,
            ),
        }
        for label, (actual, expected) in exact_locks.items():
            if actual != expected:
                raise ValueError(f"generation reliability v2 {label} drift")
        if set(self.approved_plan_actions) != EXPECTED_RELIABILITY_V2_ACTIONS:
            raise ValueError(
                "generation reliability v2 must cover all six plan actions"
            )

        changes = {item.artifact_id: item for item in self.artifact_changes}
        if len(changes) != len(self.artifact_changes):
            raise ValueError(
                "generation reliability v2 artifact change IDs must be unique"
            )
        if {
            artifact_id: item.action for artifact_id, item in changes.items()
        } != EXPECTED_RELIABILITY_V2_ARTIFACT_ACTIONS:
            raise ValueError(
                "generation reliability v2 artifact changes are not minimal"
            )
        for artifact_id, item in changes.items():
            expected_previous = PREVIOUS_RELIABILITY_V2_ARTIFACT_SHA256.get(
                artifact_id
            )
            if item.previous_sha256 != expected_previous:
                raise ValueError(
                    "generation reliability v2 previous artifact SHA-256 drift: "
                    f"{artifact_id}"
                )
            if item.action == "updated" and item.current_sha256 == expected_previous:
                raise ValueError(
                    "generation reliability v2 updated artifact did not change: "
                    f"{artifact_id}"
                )
        return self


class GenerationReliabilityAmendmentV3(EvaluationContractModel):
    amendment_id: Literal["generation_reliability_amendment_v3"]
    status: Literal["provisional_before_smoke", "frozen_after_smoke"]
    amended_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    authorization_scope: Literal[
        "explicit_generation_reliability_amendment_v3"
    ]
    approved_gate_ids: list[str] = Field(min_length=9, max_length=9)
    approved_rejection_test_ids: list[str] = Field(
        min_length=12,
        max_length=12,
    )
    previous_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    triggering_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_failures_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_stop_context_id: Literal["HIV1-I04"]
    triggering_attempted_context_count: Literal[32]
    triggering_candidate_count: Literal[0]
    corrective_interviewer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corrective_action_matrix_preflight_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    corrective_action_matrix_audit_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    corrective_stop_context_id: Literal["HIV1-S91"]
    corrective_remote_call_count: Literal[2]
    corrective_baseline_validation_code: Literal["internal_terms"]
    contexts_unchanged: Literal[True]
    review_examples_handling: Literal["carried_forward_without_file_read"]
    style_policy_unchanged: Literal[True]
    validator_unchanged: Literal[True]
    scoring_unchanged: Literal[True]
    output_contract_unchanged: Literal[True]
    prompt_registry_unchanged: Literal[True]
    candidate_generation_mode: Literal["frozen_candidate_v1"]
    internal_terms_source: Literal[
        "InterviewQuestionValidator.INTERNAL_TERMS"
    ]
    shared_constraint_identical_for_model_arms: Literal[True]
    quality_flags_non_authoritative: Literal[True]
    quality_flag_mismatches_audit_only: Literal[True]
    online_prompt_path_unchanged: Literal[True]
    retry_selection_policy: Literal[
        "first_valid_per_arm_across_paired_rounds"
    ]
    paired_arm_calls_remain_symmetric: Literal[True]
    max_paired_rounds: Literal[3]
    model_repair_enabled: Literal[False]
    model_failure_substitutes_fallback: Literal[False]
    artifact_changes: list[GenerationContractArtifactChange] = Field(
        min_length=4,
        max_length=4,
    )
    action_matrix_context_count: Literal[6]
    action_matrix_max_remote_call_count: Literal[12]
    action_matrix_remote_call_count: Literal[0, 12]
    action_matrix_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_matrix_audit_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    action_matrix_status: Literal["pending", "pass"]
    generation_restart_authorized: bool
    formal_candidate_generation_started_after_amendment: Literal[False]

    @model_validator(mode="after")
    def validate_reliability_amendment_v3(
        self,
    ) -> "GenerationReliabilityAmendmentV3":
        _validate_iso_date(
            self.amended_at,
            "generation_reliability_amendment_v3.amended_at",
        )
        exact_locks = {
            "previous manifest": (
                self.previous_manifest_sha256,
                RELIABILITY_V3_PREVIOUS_MANIFEST_SHA256,
            ),
            "previous preflight": (
                self.previous_preflight_sha256,
                RELIABILITY_V3_PREVIOUS_PREFLIGHT_SHA256,
            ),
            "blocked run": (
                self.triggering_run_id,
                RELIABILITY_V3_BLOCKED_RUN_ID,
            ),
            "blocked manifest": (
                self.triggering_manifest_sha256,
                RELIABILITY_V3_BLOCKED_MANIFEST_SHA256,
            ),
            "blocked provenance": (
                self.triggering_provenance_sha256,
                RELIABILITY_V3_BLOCKED_PROVENANCE_SHA256,
            ),
            "blocked failures": (
                self.triggering_failures_sha256,
                RELIABILITY_V3_BLOCKED_FAILURES_SHA256,
            ),
            "corrective interviewer": (
                self.corrective_interviewer_sha256,
                RELIABILITY_V3_CORRECTIVE_INTERVIEWER_SHA256,
            ),
            "corrective preflight": (
                self.corrective_action_matrix_preflight_sha256,
                RELIABILITY_V3_CORRECTIVE_PREFLIGHT_SHA256,
            ),
            "corrective audit": (
                self.corrective_action_matrix_audit_sha256,
                RELIABILITY_V3_CORRECTIVE_AUDIT_SHA256,
            ),
            "matrix preflight": (
                self.action_matrix_preflight_sha256,
                RELIABILITY_V3_MATRIX_PREFLIGHT_SHA256,
            ),
        }
        for label, (actual, expected) in exact_locks.items():
            if actual != expected:
                raise ValueError(f"generation reliability v3 {label} drift")
        if set(self.approved_gate_ids) != EXPECTED_RELIABILITY_V3_GATE_IDS:
            raise ValueError("generation reliability v3 requires V3-A through V3-I")
        if (
            set(self.approved_rejection_test_ids)
            != EXPECTED_RELIABILITY_V3_REJECTION_TEST_IDS
        ):
            raise ValueError(
                "generation reliability v3 rejection-test IDs are incomplete"
            )
        changes = {item.artifact_id: item for item in self.artifact_changes}
        if len(changes) != len(self.artifact_changes):
            raise ValueError(
                "generation reliability v3 artifact change IDs must be unique"
            )
        if {
            artifact_id: item.action for artifact_id, item in changes.items()
        } != EXPECTED_RELIABILITY_V3_ARTIFACT_ACTIONS:
            raise ValueError(
                "generation reliability v3 artifact changes are not minimal"
            )
        for artifact_id, item in changes.items():
            expected_previous = PREVIOUS_RELIABILITY_V3_ARTIFACT_SHA256[
                artifact_id
            ]
            if item.previous_sha256 != expected_previous:
                raise ValueError(
                    "generation reliability v3 previous artifact SHA-256 drift: "
                    f"{artifact_id}"
                )
            if item.current_sha256 == expected_previous:
                raise ValueError(
                    "generation reliability v3 updated artifact did not change: "
                    f"{artifact_id}"
                )

        if self.status == "provisional_before_smoke":
            if (
                self.action_matrix_status != "pending"
                or self.action_matrix_remote_call_count != 0
                or self.action_matrix_audit_sha256 is not None
                or self.generation_restart_authorized
            ):
                raise ValueError(
                    "generation reliability v3 provisional state cannot claim pass"
                )
        elif (
            self.action_matrix_status != "pass"
            or self.action_matrix_remote_call_count != 12
            or self.action_matrix_audit_sha256 is None
            or self.action_matrix_audit_sha256
            != RELIABILITY_V3_MATRIX_AUDIT_SHA256
            or not self.generation_restart_authorized
        ):
            raise ValueError(
                "generation reliability v3 frozen state requires complete smoke evidence"
            )
        return self


class GenerationReliabilityAmendmentV4(EvaluationContractModel):
    amendment_id: Literal["generation_reliability_amendment_v4"]
    status: Literal["provisional_before_smoke", "frozen_after_smoke"]
    amended_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    authorization_scope: Literal[
        "explicit_generation_reliability_amendment_v4"
    ]
    approved_gate_ids: list[str] = Field(min_length=8, max_length=8)
    approved_rejection_test_ids: list[str] = Field(
        min_length=12,
        max_length=12,
    )
    previous_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    triggering_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_failures_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_stop_context_id: Literal["HIV1-E03"]
    triggering_attempted_context_count: Literal[4]
    triggering_remote_model_call_count: Literal[14]
    triggering_candidate_count: Literal[0]
    triggering_too_many_sentences_count: Literal[3]
    triggering_collision_count: Literal[1]
    contexts_unchanged: Literal[True]
    review_examples_handling: Literal["carried_forward_without_file_read"]
    style_policy_unchanged: Literal[True]
    validator_unchanged: Literal[True]
    scoring_unchanged: Literal[True]
    output_contract_unchanged: Literal[True]
    prompt_registry_unchanged: Literal[True]
    online_prompt_path_unchanged: Literal[True]
    candidate_generation_mode: Literal["frozen_candidate_v1"]
    applies_only_to_release_event_candidate_and_smoke: Literal[True]
    shared_constraint_identical_for_model_arms: Literal[True]
    reflection_separator: Literal["semicolon_or_colon_or_comma"]
    verified_quotes_excluded_from_terminal_count: Literal[True]
    assistant_authored_question_mark_count: Literal[1]
    assistant_authored_other_terminal_max: Literal[1]
    exact_event_unit_text_required: Literal[True]
    model_output_normalization_enabled: Literal[False]
    collision_policy_unchanged: Literal[True]
    retry_selection_policy: Literal[
        "first_valid_per_arm_across_paired_rounds"
    ]
    paired_arm_calls_remain_symmetric: Literal[True]
    max_paired_rounds: Literal[3]
    model_repair_enabled: Literal[False]
    model_failure_substitutes_fallback: Literal[False]
    artifact_changes: list[GenerationContractArtifactChange] = Field(
        min_length=2,
        max_length=2,
    )
    action_matrix_context_count: Literal[6]
    action_matrix_max_remote_call_count: Literal[12]
    action_matrix_remote_call_count: Literal[0, 12]
    action_matrix_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_matrix_audit_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    action_matrix_status: Literal["pending", "pass"]
    event_smoke_context_id: Literal["HIV1-S98"]
    event_smoke_repetitions: Literal[3]
    event_smoke_max_remote_call_count: Literal[6]
    event_smoke_remote_call_count: Literal[0, 6]
    event_smoke_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_smoke_audit_sha256: list[str] = Field(max_length=3)
    event_smoke_status: Literal["pending", "pass"]
    generation_restart_authorized: bool
    formal_candidate_generation_started_after_amendment: Literal[False]

    @model_validator(mode="after")
    def validate_reliability_amendment_v4(
        self,
    ) -> "GenerationReliabilityAmendmentV4":
        _validate_iso_date(
            self.amended_at,
            "generation_reliability_amendment_v4.amended_at",
        )
        exact_locks = {
            "previous manifest": (
                self.previous_manifest_sha256,
                RELIABILITY_V4_PREVIOUS_MANIFEST_SHA256,
            ),
            "previous preflight": (
                self.previous_preflight_sha256,
                RELIABILITY_V4_PREVIOUS_PREFLIGHT_SHA256,
            ),
            "blocked run": (
                self.triggering_run_id,
                RELIABILITY_V4_BLOCKED_RUN_ID,
            ),
            "blocked manifest": (
                self.triggering_manifest_sha256,
                RELIABILITY_V4_BLOCKED_MANIFEST_SHA256,
            ),
            "blocked provenance": (
                self.triggering_provenance_sha256,
                RELIABILITY_V4_BLOCKED_PROVENANCE_SHA256,
            ),
            "blocked failures": (
                self.triggering_failures_sha256,
                RELIABILITY_V4_BLOCKED_FAILURES_SHA256,
            ),
            "action-matrix preflight": (
                self.action_matrix_preflight_sha256,
                RELIABILITY_V4_MATRIX_PREFLIGHT_SHA256,
            ),
            "event preflight": (
                self.event_smoke_preflight_sha256,
                RELIABILITY_V4_EVENT_PREFLIGHT_SHA256,
            ),
        }
        for label, (actual, expected) in exact_locks.items():
            if actual != expected:
                raise ValueError(f"generation reliability v4 {label} drift")
        if set(self.approved_gate_ids) != EXPECTED_RELIABILITY_V4_GATE_IDS:
            raise ValueError("generation reliability v4 requires V4-A through V4-H")
        if (
            set(self.approved_rejection_test_ids)
            != EXPECTED_RELIABILITY_V4_REJECTION_TEST_IDS
        ):
            raise ValueError(
                "generation reliability v4 rejection-test IDs are incomplete"
            )
        changes = {item.artifact_id: item for item in self.artifact_changes}
        if len(changes) != len(self.artifact_changes):
            raise ValueError(
                "generation reliability v4 artifact change IDs must be unique"
            )
        if {
            artifact_id: item.action for artifact_id, item in changes.items()
        } != EXPECTED_RELIABILITY_V4_ARTIFACT_ACTIONS:
            raise ValueError(
                "generation reliability v4 artifact changes are not minimal"
            )
        for artifact_id, item in changes.items():
            expected_previous = PREVIOUS_RELIABILITY_V4_ARTIFACT_SHA256[
                artifact_id
            ]
            if item.previous_sha256 != expected_previous:
                raise ValueError(
                    "generation reliability v4 previous artifact SHA-256 drift: "
                    f"{artifact_id}"
                )
            if item.current_sha256 == expected_previous:
                raise ValueError(
                    "generation reliability v4 updated artifact did not change: "
                    f"{artifact_id}"
                )

        if self.status == "provisional_before_smoke":
            if (
                self.action_matrix_status != "pending"
                or self.action_matrix_remote_call_count != 0
                or self.action_matrix_audit_sha256 is not None
                or self.event_smoke_status != "pending"
                or self.event_smoke_remote_call_count != 0
                or self.event_smoke_audit_sha256
                or self.generation_restart_authorized
            ):
                raise ValueError(
                    "generation reliability v4 provisional state cannot claim pass"
                )
        elif (
            self.action_matrix_status != "pass"
            or self.action_matrix_remote_call_count != 12
            or self.action_matrix_audit_sha256 is None
            or self.action_matrix_audit_sha256
            != RELIABILITY_V4_MATRIX_AUDIT_SHA256
            or self.event_smoke_status != "pass"
            or self.event_smoke_remote_call_count != 6
            or set(self.event_smoke_audit_sha256)
            != RELIABILITY_V4_EVENT_AUDIT_SHA256
            or not self.generation_restart_authorized
        ):
            raise ValueError(
                "generation reliability v4 frozen state requires complete smoke evidence"
            )
        return self


class GenerationReliabilityAmendmentV5(EvaluationContractModel):
    amendment_id: Literal["generation_reliability_amendment_v5"]
    status: Literal[
        "provisional_after_zero_call_tests",
        "frozen_after_zero_call_gate",
    ]
    amended_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    authorization_scope: Literal[
        "explicit_generation_reliability_amendment_v5"
    ]
    approved_gate_ids: list[str] = Field(min_length=8, max_length=8)
    approved_rejection_test_ids: list[str] = Field(min_length=12, max_length=12)
    previous_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    triggering_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_provenance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_case_key_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_failures_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    triggering_stop_context_id: Literal["HIV1-E09"]
    triggering_attempted_context_count: Literal[8]
    triggering_remote_model_call_count: Literal[20]
    triggering_candidate_count: Literal[0]
    triggering_collision_count: Literal[3]
    contexts_unchanged: Literal[True]
    review_examples_handling: Literal["carried_forward_without_file_read"]
    prompt_registry_unchanged: Literal[True]
    style_policy_unchanged: Literal[True]
    interviewer_agent_unchanged: Literal[True]
    validator_unchanged: Literal[True]
    output_contract_unchanged: Literal[True]
    model_configuration_unchanged: Literal[True]
    rating_fields_and_thresholds_unchanged: Literal[True]
    exact_tie_type: Literal["model_pair_exact"]
    exact_visible_message_required: Literal[True]
    both_model_candidates_must_be_valid: Literal[True]
    fallback_must_remain_normalized_distinct: Literal[True]
    normalized_only_collision_remains_rejected: Literal[True]
    fallback_or_three_way_collision_remains_rejected: Literal[True]
    fallback_rendered_once_per_context: Literal[True]
    collision_audit_scope: Literal["pair_level_not_arm_attributed"]
    opaque_candidate_ids_preserved: Literal[True]
    logical_candidate_count: Literal[144]
    exact_tie_ratings_must_match: Literal[True]
    exact_tie_humanistic_preference_weight: Literal[0.5]
    artifact_changes: list[GenerationContractArtifactChange] = Field(
        min_length=3,
        max_length=3,
    )
    zero_call_remote_model_call_count: Literal[0]
    zero_call_regression_status: Literal["pass"]
    formal_preflight_status: Literal["blocked_pending_v5_freeze", "ready"]
    generation_restart_authorized: bool
    formal_candidate_generation_started_after_amendment: Literal[False]
    final_generation_run_limit: Literal[1]
    automatic_v6_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_reliability_amendment_v5(
        self,
    ) -> "GenerationReliabilityAmendmentV5":
        _validate_iso_date(
            self.amended_at,
            "generation_reliability_amendment_v5.amended_at",
        )
        exact_locks = {
            "previous manifest": (
                self.previous_manifest_sha256,
                RELIABILITY_V5_PREVIOUS_MANIFEST_SHA256,
            ),
            "previous preflight": (
                self.previous_preflight_sha256,
                RELIABILITY_V5_PREVIOUS_PREFLIGHT_SHA256,
            ),
            "blocked run": (
                self.triggering_run_id,
                RELIABILITY_V5_BLOCKED_RUN_ID,
            ),
            "blocked manifest": (
                self.triggering_manifest_sha256,
                RELIABILITY_V5_BLOCKED_MANIFEST_SHA256,
            ),
            "blocked provenance": (
                self.triggering_provenance_sha256,
                RELIABILITY_V5_BLOCKED_PROVENANCE_SHA256,
            ),
            "blocked case key": (
                self.triggering_case_key_sha256,
                RELIABILITY_V5_BLOCKED_CASE_KEY_SHA256,
            ),
            "blocked failures": (
                self.triggering_failures_sha256,
                RELIABILITY_V5_BLOCKED_FAILURES_SHA256,
            ),
        }
        for label, (actual, expected) in exact_locks.items():
            if actual != expected:
                raise ValueError(f"generation reliability v5 {label} drift")
        if set(self.approved_gate_ids) != EXPECTED_RELIABILITY_V5_GATE_IDS:
            raise ValueError("generation reliability v5 requires V5-A through V5-H")
        if (
            set(self.approved_rejection_test_ids)
            != EXPECTED_RELIABILITY_V5_REJECTION_TEST_IDS
        ):
            raise ValueError(
                "generation reliability v5 rejection-test IDs are incomplete"
            )
        changes = {item.artifact_id: item for item in self.artifact_changes}
        if len(changes) != len(self.artifact_changes):
            raise ValueError(
                "generation reliability v5 artifact change IDs must be unique"
            )
        if {
            artifact_id: item.action for artifact_id, item in changes.items()
        } != EXPECTED_RELIABILITY_V5_ARTIFACT_ACTIONS:
            raise ValueError(
                "generation reliability v5 artifact changes are not minimal"
            )
        for artifact_id, item in changes.items():
            expected_previous = PREVIOUS_RELIABILITY_V5_ARTIFACT_SHA256[
                artifact_id
            ]
            if item.previous_sha256 != expected_previous:
                raise ValueError(
                    "generation reliability v5 previous artifact SHA-256 drift: "
                    f"{artifact_id}"
                )
            if item.current_sha256 == expected_previous:
                raise ValueError(
                    "generation reliability v5 updated artifact did not change: "
                    f"{artifact_id}"
                )
        if self.status == "provisional_after_zero_call_tests":
            if (
                self.formal_preflight_status != "blocked_pending_v5_freeze"
                or self.generation_restart_authorized
            ):
                raise ValueError(
                    "generation reliability v5 provisional state cannot authorize restart"
                )
        elif (
            self.formal_preflight_status != "ready"
            or not self.generation_restart_authorized
        ):
            raise ValueError(
                "generation reliability v5 frozen state requires ready preflight"
            )
        return self


class ContextFreezeRecord(EvaluationContractModel):
    frozen_at: str = Field(min_length=10, max_length=10)
    approved_by_role: Literal["member_a_psy"]
    approved_gate_ids: list[str] = Field(min_length=8, max_length=8)
    approved_rejection_test_ids: list[str] = Field(min_length=12, max_length=12)
    candidate_generation_started: Literal[False]

    @model_validator(mode="after")
    def validate_approval_record(self) -> "ContextFreezeRecord":
        _validate_iso_date(self.frozen_at, "freeze_record.frozen_at")
        if set(self.approved_gate_ids) != EXPECTED_FREEZE_GATE_IDS:
            raise ValueError("freeze record must approve FREEZE-A through FREEZE-H")
        if set(self.approved_rejection_test_ids) != EXPECTED_REJECTION_TEST_IDS:
            raise ValueError("freeze record must approve RJ-01 through RJ-12")
        return self


class HumanisticContextManifest(EvaluationContractModel):
    schema_version: Literal["humanistic_context_manifest_v1"]
    status: Literal["provisional_synthetic", "frozen_v1"]
    created_at: str = Field(min_length=10, max_length=10)
    development_contexts: ManifestAsset
    locked_test_contexts: ManifestAsset
    review_examples: ManifestAsset
    freeze_artifacts: list[FreezeArtifact] = Field(min_length=1)
    retired_locked_context_ids: list[str] = Field(min_length=1)
    new_locked_context_ids: list[str] = Field(min_length=1)
    isolation_rules: list[str] = Field(min_length=8)
    candidate_generator_status: Literal[
        "pending_before_generation",
        "blocked_before_v4_smoke",
        "blocked_before_v5_freeze",
    ]
    freeze_record: ContextFreezeRecord | None = None
    generation_contract_amendment: GenerationContractAmendment | None = None
    generation_reliability_amendment: (
        GenerationReliabilityAmendment | None
    ) = None
    generation_reliability_amendment_v2: (
        GenerationReliabilityAmendmentV2 | None
    ) = None
    generation_reliability_amendment_v3: (
        GenerationReliabilityAmendmentV3 | None
    ) = None
    generation_reliability_amendment_v4: (
        GenerationReliabilityAmendmentV4 | None
    ) = None
    generation_reliability_amendment_v5: (
        GenerationReliabilityAmendmentV5 | None
    ) = None

    @model_validator(mode="after")
    def validate_isolation_record(self) -> "HumanisticContextManifest":
        _validate_iso_date(self.created_at, "created_at")
        expected_rules = {f"ISO-{letter}" for letter in "ABCDEFGH"}
        if set(self.isolation_rules) != expected_rules:
            raise ValueError("manifest must record ISO-A through ISO-H")
        if set(self.new_locked_context_ids) != EXPECTED_LOCKED_CONTEXT_IDS_V1:
            raise ValueError("manifest locked context IDs must match approved v1 set")
        if (
            set(self.retired_locked_context_ids)
            != EXPECTED_RETIRED_LOCKED_CONTEXT_IDS_V1
        ):
            raise ValueError("manifest retired locked IDs must match approved v1 set")

        context_paths = {
            "development_contexts": self.development_contexts.repo_relative_path,
            "locked_test_contexts": self.locked_test_contexts.repo_relative_path,
            "review_examples": self.review_examples.repo_relative_path,
        }
        if context_paths != EXPECTED_CONTEXT_ASSET_PATHS:
            raise ValueError("manifest context assets must use canonical v1 paths")

        artifact_paths = {
            item.artifact_id: item.repo_relative_path
            for item in self.freeze_artifacts
        }
        if len(artifact_paths) != len(self.freeze_artifacts):
            raise ValueError("freeze artifact IDs must be unique")
        if artifact_paths != REQUIRED_FREEZE_ARTIFACT_PATHS:
            raise ValueError("manifest must contain the exact required freeze artifacts")
        all_paths = [*context_paths.values(), *artifact_paths.values()]
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("manifest asset paths must be unique")

        if self.status == "provisional_synthetic" and self.freeze_record is not None:
            raise ValueError("provisional manifest must not contain a freeze record")
        if (
            self.status == "provisional_synthetic"
            and self.generation_contract_amendment is not None
        ):
            raise ValueError(
                "provisional manifest must not contain a generation amendment"
            )
        if (
            self.status == "provisional_synthetic"
            and self.generation_reliability_amendment is not None
        ):
            raise ValueError(
                "provisional manifest must not contain a reliability amendment"
            )
        if (
            self.status == "provisional_synthetic"
            and self.generation_reliability_amendment_v2 is not None
        ):
            raise ValueError(
                "provisional manifest must not contain a reliability v2 amendment"
            )
        if (
            self.status == "provisional_synthetic"
            and self.generation_reliability_amendment_v3 is not None
        ):
            raise ValueError(
                "provisional manifest must not contain a reliability v3 amendment"
            )
        if (
            self.status == "provisional_synthetic"
            and self.generation_reliability_amendment_v4 is not None
        ):
            raise ValueError(
                "provisional manifest must not contain a reliability v4 amendment"
            )
        if (
            self.status == "provisional_synthetic"
            and self.generation_reliability_amendment_v5 is not None
        ):
            raise ValueError(
                "provisional manifest must not contain a reliability v5 amendment"
            )
        if self.status == "frozen_v1" and self.freeze_record is None:
            raise ValueError("frozen manifest requires a freeze record")
        if self.status == "frozen_v1" and self.generation_contract_amendment is None:
            raise ValueError("frozen manifest requires a generation amendment")
        if (
            self.status == "frozen_v1"
            and self.generation_reliability_amendment is None
        ):
            raise ValueError(
                "frozen manifest requires a generation reliability amendment"
            )
        if (
            self.status == "frozen_v1"
            and self.generation_reliability_amendment_v2 is None
        ):
            raise ValueError(
                "frozen manifest requires a generation reliability v2 amendment"
            )

        amendment = self.generation_contract_amendment
        reliability = self.generation_reliability_amendment
        reliability_v2 = self.generation_reliability_amendment_v2
        reliability_v3 = self.generation_reliability_amendment_v3
        reliability_v4 = self.generation_reliability_amendment_v4
        reliability_v5 = self.generation_reliability_amendment_v5
        if amendment is not None:
            if (
                amendment.development_contexts_sha256
                != self.development_contexts.sha256
                or amendment.locked_test_contexts_sha256
                != self.locked_test_contexts.sha256
            ):
                raise ValueError("generation amendment context SHA-256 drift")
            if amendment.review_examples_sha256 != self.review_examples.sha256:
                raise ValueError("generation amendment review-example SHA-256 drift")
            artifacts = {
                item.artifact_id: item for item in self.freeze_artifacts
            }
            reliability_changes = (
                {
                    item.artifact_id: item
                    for item in reliability.artifact_changes
                }
                if reliability is not None
                else {}
            )
            for change in amendment.artifact_changes:
                later_change = reliability_changes.get(change.artifact_id)
                if later_change is not None:
                    linked = change.current_sha256 == later_change.previous_sha256
                else:
                    linked = (
                        change.artifact_id in artifacts
                        and change.current_sha256
                        == artifacts[change.artifact_id].sha256
                    )
                if not linked:
                    raise ValueError(
                        "generation amendment artifact history is not linked"
                    )
            if (
                self.freeze_record is None
                or self.freeze_record.candidate_generation_started
                or amendment.candidate_generation_started
            ):
                raise ValueError(
                    "generation amendment requires candidate generation not started"
                )
        if reliability is not None:
            if (
                not reliability.contexts_unchanged
                or reliability.review_examples_handling
                != "carried_forward_without_file_read"
            ):
                raise ValueError(
                    "generation reliability amendment changed frozen data scope"
                )
            artifacts = {
                item.artifact_id: item for item in self.freeze_artifacts
            }
            reliability_v2_changes = (
                {
                    item.artifact_id: item
                    for item in reliability_v2.artifact_changes
                }
                if reliability_v2 is not None
                else {}
            )
            reliability_v3_changes = (
                {
                    item.artifact_id: item
                    for item in reliability_v3.artifact_changes
                }
                if reliability_v3 is not None
                else {}
            )
            for change in reliability.artifact_changes:
                later_change = reliability_v2_changes.get(
                    change.artifact_id
                ) or reliability_v3_changes.get(change.artifact_id)
                if later_change is not None:
                    linked = change.current_sha256 == later_change.previous_sha256
                else:
                    linked = (
                        change.artifact_id in artifacts
                        and change.current_sha256
                        == artifacts[change.artifact_id].sha256
                    )
                if not linked:
                    raise ValueError(
                        "generation reliability artifact history is not linked"
                    )
        if reliability_v2 is not None:
            if (
                not reliability_v2.contexts_unchanged
                or reliability_v2.review_examples_handling
                != "carried_forward_without_file_read"
            ):
                raise ValueError(
                    "generation reliability v2 changed frozen data scope"
                )
            artifacts = {
                item.artifact_id: item for item in self.freeze_artifacts
            }
            reliability_v3_changes = (
                {
                    item.artifact_id: item
                    for item in reliability_v3.artifact_changes
                }
                if reliability_v3 is not None
                else {}
            )
            for change in reliability_v2.artifact_changes:
                later_change = reliability_v3_changes.get(change.artifact_id)
                if later_change is not None:
                    linked = change.current_sha256 == later_change.previous_sha256
                else:
                    linked = (
                        change.artifact_id in artifacts
                        and change.current_sha256
                        == artifacts[change.artifact_id].sha256
                    )
                if not linked:
                    raise ValueError(
                        "generation reliability v2 current artifact SHA-256 drift"
                    )
        if reliability_v3 is not None:
            if (
                not reliability_v3.contexts_unchanged
                or reliability_v3.review_examples_handling
                != "carried_forward_without_file_read"
            ):
                raise ValueError(
                    "generation reliability v3 changed frozen data scope"
                )
            artifacts = {
                item.artifact_id: item for item in self.freeze_artifacts
            }
            reliability_v4_changes = (
                {
                    item.artifact_id: item
                    for item in reliability_v4.artifact_changes
                }
                if reliability_v4 is not None
                else {}
            )
            reliability_v5_changes = (
                {
                    item.artifact_id: item
                    for item in reliability_v5.artifact_changes
                }
                if reliability_v5 is not None
                else {}
            )
            for change in reliability_v3.artifact_changes:
                later_change = reliability_v4_changes.get(
                    change.artifact_id
                ) or reliability_v5_changes.get(change.artifact_id)
                if later_change is not None:
                    linked = change.current_sha256 == later_change.previous_sha256
                else:
                    linked = (
                        change.artifact_id in artifacts
                        and change.current_sha256
                        == artifacts[change.artifact_id].sha256
                    )
                if not linked:
                    raise ValueError(
                        "generation reliability v3 current artifact SHA-256 drift"
                    )
        if reliability_v4 is not None:
            if reliability_v3 is None:
                raise ValueError(
                    "generation reliability v4 requires the frozen v3 amendment"
                )
            if (
                not reliability_v4.contexts_unchanged
                or reliability_v4.review_examples_handling
                != "carried_forward_without_file_read"
            ):
                raise ValueError(
                    "generation reliability v4 changed frozen data scope"
                )
            artifacts = {
                item.artifact_id: item for item in self.freeze_artifacts
            }
            reliability_v5_changes = (
                {
                    item.artifact_id: item
                    for item in reliability_v5.artifact_changes
                }
                if reliability_v5 is not None
                else {}
            )
            for change in reliability_v4.artifact_changes:
                later_change = reliability_v5_changes.get(change.artifact_id)
                if later_change is not None:
                    linked = change.current_sha256 == later_change.previous_sha256
                else:
                    linked = (
                        change.artifact_id in artifacts
                        and change.current_sha256
                        == artifacts[change.artifact_id].sha256
                    )
                if not linked:
                    raise ValueError(
                        "generation reliability v4 current artifact SHA-256 drift"
                    )
            if reliability_v4.status == "provisional_before_smoke":
                if self.candidate_generator_status != "blocked_before_v4_smoke":
                    raise ValueError(
                        "generation reliability v4 provisional manifest must block generation"
                    )
            elif (
                reliability_v5 is None
                and self.candidate_generator_status != "pending_before_generation"
            ):
                raise ValueError(
                    "generation reliability v4 frozen manifest must enable preflight"
                )
        elif self.candidate_generator_status != "pending_before_generation":
            raise ValueError("v4 generation block requires a v4 amendment")
        if reliability_v5 is not None:
            if reliability_v4 is None or reliability_v4.status != "frozen_after_smoke":
                raise ValueError(
                    "generation reliability v5 requires the frozen v4 amendment"
                )
            if (
                not reliability_v5.contexts_unchanged
                or reliability_v5.review_examples_handling
                != "carried_forward_without_file_read"
            ):
                raise ValueError(
                    "generation reliability v5 changed frozen data scope"
                )
            artifacts = {
                item.artifact_id: item for item in self.freeze_artifacts
            }
            for change in reliability_v5.artifact_changes:
                if (
                    change.artifact_id not in artifacts
                    or change.current_sha256
                    != artifacts[change.artifact_id].sha256
                ):
                    raise ValueError(
                        "generation reliability v5 current artifact SHA-256 drift"
                    )
            if reliability_v5.status == "provisional_after_zero_call_tests":
                if self.candidate_generator_status != "blocked_before_v5_freeze":
                    raise ValueError(
                        "generation reliability v5 provisional manifest must block generation"
                    )
            elif self.candidate_generator_status != "pending_before_generation":
                raise ValueError(
                    "generation reliability v5 frozen manifest must enable preflight"
                )
        return self


class HumanisticPilotContext(EvaluationContractModel):
    schema_version: Literal["humanistic_pilot_context_v1"]
    context_id: str = Field(pattern=r"^HIV1-[A-Z][0-9]{2}$")
    split: Literal["train", "dev", "locked_test"]
    category: Literal[
        "opening", "probe", "event", "clarify", "repair", "integrate_close"
    ]
    scenario_id: str = Field(min_length=3, max_length=96)
    status: Literal["provisional_synthetic", "frozen_v1"]
    privacy: Literal["synthetic_no_personal_data"]
    visible_history: list[DialogueTurnContext] = Field(min_length=1, max_length=4)
    latest_user_turn_id: int = Field(ge=1)
    frozen_plan: InterviewPlanOutput
    plan_protected_fields: list[str] = Field(min_length=5)
    event_unit: FrozenEventUnit | None = None
    allowed_facts: list[str] = Field(min_length=1, max_length=12)
    reflection_review: ReflectionReview
    formal_answer: bool = True

    @model_validator(mode="after")
    def validate_runtime_contract(self) -> "HumanisticPilotContext":
        turn_ids = [item.turn_id for item in self.visible_history]
        if any(turn_id is None for turn_id in turn_ids):
            raise ValueError("visible_history turn_id must be an integer")
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError("visible_history turn_id must be unique")
        if turn_ids != sorted(turn_ids):
            raise ValueError("visible_history turn_id must be ordered")

        latest = next(
            (
                item
                for item in self.visible_history
                if item.turn_id == self.latest_user_turn_id
            ),
            None,
        )
        if latest is None or latest.speaker != "user":
            raise ValueError("latest_user_turn_id must select a visible user turn")
        if self.visible_history[-1].turn_id != self.latest_user_turn_id:
            raise ValueError("latest user turn must be the final visible turn")

        user_turn_ids = {
            item.turn_id for item in self.visible_history if item.speaker == "user"
        }
        reflection_ids = set(self.frozen_plan.reflection_basis_turn_ids)
        if not reflection_ids or not reflection_ids.issubset(user_turn_ids):
            raise ValueError("reflection basis must reference visible user turns")
        if set(self.reflection_review.turn_ids) != reflection_ids:
            raise ValueError("reflection review and frozen plan turn IDs must match")

        if self.frozen_plan.target_dimension not in CANONICAL_DIMENSION_KEYS:
            raise ValueError("target_dimension must use the frozen six-dimension keys")
        expected_stage = _stage_contract(
            self.category,
            self.frozen_plan.target_dimension,
        )[0]
        if latest.stage_code != expected_stage:
            raise ValueError("latest user turn stage must match target_dimension")

        expected_actions = {
            "opening": {"PROBE"},
            "probe": {"PROBE", "CHALLENGE"},
            "event": {"RELEASE_EVENT"},
            "clarify": {"CLARIFY"},
            "repair": {"CLARIFY"},
            "integrate_close": {"INTEGRATE", "CONCLUDE"},
        }[self.category]
        if self.frozen_plan.action not in expected_actions:
            raise ValueError("category and frozen plan action do not match")

        protected = set(self.plan_protected_fields)
        required_protected = {
            "response_intent",
            "action",
            "target_dimension",
            "delivery_mode",
            "question_intent",
        }
        if not required_protected.issubset(protected):
            raise ValueError("plan_protected_fields misses a renderer contract field")

        if self.frozen_plan.action == "RELEASE_EVENT":
            if self.event_unit is None:
                raise ValueError("RELEASE_EVENT requires event_unit")
            if (
                self.frozen_plan.release_event_code != self.event_unit.event_code
                or self.frozen_plan.release_unit_code != self.event_unit.unit_code
            ):
                raise ValueError("frozen plan and event unit selection must match")
            if self.event_unit.text not in self.allowed_facts:
                raise ValueError("event unit text must be an exact allowed fact")
            if not {"release_event_code", "release_unit_code"}.issubset(protected):
                raise ValueError("event selection must be protected")
        elif self.event_unit is not None:
            raise ValueError("only RELEASE_EVENT may include event_unit")

        if self.category == "repair":
            latest_index = self.visible_history.index(latest)
            if not any(
                item.speaker == "ai"
                for item in self.visible_history[:latest_index]
            ):
                raise ValueError("repair context requires the preceding AI turn")
            if self.formal_answer:
                raise ValueError("repair context must remain non-scoring")
        elif not self.formal_answer:
            raise ValueError("only repair contexts may be non-scoring")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_repo_file(repo_root: Path, relative_path: str) -> Path:
    root = repo_root.resolve(strict=True)
    _validate_repo_relative_path(relative_path)
    lexical_path = root.joinpath(*PurePosixPath(relative_path).parts)
    cursor = root
    for part in PurePosixPath(relative_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"manifest assets must not be symlinks: {relative_path}")
    try:
        resolved = lexical_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"manifest asset does not exist: {relative_path}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"manifest asset escapes repository root: {relative_path}") from exc
    if not resolved.is_file():
        raise ValueError(f"manifest asset is not a regular file: {relative_path}")
    return resolved


def _verify_asset(repo_root: Path, asset: ManifestAsset | FreezeArtifact) -> Path:
    path = _resolve_repo_file(repo_root, asset.repo_relative_path)
    if not path.is_file():
        raise ValueError(f"manifest asset does not exist: {asset.repo_relative_path}")
    actual = _sha256(path)
    if actual != asset.sha256:
        raise ValueError(
            f"manifest hash mismatch for {asset.repo_relative_path}: {actual}"
        )
    return path


def _validate_manifest_location(path: Path, repo_root: Path) -> Path:
    root = repo_root.resolve(strict=True)
    if path.is_symlink():
        raise ValueError("context manifest must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"context manifest does not exist: {path}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("context manifest must remain inside repository root") from exc
    if not resolved.is_file():
        raise ValueError("context manifest must be a regular file")
    return resolved


def _load_context_file(path: Path) -> list[HumanisticPilotContext]:
    return [
        HumanisticPilotContext.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _normalized_content(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


def _context_content_fingerprint(record: HumanisticPilotContext) -> str:
    content = {
        "visible_history": [item.content for item in record.visible_history],
        "question_intent": record.frozen_plan.question_intent,
        "allowed_facts": record.allowed_facts,
        "reflection_summary": record.reflection_review.supported_summary,
    }
    serialized = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _locked_content_segments(
    records: list[HumanisticPilotContext],
) -> set[str]:
    segments: set[str] = set()
    for record in records:
        values = [
            *(item.content for item in record.visible_history),
            *record.allowed_facts,
            record.frozen_plan.question_intent,
            record.reflection_review.supported_summary,
        ]
        segments.update(
            normalized
            for value in values
            if len(normalized := _normalized_content(value)) >= 12
        )
    return segments


def _validate_review_examples(
    records: list[object],
    *,
    development_ids: set[str],
    locked_ids: set[str],
) -> None:
    example_ids: list[str] = []
    for index, item in enumerate(records, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"review example line {index} must be an object")
        example_id = item.get("example_id")
        context_id = item.get("context_id")
        if not isinstance(example_id, str) or not example_id:
            raise ValueError(f"review example line {index} requires example_id")
        if not isinstance(context_id, str) or not context_id:
            raise ValueError(f"review example line {index} requires context_id")
        example_ids.append(example_id)
        if context_id in locked_ids:
            raise ValueError(f"review examples reference locked context: {context_id}")
        if context_id not in development_ids:
            raise ValueError(f"review examples reference unknown context: {context_id}")
        if item.get("status") != "provisional_synthetic":
            raise ValueError("review examples remain provisional author-rule assets")
        if item.get("human_review") is not None:
            raise ValueError("review examples must not contain fabricated human review")
    if len(example_ids) != len(set(example_ids)):
        raise ValueError("review example IDs must be unique")


def _validate_locked_isolation(
    development: list[HumanisticPilotContext],
    locked: list[HumanisticPilotContext],
    *,
    development_path: Path,
    review_examples_path: Path,
    artifact_paths: dict[str, Path],
) -> None:
    development_fingerprints = {
        _context_content_fingerprint(item) for item in development
    }
    locked_fingerprints = {_context_content_fingerprint(item) for item in locked}
    if development_fingerprints & locked_fingerprints:
        raise ValueError("development and locked contexts duplicate frozen content")

    leakage_targets = {
        "development contexts": development_path.read_text(encoding="utf-8"),
        "review examples": review_examples_path.read_text(encoding="utf-8"),
        "prompt seed registry": artifact_paths[
            "prompt_seed_registry"
        ].read_text(encoding="utf-8"),
        "interviewer prompt": artifact_paths[
            "interviewer_agent"
        ].read_text(encoding="utf-8"),
    }
    locked_identifiers = {
        identifier
        for item in locked
        for identifier in (item.context_id, item.scenario_id)
    }
    locked_segments = _locked_content_segments(locked)
    for label, raw_text in leakage_targets.items():
        for identifier in locked_identifiers:
            if identifier in raw_text:
                raise ValueError(f"locked identifier leaked into {label}: {identifier}")
        normalized_target = _normalized_content(raw_text)
        for segment in locked_segments:
            if segment in normalized_target:
                raise ValueError(f"locked content leaked into {label}")


def load_context_manifest(
    path: Path,
    *,
    repo_root: Path,
    require_frozen: bool = False,
) -> list[HumanisticPilotContext]:
    path = _validate_manifest_location(path, repo_root)
    manifest = HumanisticContextManifest.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    development_path = _verify_asset(repo_root, manifest.development_contexts)
    locked_path = _verify_asset(repo_root, manifest.locked_test_contexts)
    review_examples_path = _verify_asset(repo_root, manifest.review_examples)
    artifact_paths = {
        artifact.artifact_id: _verify_asset(repo_root, artifact)
        for artifact in manifest.freeze_artifacts
    }

    development = _load_context_file(development_path)
    locked = _load_context_file(locked_path)
    if len(development) != manifest.development_contexts.count:
        raise ValueError("development context count does not match manifest")
    if len(locked) != manifest.locked_test_contexts.count:
        raise ValueError("locked context count does not match manifest")
    if any(item.split == "locked_test" for item in development):
        raise ValueError("development file must not contain locked_test rows")
    if any(item.split != "locked_test" for item in locked):
        raise ValueError("locked file may only contain locked_test rows")

    expected_status = manifest.status
    record_statuses = {item.status for item in development + locked}
    if record_statuses != {expected_status}:
        raise ValueError("manifest and all context rows must share one status")
    if require_frozen and expected_status != "frozen_v1":
        raise ValueError("release evaluation requires manifest status frozen_v1")

    locked_ids = {item.context_id for item in locked}
    if locked_ids != set(manifest.new_locked_context_ids):
        raise ValueError("locked context IDs do not match manifest")
    if set(manifest.retired_locked_context_ids) & {
        item.context_id for item in development + locked
    }:
        raise ValueError("retired locked context re-entered the evaluation manifest")

    review_examples: list[object] = []
    for line_number, line in enumerate(
        review_examples_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            review_examples.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"review example line {line_number} is invalid JSON"
            ) from exc
    if len(review_examples) != manifest.review_examples.count:
        raise ValueError("review example count does not match manifest")
    _validate_review_examples(
        review_examples,
        development_ids={item.context_id for item in development},
        locked_ids=locked_ids,
    )
    _validate_locked_isolation(
        development,
        locked,
        development_path=development_path,
        review_examples_path=review_examples_path,
        artifact_paths=artifact_paths,
    )

    records = development + locked
    validate_context_manifest(records)
    return records


def validate_context_manifest(records: list[HumanisticPilotContext]) -> None:
    if len(records) != 48:
        raise ValueError("humanistic context manifest must contain exactly 48 rows")
    context_ids = [item.context_id for item in records]
    scenario_ids = [item.scenario_id for item in records]
    if len(context_ids) != len(set(context_ids)):
        raise ValueError("context_id must be unique")
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("scenario_id must be unique")
    if Counter(item.split for item in records) != EXPECTED_SPLIT_COUNTS:
        raise ValueError("context split must remain 32/8/8")
    if Counter(item.category for item in records) != EXPECTED_CATEGORY_COUNTS:
        raise ValueError("context category distribution changed")
    if (
        Counter(item.frozen_plan.target_dimension for item in records)
        != EXPECTED_DIMENSION_COUNTS
    ):
        raise ValueError("context dimension distribution changed")
    for split in EXPECTED_SPLIT_COUNTS:
        dimensions = {
            item.frozen_plan.target_dimension
            for item in records
            if item.split == split
        }
        if dimensions != CANONICAL_DIMENSION_KEYS:
            raise ValueError(f"{split} must cover all six dimensions")


def build_runtime_context(record: HumanisticPilotContext) -> AgentRuntimeContext:
    target = record.frozen_plan.target_dimension
    stage_code, stage_order, stage_title = _stage_contract(record.category, target)
    history = [item.model_copy(deep=True) for item in record.visible_history]
    latest = next(
        item for item in history if item.turn_id == record.latest_user_turn_id
    )
    background = "；".join(record.allowed_facts)
    return AgentRuntimeContext(
        session=SessionContext(
            session_uuid=f"offline-{record.context_id.lower()}",
            assessment_mode="offline_evaluation",
        ),
        participant=ParticipantContext(nickname="离线评测参与者"),
        scenario=ScenarioContext(
            scenario_code=record.scenario_id,
            title=record.context_id,
            background=background,
        ),
        stage=StageContext(
            stage_code=stage_code,
            stage_order=stage_order,
            title=stage_title,
            stage_goal=f"固定观察 {target}",
            context=background,
            main_question=record.frozen_plan.question_intent,
        ),
        dialogue_history=history,
        latest_user_turn=latest,
    )


def build_evaluation_blueprint(
    record: HumanisticPilotContext,
) -> GeneratedScenarioBlueprint:
    nodes: list[GeneratedStoryNode] = []
    cards: list[GeneratedEventCard] = []
    previous_event: str | None = None
    fact_codes: list[str] = []
    for node_code, event_code, node_role in _EVENT_LAYOUT:
        selected = record.event_unit if event_code == "counter_evidence" else None
        unit_code = (
            selected.unit_code if selected else f"{event_code}_context_1"
        )
        unit_text = selected.text if selected else _EVENT_PLACEHOLDERS[event_code]
        direction = (
            selected.counterevidence_direction if selected else "neutral"
        )
        unit = PresentationUnit(
            unit_code=unit_code,
            text=unit_text,
            required=True,
            counterevidence_direction=direction,
        )
        fact_codes.append(unit_code)
        cards.append(
            GeneratedEventCard(
                event_code=event_code,
                node_code=node_code,
                node_role=node_role,
                facts=[unit_text],
                presentation_units=[unit],
                release_condition=EventReleaseCondition(
                    after_event_codes=[previous_event] if previous_event else [],
                    requires_any_evidence=(
                        ["problem_definition"]
                        if event_code == "evidence_uncertainty"
                        else []
                    ),
                    requires_prior_decision=event_code == "counter_evidence",
                    reserved_turns_before_end=(
                        2 if event_code == "counter_evidence" else 0
                    ),
                ),
                elicitation_opportunities=_EVENT_OPPORTUNITIES[event_code],
            )
        )
        nodes.append(
            GeneratedStoryNode(
                node_code=node_code,
                event_code=event_code,
                node_role=node_role,
                stable_facts=[unit_text],
                question_goal=f"离线评测固定 {event_code} 功能",
            )
        )
        previous_event = event_code

    latest = next(
        item
        for item in record.visible_history
        if item.turn_id == record.latest_user_turn_id
    )
    return GeneratedScenarioBlueprint(
        occupation_category="离线评测",
        occupation="合成工作情境",
        user_role="评测参与者",
        title=record.context_id,
        core_dilemma=latest.content,
        decision_goal=record.frozen_plan.question_intent,
        story_nodes=nodes,
        event_cards=cards,
        conversation_budget=ConversationBudget(
            min_total_user_turns=6,
            max_total_user_turns=12,
            max_probes_per_topic=2,
            max_consecutive_same_dimension=2,
            max_clarifications_per_answer=1,
            reserved_update_turns=2,
            reserved_closure_turns=1,
        ),
        task_domain="humanistic_interviewer_offline_evaluation",
        fact_envelope_codes=fact_codes,
    )


def build_renderer_input_payload(
    record: HumanisticPilotContext,
    *,
    style_version: str,
) -> dict[str, object]:
    from app.agents.interviewer_agent import InterviewerAgent

    return InterviewerAgent.renderer_input_payload(
        build_runtime_context(record),
        build_evaluation_blueprint(record),
        record.frozen_plan,
        style_version=style_version,
    )


def manifest_as_json(records: list[HumanisticPilotContext]) -> str:
    return "\n".join(
        json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        for item in records
    )


__all__ = [
    "CANONICAL_DIMENSION_KEYS",
    "CONTEXT_SCHEMA_VERSION",
    "EXPECTED_FREEZE_GATE_IDS",
    "EXPECTED_LOCKED_CONTEXT_IDS_V1",
    "EXPECTED_REJECTION_TEST_IDS",
    "EXPECTED_RETIRED_LOCKED_CONTEXT_IDS_V1",
    "MANIFEST_SCHEMA_VERSION",
    "ContextFreezeRecord",
    "HumanisticContextManifest",
    "HumanisticPilotContext",
    "build_evaluation_blueprint",
    "build_renderer_input_payload",
    "build_runtime_context",
    "load_context_manifest",
    "manifest_as_json",
    "validate_context_manifest",
]
