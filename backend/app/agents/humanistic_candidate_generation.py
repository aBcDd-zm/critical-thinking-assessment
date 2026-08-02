from __future__ import annotations

import hashlib
import json
import os
import random
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.humanistic_evaluation_context import (
    HumanisticContextManifest,
    HumanisticPilotContext,
    build_evaluation_blueprint,
    build_runtime_context,
    validate_context_manifest,
)
from app.agents.interview_blueprint import GeneratedScenarioBlueprint
from app.agents.interview_question_validator import InterviewQuestionValidator
from app.agents.interviewer_output_contract import (
    INTERVIEWER_OUTPUT_CONTRACT_SHA256,
    INTERVIEWER_OUTPUT_CONTRACT_VERSION,
    INTERVIEWER_OUTPUT_REQUIRED_FIELDS,
    OutputContractIssue,
    parse_strict_interviewer_output,
)
from app.agents.interviewer_agent import (
    BASELINE_INTERVIEWER_STYLE,
    CANDIDATE_GENERATION_MODE,
    HUMANISTIC_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_PROMPT_VERSION,
    INTERVIEWER_PROMPT_VERSION,
    InterviewerAgent,
)
from app.agents.progressive_schemas import InterviewPlanOutput, InterviewerOutput
from app.agents.schemas import AgentRuntimeContext
from app.core.config import Settings, get_settings
from app.services.model_gateway_service import ModelIdentityResponseError


ArmName = Literal["baseline", "humanistic", "fallback"]
ModelArmName = Literal["baseline", "humanistic"]

GENERATION_SCHEMA_VERSION = "humanistic_candidate_generation_v1"
BLIND_PACKET_SCHEMA_VERSION = "humanistic_blind_review_packet_v1"
GENERATION_MANIFEST_SCHEMA_VERSION = "humanistic_candidate_generation_manifest_v1"
EXPECTED_ARMS = ("baseline", "humanistic", "fallback")
MODEL_ARMS = ("baseline", "humanistic")
PROMPT_VERSION_BY_ARM = {
    "baseline": INTERVIEWER_PROMPT_VERSION,
    "humanistic": HUMANISTIC_INTERVIEWER_PROMPT_VERSION,
}
STYLE_VERSION_BY_ARM = {
    "baseline": BASELINE_INTERVIEWER_STYLE,
    "humanistic": HUMANISTIC_INTERVIEWER_STYLE,
    "fallback": HUMANISTIC_INTERVIEWER_STYLE,
}
CASE_ID_PATTERN = r"^case_[0-9a-f]{32}$"
CANDIDATE_ID_PATTERN = r"^cand_[0-9a-f]{32}$"
ATTEMPT_ID_PATTERN = r"^attempt_[0-9a-f]{32}$"
COLLISION_ID_PATTERN = r"^collision_[0-9a-f]{32}$"
RUN_ID_PATTERN = r"^run_[0-9a-f]{32}$"
QUALITY_FLAG_VALIDATOR_CODE_MAP: dict[str, frozenset[str]] = {
    "single_focus": frozenset(
        {"question_count", "plan_question_omission", "too_many_sentences"}
    ),
    "faithful_reflection": frozenset(
        {
            "unsupported_inference",
            "ungrounded_reflection",
            "reflection_quote_ids",
            "missing_reflection",
        }
    ),
    "non_judgmental": frozenset({"judgmental", "evaluative_praise"}),
    "non_leading": frozenset(
        {
            "leading",
            "agreement_pressure",
            "prescriptive_authority",
            "corrective_instruction",
            "forced_resolution",
        }
    ),
    "no_internal_terms": frozenset({"internal_terms"}),
    "no_unreleased_facts": frozenset(
        {"unreleased_fact", "unexpected_fact"}
    ),
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalized_candidate_text(value: str) -> str:
    return re.sub(r"[\s，。！？?、：；“”‘’\"']", "", value).lower()


def _collision_scope(
    baseline_text: str,
    humanistic_text: str,
    fallback_text: str,
) -> Literal[
    "model_pair_normalized_only",
    "fallback_involved",
    "three_way",
]:
    normalized = [
        _normalized_candidate_text(value)
        for value in (baseline_text, humanistic_text, fallback_text)
    ]
    if len(set(normalized)) == 1:
        return "three_way"
    if normalized[2] in normalized[:2]:
        return "fallback_involved"
    return "model_pair_normalized_only"


def quality_flag_mismatches(
    output: InterviewerOutput,
    validation_codes: list[str],
) -> list[str]:
    """Return audit-only contradictions without changing acceptance semantics."""
    codes = set(validation_codes)
    mismatches: list[str] = []
    for flag_name, declared_value in output.quality_flags.model_dump().items():
        if not declared_value:
            mismatches.append(f"{flag_name}:declared_false")
            continue
        conflicting_codes = sorted(
            codes & QUALITY_FLAG_VALIDATOR_CODE_MAP.get(flag_name, frozenset())
        )
        if conflicting_codes:
            mismatches.append(
                f"{flag_name}:claimed_true_but_failed:"
                + ",".join(conflicting_codes)
            )
    return mismatches


class GenerationContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptSource(GenerationContractModel):
    arm: ModelArmName
    template_code: str
    version: str
    content: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationSourceHashes(GenerationContractModel):
    context_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_cli_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    interviewer_agent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_contract_module_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_adapter_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_gateway_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationProtocol(GenerationContractModel):
    schema_version: Literal["humanistic_candidate_generation_v1"] = (
        GENERATION_SCHEMA_VERSION
    )
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: Literal[0.2] = 0.2
    max_tokens: Literal[700] = 700
    json_mode: Literal[True] = True
    thinking_enabled: Literal[False] = False
    reasoning_effort: Literal["low"] = "low"
    timeout_seconds: Literal[15] = 15
    max_paired_rounds: Literal[3] = 3
    model_attempts_per_arm_per_round: Literal[1] = 1
    retry_selection_policy: Literal[
        "first_valid_per_arm_across_paired_rounds"
    ] = "first_valid_per_arm_across_paired_rounds"
    baseline_repair_enabled: Literal[False] = False
    model_failure_substitutes_fallback: Literal[False] = False
    prompt_source: Literal["frozen_seed_registry"] = "frozen_seed_registry"
    case_and_arm_double_blind: Literal[True] = True
    output_contract_version: str = INTERVIEWER_OUTPUT_CONTRACT_VERSION
    output_contract_sha256: str = Field(
        default=INTERVIEWER_OUTPUT_CONTRACT_SHA256,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_output_contract_lock(self) -> "GenerationProtocol":
        if self.output_contract_version != INTERVIEWER_OUTPUT_CONTRACT_VERSION:
            raise ValueError("generation protocol output contract version drift")
        if self.output_contract_sha256 != INTERVIEWER_OUTPUT_CONTRACT_SHA256:
            raise ValueError("generation protocol output contract SHA-256 drift")
        return self


class BlindVisibleTurn(GenerationContractModel):
    turn_id: int = Field(ge=1)
    speaker: Literal["user", "ai"]
    content: str = Field(min_length=1, max_length=1000)


class BlindReviewContext(GenerationContractModel):
    visible_history: list[BlindVisibleTurn] = Field(min_length=1, max_length=4)
    question_intent: str = Field(min_length=1, max_length=500)
    allowed_facts: list[str] = Field(min_length=1, max_length=12)
    reflection_basis_turn_ids: list[int] = Field(default_factory=list, max_length=4)
    expected_question_count: int = Field(ge=0, le=1)
    formal_answer: bool


class BlindCandidate(GenerationContractModel):
    candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    candidate_text: str = Field(min_length=1, max_length=500)


class BlindReviewCase(GenerationContractModel):
    schema_version: Literal["humanistic_blind_review_packet_v1"] = (
        BLIND_PACKET_SCHEMA_VERSION
    )
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    review_context: BlindReviewContext
    candidates: list[BlindCandidate] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_candidates(self) -> "BlindReviewCase":
        candidate_ids = [item.candidate_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique within a blind case")
        exact_texts = {item.candidate_text for item in self.candidates}
        normalized = {
            _normalized_candidate_text(item.candidate_text)
            for item in self.candidates
        }
        if len(normalized) == 3:
            return self
        if len(exact_texts) == 2 and len(normalized) == 2:
            return self
        raise ValueError(
            "blind candidates allow only three distinct texts or one exact pair tie"
        )
        return self


class CaseKeyRecord(GenerationContractModel):
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    context_id: str = Field(pattern=r"^HIV1-[A-Z][0-9]{2}$")
    split: Literal["train", "dev", "locked_test"]


class ArmAssignment(GenerationContractModel):
    candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    arm: ArmName


class ArmKeyRecord(GenerationContractModel):
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    assignments: list[ArmAssignment] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_assignments(self) -> "ArmKeyRecord":
        candidate_ids = [item.candidate_id for item in self.assignments]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("arm key candidate IDs must be unique")
        if {item.arm for item in self.assignments} != set(EXPECTED_ARMS):
            raise ValueError("arm key requires one baseline, humanistic, and fallback")
        return self


class ExactModelTieRecord(GenerationContractModel):
    schema_version: Literal["humanistic_model_pair_exact_tie_v1"] = (
        "humanistic_model_pair_exact_tie_v1"
    )
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    context_id: str = Field(pattern=r"^HIV1-[A-Z][0-9]{2}$")
    split: Literal["train", "dev", "locked_test"]
    paired_round: int = Field(ge=1, le=3)
    collision_scope: Literal["model_pair_exact"] = "model_pair_exact"
    candidate_ids: list[str] = Field(min_length=2, max_length=2)
    candidate_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fallback_candidate_id: str = Field(pattern=CANDIDATE_ID_PATTERN)
    fallback_candidate_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_exact_tie(self) -> "ExactModelTieRecord":
        if len(set(self.candidate_ids)) != 2:
            raise ValueError("exact tie candidate IDs must be unique")
        if self.fallback_candidate_id in self.candidate_ids:
            raise ValueError("exact tie fallback candidate ID must be distinct")
        if self.fallback_candidate_text_sha256 == self.candidate_text_sha256:
            raise ValueError("exact tie fallback text must remain distinct")
        return self


class AttemptProvenance(GenerationContractModel):
    attempt_id: str = Field(pattern=ATTEMPT_ID_PATTERN)
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    context_id: str = Field(pattern=r"^HIV1-[A-Z][0-9]{2}$")
    split: Literal["train", "dev", "locked_test"]
    arm: ArmName
    paired_round: int = Field(ge=1, le=3)
    call_position: int = Field(ge=1, le=3)
    status: Literal["success", "failed"]
    selected: bool
    candidate_id: str | None = Field(default=None, pattern=CANDIDATE_ID_PATTERN)
    source_type: Literal["model", "deterministic"]
    style_version: str
    prompt_version: str
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_contract_version: str
    output_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    renderer_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_text_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    raw_output_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    provider: str | None = None
    model: str | None = None
    duration_ms: int = Field(ge=0)
    validation_codes: list[str] = Field(default_factory=list)
    quality_flag_mismatches: list[str] = Field(default_factory=list)
    contract_errors: list[OutputContractIssue] = Field(default_factory=list)


class GenerationFailure(GenerationContractModel):
    attempt_id: str | None = Field(default=None, pattern=ATTEMPT_ID_PATTERN)
    collision_id: str | None = Field(default=None, pattern=COLLISION_ID_PATTERN)
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    case_id: str = Field(pattern=CASE_ID_PATTERN)
    context_id: str = Field(pattern=r"^HIV1-[A-Z][0-9]{2}$")
    split: Literal["train", "dev", "locked_test"]
    arm: ArmName | None = None
    paired_round: int = Field(ge=1, le=3)
    error_code: str = Field(min_length=1)
    collision_scope: Literal[
        "model_pair_normalized_only",
        "fallback_involved",
        "three_way",
        "mixed",
    ] | None = None
    fatal: bool = False
    validation_codes: list[str] = Field(default_factory=list)
    quality_flag_mismatches: list[str] = Field(default_factory=list)
    contract_errors: list[OutputContractIssue] = Field(default_factory=list)
    raw_output_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_failure_identity(self) -> "GenerationFailure":
        if self.error_code == "candidate_text_collision":
            if (
                self.attempt_id is not None
                or self.collision_id is None
                or self.arm is not None
                or self.collision_scope is None
            ):
                raise ValueError(
                    "candidate collision must use pair-level collision audit"
                )
        elif (
            self.attempt_id is None
            or self.collision_id is not None
            or self.arm is None
            or self.collision_scope is not None
        ):
            raise ValueError("arm failure must use an attempt-level audit")
        return self


class CandidateGenerationManifest(GenerationContractModel):
    schema_version: Literal["humanistic_candidate_generation_manifest_v1"] = (
        GENERATION_MANIFEST_SCHEMA_VERSION
    )
    status: Literal["complete", "blocked"]
    run_id: str = Field(pattern=RUN_ID_PATTERN)
    context_count: int = Field(ge=0)
    attempted_context_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    case_key_count: int = Field(ge=0)
    arm_key_count: int = Field(ge=0)
    provenance_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    exact_model_tie_count: int = Field(ge=0)
    blocked_context_ids: list[str] = Field(default_factory=list)
    stop_reason: str | None = Field(default=None, min_length=1)
    stop_context_id: str | None = Field(
        default=None,
        pattern=r"^HIV1-[A-Z][0-9]{2}$",
    )
    source_hashes: GenerationSourceHashes
    protocol: GenerationProtocol
    output_sha256: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stop_audit(self) -> "CandidateGenerationManifest":
        if self.status == "complete":
            if self.attempted_context_count != self.context_count:
                raise ValueError("complete generation must attempt every context")
            if self.blocked_context_ids or self.stop_reason or self.stop_context_id:
                raise ValueError("complete generation must not contain stop audit fields")
            return self
        if self.exact_model_tie_count != 0:
            raise ValueError("blocked atomic generation cannot publish exact ties")
        if not 1 <= self.attempted_context_count <= self.context_count:
            raise ValueError("blocked generation requires attempted context coverage")
        if self.stop_reason is None or self.stop_context_id is None:
            raise ValueError("blocked generation requires an explicit stop audit")
        if self.blocked_context_ids != [self.stop_context_id]:
            raise ValueError("blocked generation must identify the fail-fast context")
        return self


@dataclass(frozen=True)
class ArmGenerationRequest:
    arm: ArmName
    style_version: str
    prompt_version: str
    prompt_content: str
    prompt_sha256: str
    output_contract_version: str
    output_contract_sha256: str
    renderer_input: dict[str, object]
    runtime_context: AgentRuntimeContext
    blueprint: GeneratedScenarioBlueprint
    plan: InterviewPlanOutput
    previous_questions: list[str]


@dataclass(frozen=True)
class ArmGenerationResult:
    output: InterviewerOutput
    raw_output: str
    provider: str | None
    model: str | None
    duration_ms: int
    validation_codes: list[str] = field(default_factory=list)
    quality_flag_mismatches: list[str] = field(default_factory=list)


class CandidateArmFailure(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        validation_codes: list[str] | None = None,
        quality_flag_mismatches: list[str] | None = None,
        contract_errors: list[OutputContractIssue] | None = None,
        raw_output: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        duration_ms: int = 0,
        fatal: bool = False,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.validation_codes = validation_codes or []
        self.quality_flag_mismatches = quality_flag_mismatches or []
        self.contract_errors = contract_errors or []
        self.raw_output = raw_output
        self.provider = provider
        self.model = model
        self.duration_ms = max(duration_ms, 0)
        self.fatal = fatal


class CandidateArmRenderer(Protocol):
    def render(self, request: ArmGenerationRequest) -> ArmGenerationResult: ...


class StrictFrozenInterviewerRenderer:
    """One-shot model arms plus an independently requested deterministic arm."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.timeout_seconds = (
            self.settings.CANDIDATE_GENERATION_TIMEOUT_SECONDS
        )
        self.agent = InterviewerAgent()
        self.validator = InterviewQuestionValidator()

    def render(self, request: ArmGenerationRequest) -> ArmGenerationResult:
        if (
            request.output_contract_version
            != INTERVIEWER_OUTPUT_CONTRACT_VERSION
            or request.output_contract_sha256
            != INTERVIEWER_OUTPUT_CONTRACT_SHA256
        ):
            raise CandidateArmFailure(
                "output_contract_lock_mismatch",
                fatal=True,
            )
        if request.arm == "fallback":
            try:
                return self._render_fallback(request)
            except CandidateArmFailure:
                raise
            except Exception as exc:  # noqa: BLE001
                raise CandidateArmFailure(
                    "deterministic_fallback_exception",
                    validation_codes=[type(exc).__name__],
                ) from exc
        if self.settings.MODEL_GATEWAY_MODE.lower() != "real":
            raise CandidateArmFailure("real_model_mode_required", fatal=True)

        started = perf_counter()
        raw = ""
        model: str | None = None
        try:
            raw, model = self.agent._call(  # noqa: SLF001
                request.renderer_input,
                request.prompt_content,
                request.style_version,
                self.timeout_seconds,
                repair=None,
            )
            if model is None:
                raise CandidateArmFailure(
                    "model_identity_missing",
                    raw_output=raw,
                    fatal=True,
                )
            if model != self.settings.DEEPSEEK_MODEL:
                raise CandidateArmFailure(
                    "model_identity_mismatch",
                    validation_codes=["MODEL_IDENTITY_MISMATCH"],
                    raw_output=raw,
                    model=model,
                    fatal=True,
                )
            output = self._parse_and_validate(request, raw)
            if output.fallback_used:
                raise CandidateArmFailure(
                    "model_arm_returned_fallback",
                    raw_output=raw,
                    model=model,
                )
            return ArmGenerationResult(
                output=output,
                raw_output=raw,
                provider=self.settings.MODEL_PROVIDER,
                model=model or self.settings.DEEPSEEK_MODEL,
                duration_ms=int((perf_counter() - started) * 1000),
            )
        except CandidateArmFailure as exc:
            raise CandidateArmFailure(
                exc.error_code,
                validation_codes=exc.validation_codes,
                quality_flag_mismatches=exc.quality_flag_mismatches,
                contract_errors=exc.contract_errors,
                raw_output=exc.raw_output or raw,
                provider=exc.provider or self.settings.MODEL_PROVIDER,
                model=exc.model or model,
                duration_ms=int((perf_counter() - started) * 1000),
                fatal=exc.fatal,
            ) from exc
        except ModelIdentityResponseError as exc:
            raise CandidateArmFailure(
                "model_identity_missing",
                validation_codes=["MODEL_IDENTITY_MISSING"],
                raw_output=exc.raw_output,
                provider=self.settings.MODEL_PROVIDER,
                model=None,
                duration_ms=int((perf_counter() - started) * 1000),
                fatal=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise CandidateArmFailure(
                "model_call_exception",
                validation_codes=[type(exc).__name__],
                raw_output=raw,
                provider=self.settings.MODEL_PROVIDER,
                model=model,
                duration_ms=int((perf_counter() - started) * 1000),
            ) from exc

    def _render_fallback(
        self,
        request: ArmGenerationRequest,
    ) -> ArmGenerationResult:
        started = perf_counter()
        output = self.agent._fallback(  # noqa: SLF001
            request.plan,
            request.blueprint,
            request.runtime_context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE,
        )
        errors = self._validation_errors(request, output, humanistic=True)
        if errors:
            raise CandidateArmFailure(
                "deterministic_fallback_invalid",
                validation_codes=errors,
                raw_output=output.model_dump_json(),
                duration_ms=int((perf_counter() - started) * 1000),
            )
        return ArmGenerationResult(
            output=output,
            raw_output=output.model_dump_json(),
            provider=None,
            model=None,
            duration_ms=int((perf_counter() - started) * 1000),
        )

    def _parse_and_validate(
        self,
        request: ArmGenerationRequest,
        raw: str,
    ) -> InterviewerOutput:
        output, contract_errors = parse_strict_interviewer_output(raw)
        if output is None:
            raise CandidateArmFailure(
                "output_contract_invalid",
                contract_errors=contract_errors,
                raw_output=raw,
            )
        errors = self._validation_errors(
            request,
            output,
            humanistic=request.arm == "humanistic",
        )
        if errors:
            raise CandidateArmFailure(
                "validator_rejected",
                validation_codes=errors,
                quality_flag_mismatches=quality_flag_mismatches(output, errors),
                raw_output=raw,
            )
        return output

    def _validation_errors(
        self,
        request: ArmGenerationRequest,
        output: InterviewerOutput,
        *,
        humanistic: bool,
    ) -> list[str]:
        event = next(
            (
                item
                for item in request.blueprint.event_cards
                if item.event_code == request.plan.release_event_code
            ),
            None,
        )
        unit = self.agent._selected_unit(  # noqa: SLF001
            event,
            request.plan.release_unit_code,
        )
        valid, errors = self.validator.validate(
            output,
            plan=request.plan,
            allowed_fact_codes=(
                {request.plan.release_unit_code}
                if request.plan.release_unit_code
                else set()
            ),
            previous_questions=request.previous_questions,
            allowed_source_turn_ids=set(request.plan.reflection_basis_turn_ids),
            source_turn_texts={
                item.turn_id: item.content
                for item in request.runtime_context.dialogue_history
                if item.turn_id is not None
            },
            allowed_fact_text=unit.text if unit else None,
            enforce_humanistic_safety=humanistic,
        )
        return [] if valid else errors


class OpaqueIdFactory:
    def run_id(self) -> str:
        return f"run_{secrets.token_hex(16)}"

    def case_id(self) -> str:
        return f"case_{secrets.token_hex(16)}"

    def candidate_id(self) -> str:
        return f"cand_{secrets.token_hex(16)}"

    def attempt_id(self) -> str:
        return f"attempt_{secrets.token_hex(16)}"

    def collision_id(self) -> str:
        return f"collision_{secrets.token_hex(16)}"


@dataclass
class CandidateGenerationBatch:
    manifest: CandidateGenerationManifest
    blind_cases: list[BlindReviewCase]
    case_key: list[CaseKeyRecord]
    arm_key: list[ArmKeyRecord]
    provenance: list[AttemptProvenance]
    failures: list[GenerationFailure]
    exact_model_ties: list[ExactModelTieRecord]


def load_frozen_prompt_sources(path: Path) -> dict[ModelArmName, PromptSource]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    templates = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(templates, list):
        raise ValueError("prompt registry must contain a templates list")
    sources: dict[ModelArmName, PromptSource] = {}
    for arm in MODEL_ARMS:
        expected = PROMPT_VERSION_BY_ARM[arm]
        matches = [
            item
            for item in templates
            if isinstance(item, dict)
            and item.get("agent_name") == "interviewer"
            and item.get("template_code") == expected
            and item.get("version") == expected
            and item.get("status") == "active"
        ]
        if len(matches) != 1:
            raise ValueError(f"prompt registry requires exactly one frozen {arm} prompt")
        template = matches[0]
        if (
            template.get("output_contract_version")
            != INTERVIEWER_OUTPUT_CONTRACT_VERSION
        ):
            raise ValueError(
                f"{arm} prompt output contract version differs from shared lock"
            )
        output_schema = template.get("output_schema_json")
        required = (
            output_schema.get("required")
            if isinstance(output_schema, dict)
            else None
        )
        if required != list(INTERVIEWER_OUTPUT_REQUIRED_FIELDS):
            raise ValueError(
                f"{arm} prompt required fields differ from shared output contract"
            )
        content = template.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{arm} prompt content is missing")
        sources[arm] = PromptSource(
            arm=arm,
            template_code=expected,
            version=expected,
            content=content,
            content_sha256=_sha256_text(content),
        )
    return sources


def _resolve_generation_asset(repo_root: Path, relative_path: str) -> Path:
    root = repo_root.resolve(strict=True)
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("generation assets must remain repository-relative")
    lexical = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"generation asset must not be a symlink: {relative_path}")
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(
            f"generation asset is outside the frozen repository: {relative_path}"
        ) from exc
    if not resolved.is_file():
        raise ValueError(f"generation asset is not a file: {relative_path}")
    return resolved


def load_frozen_generation_contexts(
    manifest_path: Path,
    *,
    repo_root: Path,
) -> tuple[HumanisticContextManifest, list[HumanisticPilotContext]]:
    """Load the frozen contexts without opening the review-example asset."""
    root = repo_root.resolve(strict=True)
    resolved_manifest = manifest_path.resolve(strict=True)
    try:
        resolved_manifest.relative_to(root)
    except ValueError as exc:
        raise ValueError("generation manifest must remain inside repository root") from exc
    if manifest_path.is_symlink():
        raise ValueError("generation manifest must not be a symlink")
    manifest = HumanisticContextManifest.model_validate_json(
        resolved_manifest.read_text(encoding="utf-8")
    )
    if manifest.status != "frozen_v1" or manifest.freeze_record is None:
        raise ValueError("candidate generation requires a formally frozen manifest")
    reliability_v3 = manifest.generation_reliability_amendment_v3
    if (
        reliability_v3 is None
        or reliability_v3.status != "frozen_after_smoke"
        or reliability_v3.action_matrix_status != "pass"
        or not reliability_v3.generation_restart_authorized
    ):
        raise ValueError(
            "candidate generation requires a passed and frozen reliability v3 gate"
        )
    if manifest.candidate_generator_status != "pending_before_generation":
        raise ValueError("frozen context manifest has an invalid generation state")

    development_path = _resolve_generation_asset(
        root,
        manifest.development_contexts.repo_relative_path,
    )
    locked_path = _resolve_generation_asset(
        root,
        manifest.locked_test_contexts.repo_relative_path,
    )
    for asset, path in (
        (manifest.development_contexts, development_path),
        (manifest.locked_test_contexts, locked_path),
    ):
        if sha256_file(path) != asset.sha256:
            raise ValueError(f"generation context hash mismatch: {asset.repo_relative_path}")
    for artifact in manifest.freeze_artifacts:
        path = _resolve_generation_asset(root, artifact.repo_relative_path)
        if sha256_file(path) != artifact.sha256:
            raise ValueError(f"generation source hash mismatch: {artifact.repo_relative_path}")

    def load_rows(path: Path) -> list[HumanisticPilotContext]:
        return [
            HumanisticPilotContext.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    development = load_rows(development_path)
    locked = load_rows(locked_path)
    if len(development) != manifest.development_contexts.count:
        raise ValueError("development count differs from frozen manifest")
    if len(locked) != manifest.locked_test_contexts.count:
        raise ValueError("locked count differs from frozen manifest")
    if any(item.split == "locked_test" for item in development):
        raise ValueError("development generation input contains locked rows")
    if any(item.split != "locked_test" for item in locked):
        raise ValueError("locked generation input contains non-locked rows")
    records = development + locked
    if {item.status for item in records} != {"frozen_v1"}:
        raise ValueError("candidate generation requires frozen_v1 context rows")
    if {item.context_id for item in locked} != set(
        manifest.new_locked_context_ids
    ):
        raise ValueError("locked generation IDs differ from frozen manifest")
    validate_context_manifest(records)
    return manifest, records


def validate_real_generation_environment(
    protocol: GenerationProtocol,
    settings: Settings,
) -> None:
    if settings.MODEL_GATEWAY_MODE.lower() != "real":
        raise ValueError("candidate generation requires MODEL_GATEWAY_MODE=real")
    if not settings.DEEPSEEK_API_KEY:
        raise ValueError("candidate generation requires a configured API key")
    if settings.MODEL_PROVIDER != protocol.provider:
        raise ValueError("configured provider differs from frozen generation protocol")
    if settings.DEEPSEEK_MODEL != protocol.model:
        raise ValueError("configured model differs from frozen generation protocol")
    if (
        settings.CANDIDATE_GENERATION_TIMEOUT_SECONDS
        != protocol.timeout_seconds
    ):
        raise ValueError(
            "configured candidate timeout differs from generation protocol"
        )


def build_candidate_arm_request(
    record: HumanisticPilotContext,
    arm: ArmName,
    prompt_sources: dict[ModelArmName, PromptSource],
) -> ArmGenerationRequest:
    runtime = build_runtime_context(record)
    blueprint = build_evaluation_blueprint(record)
    style_version = STYLE_VERSION_BY_ARM[arm]
    renderer_input = InterviewerAgent.renderer_input_payload(
        runtime,
        blueprint,
        record.frozen_plan,
        style_version=style_version,
    )
    renderer_input["generation_mode"] = CANDIDATE_GENERATION_MODE
    if arm == "fallback":
        prompt_version = "deterministic_humanistic_v1"
        prompt_content = "deterministic_plan_only_fallback"
        prompt_sha256 = _sha256_text(prompt_content)
    else:
        source = prompt_sources[arm]
        prompt_version = source.version
        prompt_content = source.content
        prompt_sha256 = source.content_sha256
    return ArmGenerationRequest(
        arm=arm,
        style_version=style_version,
        prompt_version=prompt_version,
        prompt_content=prompt_content,
        prompt_sha256=prompt_sha256,
        output_contract_version=INTERVIEWER_OUTPUT_CONTRACT_VERSION,
        output_contract_sha256=INTERVIEWER_OUTPUT_CONTRACT_SHA256,
        renderer_input=renderer_input,
        runtime_context=runtime,
        blueprint=blueprint,
        plan=record.frozen_plan,
        previous_questions=[
            item.content for item in runtime.dialogue_history if item.speaker == "ai"
        ],
    )


def _blind_context(record: HumanisticPilotContext) -> BlindReviewContext:
    return BlindReviewContext(
        visible_history=[
            BlindVisibleTurn(
                turn_id=item.turn_id,
                speaker=item.speaker,
                content=item.content,
            )
            for item in record.visible_history
            if item.turn_id is not None and item.speaker in {"user", "ai"}
        ],
        question_intent=record.frozen_plan.question_intent,
        allowed_facts=record.allowed_facts,
        reflection_basis_turn_ids=record.frozen_plan.reflection_basis_turn_ids,
        expected_question_count=(
            0 if record.frozen_plan.action == "CONCLUDE" else 1
        ),
        formal_answer=record.formal_answer,
    )


def _balanced_arm_orders(
    count: int,
    rng: random.Random,
) -> list[tuple[ModelArmName, ModelArmName]]:
    baseline_first = (count + 1) // 2
    orders: list[tuple[ModelArmName, ModelArmName]] = [
        ("baseline", "humanistic") for _ in range(baseline_first)
    ]
    orders.extend(
        ("humanistic", "baseline") for _ in range(count - baseline_first)
    )
    rng.shuffle(orders)
    return orders


def generate_candidate_batch(
    records: list[HumanisticPilotContext],
    *,
    renderer: CandidateArmRenderer,
    prompt_sources: dict[ModelArmName, PromptSource],
    protocol: GenerationProtocol,
    source_hashes: GenerationSourceHashes,
    id_factory: OpaqueIdFactory | None = None,
    rng: random.Random | None = None,
    enforce_production_count: bool = True,
) -> CandidateGenerationBatch:
    if enforce_production_count and len(records) != 48:
        raise ValueError("production candidate generation requires 48 contexts")
    if not records:
        raise ValueError("candidate generation requires at least one context")
    if any(item.status != "frozen_v1" for item in records):
        raise ValueError("candidate generation only accepts frozen_v1 contexts")
    context_ids = [item.context_id for item in records]
    if len(context_ids) != len(set(context_ids)):
        raise ValueError("candidate generation context IDs must be unique")
    if set(prompt_sources) != set(MODEL_ARMS):
        raise ValueError("both frozen model-arm prompts are required")

    id_factory = id_factory or OpaqueIdFactory()
    rng = rng or secrets.SystemRandom()
    run_id = id_factory.run_id()
    ordered_records = list(records)
    rng.shuffle(ordered_records)
    arm_orders = _balanced_arm_orders(len(ordered_records), rng)

    blind_cases: list[BlindReviewCase] = []
    case_key: list[CaseKeyRecord] = []
    arm_key: list[ArmKeyRecord] = []
    provenance: list[AttemptProvenance] = []
    failures: list[GenerationFailure] = []
    exact_model_ties: list[ExactModelTieRecord] = []
    blocked_context_ids: list[str] = []
    stop_reason: str | None = None
    stop_context_id: str | None = None

    for record, model_order in zip(ordered_records, arm_orders, strict=True):
        case_id = id_factory.case_id()
        case_key.append(
            CaseKeyRecord(
                case_id=case_id,
                context_id=record.context_id,
                split=record.split,
            )
        )
        selected_results: dict[ArmName, ArmGenerationResult] | None = None
        selected_attempt_ids: dict[ArmName, str] = {}
        valid_model_results: dict[
            ModelArmName,
            list[tuple[ArmGenerationResult, str]],
        ] = {arm: [] for arm in MODEL_ARMS}
        context_stop_reason: str | None = None
        collision_count = 0
        fallback_cache: tuple[ArmGenerationResult, str] | None = None
        selected_exact_tie = False
        selected_paired_round: int | None = None

        for paired_round in range(1, protocol.max_paired_rounds + 1):
            for call_position, arm in enumerate(model_order, start=1):
                request = build_candidate_arm_request(
                    record,
                    arm,
                    prompt_sources,
                )
                attempt_id = id_factory.attempt_id()
                input_sha = _sha256_text(_canonical_json(request.renderer_input))
                try:
                    result = renderer.render(request)
                    provenance.append(
                        AttemptProvenance(
                            attempt_id=attempt_id,
                            run_id=run_id,
                            case_id=case_id,
                            context_id=record.context_id,
                            split=record.split,
                            arm=arm,
                            paired_round=paired_round,
                            call_position=call_position,
                            status="success",
                            selected=False,
                            source_type="model",
                            style_version=request.style_version,
                            prompt_version=request.prompt_version,
                            prompt_sha256=request.prompt_sha256,
                            output_contract_version=(
                                request.output_contract_version
                            ),
                            output_contract_sha256=(
                                request.output_contract_sha256
                            ),
                            renderer_input_sha256=input_sha,
                            candidate_text_sha256=_sha256_text(
                                result.output.message
                            ),
                            raw_output_sha256=_sha256_text(result.raw_output),
                            provider=result.provider,
                            model=result.model,
                            duration_ms=result.duration_ms,
                            validation_codes=result.validation_codes,
                            quality_flag_mismatches=(
                                result.quality_flag_mismatches
                            ),
                        )
                    )
                    valid_model_results[arm].append((result, attempt_id))
                except CandidateArmFailure as exc:
                    raw_sha = (
                        _sha256_text(exc.raw_output)
                        if exc.raw_output is not None
                        else None
                    )
                    failures.append(
                        GenerationFailure(
                            attempt_id=attempt_id,
                            run_id=run_id,
                            case_id=case_id,
                            context_id=record.context_id,
                            split=record.split,
                            arm=arm,
                            paired_round=paired_round,
                            error_code=exc.error_code,
                            fatal=exc.fatal,
                            validation_codes=exc.validation_codes,
                            quality_flag_mismatches=(
                                exc.quality_flag_mismatches
                            ),
                            contract_errors=exc.contract_errors,
                            raw_output_sha256=raw_sha,
                        )
                    )
                    provenance.append(
                        AttemptProvenance(
                            attempt_id=attempt_id,
                            run_id=run_id,
                            case_id=case_id,
                            context_id=record.context_id,
                            split=record.split,
                            arm=arm,
                            paired_round=paired_round,
                            call_position=call_position,
                            status="failed",
                            selected=False,
                            source_type="model",
                            style_version=request.style_version,
                            prompt_version=request.prompt_version,
                            prompt_sha256=request.prompt_sha256,
                            output_contract_version=(
                                request.output_contract_version
                            ),
                            output_contract_sha256=(
                                request.output_contract_sha256
                            ),
                            renderer_input_sha256=input_sha,
                            raw_output_sha256=raw_sha,
                            provider=exc.provider,
                            model=exc.model,
                            duration_ms=exc.duration_ms,
                            validation_codes=exc.validation_codes,
                            quality_flag_mismatches=(
                                exc.quality_flag_mismatches
                            ),
                            contract_errors=exc.contract_errors,
                        )
                    )
                    if exc.fatal:
                        context_stop_reason = f"fatal_{exc.error_code}"
                        break

            if context_stop_reason is not None:
                break
            if any(not valid_model_results[arm] for arm in MODEL_ARMS):
                continue

            if fallback_cache is None:
                fallback_request = build_candidate_arm_request(
                    record,
                    "fallback",
                    prompt_sources,
                )
                fallback_attempt_id = id_factory.attempt_id()
                fallback_input_sha = _sha256_text(
                    _canonical_json(fallback_request.renderer_input)
                )
                try:
                    fallback_result = renderer.render(fallback_request)
                except CandidateArmFailure as exc:
                    fallback_raw_sha = (
                        _sha256_text(exc.raw_output)
                        if exc.raw_output is not None
                        else None
                    )
                    failures.append(
                        GenerationFailure(
                            attempt_id=fallback_attempt_id,
                            run_id=run_id,
                            case_id=case_id,
                            context_id=record.context_id,
                            split=record.split,
                            arm="fallback",
                            paired_round=paired_round,
                            error_code=exc.error_code,
                            fatal=True,
                            validation_codes=exc.validation_codes,
                            contract_errors=exc.contract_errors,
                            raw_output_sha256=fallback_raw_sha,
                        )
                    )
                    provenance.append(
                        AttemptProvenance(
                            attempt_id=fallback_attempt_id,
                            run_id=run_id,
                            case_id=case_id,
                            context_id=record.context_id,
                            split=record.split,
                            arm="fallback",
                            paired_round=paired_round,
                            call_position=3,
                            status="failed",
                            selected=False,
                            source_type="deterministic",
                            style_version=fallback_request.style_version,
                            prompt_version=fallback_request.prompt_version,
                            prompt_sha256=fallback_request.prompt_sha256,
                            output_contract_version=(
                                fallback_request.output_contract_version
                            ),
                            output_contract_sha256=(
                                fallback_request.output_contract_sha256
                            ),
                            renderer_input_sha256=fallback_input_sha,
                            raw_output_sha256=fallback_raw_sha,
                            duration_ms=exc.duration_ms,
                            validation_codes=exc.validation_codes,
                            contract_errors=exc.contract_errors,
                        )
                    )
                    context_stop_reason = "deterministic_fallback_failed"
                    break
                provenance.append(
                    AttemptProvenance(
                        attempt_id=fallback_attempt_id,
                        run_id=run_id,
                        case_id=case_id,
                        context_id=record.context_id,
                        split=record.split,
                        arm="fallback",
                        paired_round=paired_round,
                        call_position=3,
                        status="success",
                        selected=False,
                        source_type="deterministic",
                        style_version=fallback_request.style_version,
                        prompt_version=fallback_request.prompt_version,
                        prompt_sha256=fallback_request.prompt_sha256,
                        output_contract_version=(
                            fallback_request.output_contract_version
                        ),
                        output_contract_sha256=(
                            fallback_request.output_contract_sha256
                        ),
                        renderer_input_sha256=fallback_input_sha,
                        candidate_text_sha256=_sha256_text(
                            fallback_result.output.message
                        ),
                        raw_output_sha256=_sha256_text(
                            fallback_result.raw_output
                        ),
                        duration_ms=fallback_result.duration_ms,
                        validation_codes=fallback_result.validation_codes,
                    )
                )
                fallback_cache = (fallback_result, fallback_attempt_id)
            fallback_result, fallback_attempt_id = fallback_cache
            selected_pair: tuple[
                tuple[ArmGenerationResult, str],
                tuple[ArmGenerationResult, str],
            ] | None = None
            collision_scopes: set[str] = set()
            for baseline_result in valid_model_results["baseline"]:
                for humanistic_result in valid_model_results["humanistic"]:
                    baseline_text = baseline_result[0].output.message
                    humanistic_text = humanistic_result[0].output.message
                    fallback_text = fallback_result.output.message
                    if (
                        baseline_text == humanistic_text
                        and _normalized_candidate_text(baseline_text)
                        != _normalized_candidate_text(fallback_text)
                    ):
                        selected_pair = (
                            baseline_result,
                            humanistic_result,
                        )
                        selected_exact_tie = True
                        break
                    normalized = {
                        _normalized_candidate_text(baseline_text),
                        _normalized_candidate_text(humanistic_text),
                        _normalized_candidate_text(fallback_text),
                    }
                    if len(normalized) == 3:
                        selected_pair = (
                            baseline_result,
                            humanistic_result,
                        )
                        break
                    collision_scopes.add(
                        _collision_scope(
                            baseline_text,
                            humanistic_text,
                            fallback_text,
                        )
                    )
                if selected_pair is not None:
                    break
            if selected_pair is None:
                collision_count += 1
                failures.append(
                    GenerationFailure(
                        collision_id=id_factory.collision_id(),
                        run_id=run_id,
                        case_id=case_id,
                        context_id=record.context_id,
                        split=record.split,
                        paired_round=paired_round,
                        error_code="candidate_text_collision",
                        collision_scope=(
                            next(iter(collision_scopes))
                            if len(collision_scopes) == 1
                            else "mixed"
                        ),
                    )
                )
                continue
            selected_results = {
                "baseline": selected_pair[0][0],
                "humanistic": selected_pair[1][0],
                "fallback": fallback_result,
            }
            selected_attempt_ids = {
                "baseline": selected_pair[0][1],
                "humanistic": selected_pair[1][1],
                "fallback": fallback_attempt_id,
            }
            selected_paired_round = paired_round
            break

        if selected_results is None:
            blocked_context_ids.append(record.context_id)
            if context_stop_reason is None:
                context_stop_reason = (
                    "candidate_text_collision_exhausted"
                    if collision_count > 0
                    else "paired_rounds_exhausted"
                )
            stop_reason = context_stop_reason
            stop_context_id = record.context_id
            break

        assignments: list[ArmAssignment] = []
        candidates: list[BlindCandidate] = []
        candidate_ids_by_arm: dict[ArmName, str] = {}
        for arm in EXPECTED_ARMS:
            candidate_id = id_factory.candidate_id()
            candidate_ids_by_arm[arm] = candidate_id
            result = selected_results[arm]
            assignments.append(ArmAssignment(candidate_id=candidate_id, arm=arm))
            candidates.append(
                BlindCandidate(
                    candidate_id=candidate_id,
                    candidate_text=result.output.message,
                )
            )
            matching = next(
                item
                for item in provenance
                if item.attempt_id == selected_attempt_ids[arm]
            )
            matching.selected = True
            matching.candidate_id = candidate_id
        if selected_exact_tie:
            assert selected_paired_round is not None
            exact_model_ties.append(
                ExactModelTieRecord(
                    run_id=run_id,
                    case_id=case_id,
                    context_id=record.context_id,
                    split=record.split,
                    paired_round=selected_paired_round,
                    candidate_ids=[
                        candidate_ids_by_arm["baseline"],
                        candidate_ids_by_arm["humanistic"],
                    ],
                    candidate_text_sha256=_sha256_text(
                        selected_results["baseline"].output.message
                    ),
                    fallback_candidate_id=candidate_ids_by_arm["fallback"],
                    fallback_candidate_text_sha256=_sha256_text(
                        selected_results["fallback"].output.message
                    ),
                )
            )
        rng.shuffle(candidates)
        blind_cases.append(
            BlindReviewCase(
                case_id=case_id,
                review_context=_blind_context(record),
                candidates=candidates,
            )
        )
        arm_key.append(ArmKeyRecord(case_id=case_id, assignments=assignments))

    status: Literal["complete", "blocked"] = (
        "blocked" if blocked_context_ids else "complete"
    )
    if status == "blocked":
        blind_cases = []
        arm_key = []
        exact_model_ties = []
        for item in provenance:
            item.selected = False
            item.candidate_id = None
    manifest = CandidateGenerationManifest(
        status=status,
        run_id=run_id,
        context_count=len(records),
        attempted_context_count=len(case_key),
        candidate_count=sum(len(item.candidates) for item in blind_cases),
        case_key_count=len(case_key),
        arm_key_count=len(arm_key),
        provenance_count=len(provenance),
        failure_count=len(failures),
        exact_model_tie_count=len(exact_model_ties),
        blocked_context_ids=blocked_context_ids,
        stop_reason=stop_reason,
        stop_context_id=stop_context_id,
        source_hashes=source_hashes,
        protocol=protocol,
    )
    batch = CandidateGenerationBatch(
        manifest=manifest,
        blind_cases=blind_cases,
        case_key=case_key,
        arm_key=arm_key,
        provenance=provenance,
        failures=failures,
        exact_model_ties=exact_model_ties,
    )
    validate_candidate_batch(batch, expected_context_count=len(records))
    return batch


def validate_candidate_batch(
    batch: CandidateGenerationBatch,
    *,
    expected_context_count: int,
) -> None:
    manifest = batch.manifest
    if manifest.context_count != expected_context_count:
        raise ValueError("generation manifest context count is inconsistent")
    if manifest.candidate_count != sum(
        len(item.candidates) for item in batch.blind_cases
    ):
        raise ValueError("generation manifest candidate count is inconsistent")
    if manifest.case_key_count != len(batch.case_key):
        raise ValueError("generation manifest case-key count is inconsistent")
    if manifest.attempted_context_count != len(batch.case_key):
        raise ValueError("generation manifest attempted-context count is inconsistent")
    if manifest.arm_key_count != len(batch.arm_key):
        raise ValueError("generation manifest arm-key count is inconsistent")
    if manifest.provenance_count != len(batch.provenance):
        raise ValueError("generation manifest provenance count is inconsistent")
    if manifest.failure_count != len(batch.failures):
        raise ValueError("generation manifest failure count is inconsistent")
    if manifest.exact_model_tie_count != len(batch.exact_model_ties):
        raise ValueError("generation manifest exact-tie count is inconsistent")
    protocol = manifest.protocol
    if (
        protocol.output_contract_version
        != INTERVIEWER_OUTPUT_CONTRACT_VERSION
        or protocol.output_contract_sha256
        != INTERVIEWER_OUTPUT_CONTRACT_SHA256
    ):
        raise ValueError("generation protocol output contract lock is inconsistent")
    if any(
        item.output_contract_version != protocol.output_contract_version
        or item.output_contract_sha256 != protocol.output_contract_sha256
        for item in batch.provenance
    ):
        raise ValueError("provenance output contract lock is inconsistent")
    if any(
        item.status == "success" and item.contract_errors
        for item in batch.provenance
    ):
        raise ValueError("successful provenance cannot contain contract errors")
    if any(
        item.error_code == "output_contract_invalid"
        and not item.contract_errors
        for item in batch.failures
    ):
        raise ValueError(
            "output contract failure requires field-level contract errors"
        )
    if manifest.status != "complete":
        if batch.blind_cases or batch.arm_key:
            raise ValueError("blocked batch must not expose a partial blind packet")
        if manifest.candidate_count != 0 or manifest.arm_key_count != 0:
            raise ValueError("blocked batch counts must not claim formal candidates")
        if batch.exact_model_ties:
            raise ValueError("blocked batch must not publish exact-tie records")
        if not manifest.blocked_context_ids:
            raise ValueError("blocked batch must identify at least one blocked context")
        case_context_ids = {item.context_id for item in batch.case_key}
        if not set(manifest.blocked_context_ids).issubset(case_context_ids):
            raise ValueError("blocked context IDs must exist in the sealed case key")
        if not batch.case_key or (
            batch.case_key[-1].context_id != manifest.stop_context_id
        ):
            raise ValueError("fail-fast stop context must be the final attempted context")
        fatal_failures = [item for item in batch.failures if item.fatal]
        if manifest.stop_reason and manifest.stop_reason.startswith("fatal_"):
            expected_error = manifest.stop_reason.removeprefix("fatal_")
            if not any(
                item.context_id == manifest.stop_context_id
                and item.error_code == expected_error
                for item in fatal_failures
            ):
                raise ValueError("fatal stop reason must match the failure ledger")
        if manifest.stop_reason == "deterministic_fallback_failed" and not any(
            item.context_id == manifest.stop_context_id
            and item.arm == "fallback"
            and item.fatal
            for item in batch.failures
        ):
            raise ValueError("fallback stop reason must match the failure ledger")
        return
    if len(batch.blind_cases) != expected_context_count:
        raise ValueError("blind packet context coverage is incomplete")
    if len(batch.case_key) != expected_context_count:
        raise ValueError("case key context coverage is incomplete")
    if len(batch.arm_key) != expected_context_count:
        raise ValueError("arm key context coverage is incomplete")

    case_ids = [item.case_id for item in batch.blind_cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("case IDs must be globally unique")
    case_key_ids = [item.case_id for item in batch.case_key]
    arm_key_ids = [item.case_id for item in batch.arm_key]
    if len(case_key_ids) != len(set(case_key_ids)):
        raise ValueError("case key contains duplicate case IDs")
    if len(arm_key_ids) != len(set(arm_key_ids)):
        raise ValueError("arm key contains duplicate case IDs")
    if len({item.context_id for item in batch.case_key}) != len(batch.case_key):
        raise ValueError("case key contains duplicate true context IDs")
    if set(case_ids) != set(case_key_ids):
        raise ValueError("blind packet and case key case IDs differ")
    if set(case_ids) != set(arm_key_ids):
        raise ValueError("blind packet and arm key case IDs differ")

    tie_by_case = {item.case_id: item for item in batch.exact_model_ties}
    if len(tie_by_case) != len(batch.exact_model_ties):
        raise ValueError("exact-tie case IDs must be unique")
    case_key_by_id = {item.case_id: item for item in batch.case_key}
    if any(
        tie.run_id != manifest.run_id
        or tie.case_id not in case_key_by_id
        or tie.context_id != case_key_by_id[tie.case_id].context_id
        or tie.split != case_key_by_id[tie.case_id].split
        for tie in batch.exact_model_ties
    ):
        raise ValueError("exact-tie sealed provenance is not linked")
    candidate_ids = [
        candidate.candidate_id
        for case in batch.blind_cases
        for candidate in case.candidates
    ]
    for case in batch.blind_cases:
        if len(case.candidates) != 3:
            raise ValueError("each blind case must contain exactly three candidates")
        normalized_texts = {
            _normalized_candidate_text(candidate.candidate_text)
            for candidate in case.candidates
        }
        exact_text_groups: dict[str, list[str]] = {}
        for candidate in case.candidates:
            exact_text_groups.setdefault(candidate.candidate_text, []).append(
                candidate.candidate_id
            )
        duplicated_groups = [
            ids for ids in exact_text_groups.values() if len(ids) > 1
        ]
        tie = tie_by_case.get(case.case_id)
        if len(normalized_texts) == 3:
            if tie is not None:
                raise ValueError("exact tie record cannot annotate distinct candidates")
            continue
        if (
            len(normalized_texts) != 2
            or len(duplicated_groups) != 1
            or len(duplicated_groups[0]) != 2
        ):
            raise ValueError(
                "only one declared exact model-pair tie is allowed per case"
            )
        if tie is None:
            raise ValueError("exact duplicate candidates require a sealed tie record")
        if set(tie.candidate_ids) != set(duplicated_groups[0]):
            raise ValueError("sealed exact tie candidate IDs are inconsistent")
        if tie.fallback_candidate_id not in {
            item.candidate_id for item in case.candidates
        }:
            raise ValueError("sealed exact tie fallback candidate is missing")
        tied_text = next(
            text for text, ids in exact_text_groups.items() if len(ids) == 2
        )
        fallback_text = next(
            item.candidate_text
            for item in case.candidates
            if item.candidate_id == tie.fallback_candidate_id
        )
        if (
            tie.candidate_text_sha256 != _sha256_text(tied_text)
            or tie.fallback_candidate_text_sha256
            != _sha256_text(fallback_text)
            or _normalized_candidate_text(tied_text)
            == _normalized_candidate_text(fallback_text)
        ):
            raise ValueError("sealed exact tie content hashes are inconsistent")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be globally unique")
    keyed_candidate_ids = {
        item.candidate_id
        for record in batch.arm_key
        for item in record.assignments
    }
    if set(candidate_ids) != keyed_candidate_ids:
        raise ValueError("blind packet and arm key candidate IDs differ")
    blind_candidates_by_case = {
        item.case_id: {candidate.candidate_id for candidate in item.candidates}
        for item in batch.blind_cases
    }
    keyed_candidates_by_case = {
        item.case_id: {assignment.candidate_id for assignment in item.assignments}
        for item in batch.arm_key
    }
    if blind_candidates_by_case != keyed_candidates_by_case:
        raise ValueError("arm assignments were swapped across blind cases")
    selected_provenance = [
        item
        for item in batch.provenance
        if item.selected and item.candidate_id is not None
    ]
    selected_provenance_ids = {
        item.candidate_id
        for item in selected_provenance
    }
    if len(selected_provenance) != len(selected_provenance_ids):
        raise ValueError("selected provenance candidate IDs must be unique")
    if set(candidate_ids) != selected_provenance_ids:
        raise ValueError("selected provenance does not cover every candidate")
    selected_by_candidate = {
        item.candidate_id: item
        for item in batch.provenance
        if item.selected and item.candidate_id is not None
    }
    arm_by_candidate = {
        assignment.candidate_id: assignment.arm
        for item in batch.arm_key
        for assignment in item.assignments
    }
    if any(
        selected_by_candidate[candidate_id].arm != arm
        for candidate_id, arm in arm_by_candidate.items()
    ):
        raise ValueError("selected provenance arm differs from sealed arm key")
    for tie in batch.exact_model_ties:
        if {
            arm_by_candidate[candidate_id]
            for candidate_id in tie.candidate_ids
        } != set(MODEL_ARMS):
            raise ValueError("exact tie must contain baseline and humanistic candidates")
        if arm_by_candidate[tie.fallback_candidate_id] != "fallback":
            raise ValueError("exact tie fallback candidate arm is inconsistent")
    case_by_candidate = {
        assignment.candidate_id: item.case_id
        for item in batch.arm_key
        for assignment in item.assignments
    }
    if any(
        selected_by_candidate[candidate_id].case_id != case_id
        for candidate_id, case_id in case_by_candidate.items()
    ):
        raise ValueError("selected provenance case differs from sealed arm key")
    text_sha_by_candidate = {
        candidate.candidate_id: _sha256_text(candidate.candidate_text)
        for case in batch.blind_cases
        for candidate in case.candidates
    }
    if any(
        selected_by_candidate[candidate_id].status != "success"
        or selected_by_candidate[candidate_id].candidate_text_sha256
        != candidate_sha
        for candidate_id, candidate_sha in text_sha_by_candidate.items()
    ):
        raise ValueError("selected provenance content hash is inconsistent")
    if manifest.candidate_count != expected_context_count * 3:
        raise ValueError("complete batch must contain exactly three candidates per case")


def _jsonl_bytes(records: list[BaseModel]) -> bytes:
    return (
        "".join(item.model_dump_json() + "\n" for item in records)
    ).encode("utf-8")


def validate_private_output_path(output_dir: Path, *, repo_root: Path) -> Path:
    resolved_repo = repo_root.resolve(strict=True)
    resolved_output = output_dir.expanduser().resolve(strict=False)
    if resolved_output == resolved_repo or resolved_output.is_relative_to(
        resolved_repo
    ):
        raise ValueError("candidate output directory must remain outside the Git repository")
    if resolved_output.exists():
        raise FileExistsError(
            f"candidate output directory already exists: {resolved_output}"
        )
    return resolved_output


def _make_private_directory(path: Path, *, parents: bool = False) -> None:
    path.mkdir(mode=0o700, parents=parents, exist_ok=False)
    path.chmod(0o700)


def _write_private_bytes(path: Path, content: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)


def write_complete_batch(
    batch: CandidateGenerationBatch,
    output_dir: Path,
    *,
    repo_root: Path,
) -> None:
    validate_candidate_batch(
        batch,
        expected_context_count=batch.manifest.context_count,
    )
    if batch.manifest.status != "complete":
        raise ValueError("cannot write a formal candidate packet from a blocked batch")
    output_dir = validate_private_output_path(output_dir, repo_root=repo_root)

    reviewer_dir = output_dir / "reviewer"
    sealed_dir = output_dir / "sealed"
    _make_private_directory(output_dir, parents=True)
    _make_private_directory(reviewer_dir)
    _make_private_directory(sealed_dir)
    outputs = {
        "reviewer/blind_review_packet_v1.jsonl": _jsonl_bytes(batch.blind_cases),
        "sealed/case_key_v1.jsonl": _jsonl_bytes(batch.case_key),
        "sealed/arm_key_v1.jsonl": _jsonl_bytes(batch.arm_key),
        "sealed/candidate_provenance_v1.jsonl": _jsonl_bytes(batch.provenance),
        "sealed/generation_failures_v1.jsonl": _jsonl_bytes(batch.failures),
        "sealed/exact_model_ties_v1.jsonl": _jsonl_bytes(
            batch.exact_model_ties
        ),
    }
    for relative_path, content in outputs.items():
        _write_private_bytes(output_dir / relative_path, content)
    batch.manifest.output_sha256 = {
        relative_path: _sha256_bytes(content)
        for relative_path, content in outputs.items()
    }
    _write_private_bytes(
        output_dir / "candidate_generation_manifest_v1.json",
        (batch.manifest.model_dump_json(indent=2) + "\n").encode("utf-8"),
    )


def write_blocked_audit(
    batch: CandidateGenerationBatch,
    output_dir: Path,
    *,
    repo_root: Path,
) -> None:
    validate_candidate_batch(
        batch,
        expected_context_count=batch.manifest.context_count,
    )
    if batch.manifest.status != "blocked":
        raise ValueError("blocked audit writer only accepts blocked batches")
    if batch.blind_cases or batch.arm_key:
        raise ValueError("blocked audit must not contain a partial reviewer packet")
    output_dir = validate_private_output_path(output_dir, repo_root=repo_root)
    sealed_dir = output_dir / "sealed"
    _make_private_directory(output_dir, parents=True)
    _make_private_directory(sealed_dir)
    outputs = {
        "sealed/case_key_v1.jsonl": _jsonl_bytes(batch.case_key),
        "sealed/candidate_provenance_v1.jsonl": _jsonl_bytes(batch.provenance),
        "sealed/generation_failures_v1.jsonl": _jsonl_bytes(batch.failures),
        "sealed/exact_model_ties_v1.jsonl": _jsonl_bytes(
            batch.exact_model_ties
        ),
    }
    for relative_path, content in outputs.items():
        _write_private_bytes(output_dir / relative_path, content)
    batch.manifest.output_sha256 = {
        relative_path: _sha256_bytes(content)
        for relative_path, content in outputs.items()
    }
    _write_private_bytes(
        output_dir / "candidate_generation_manifest_v1.json",
        (batch.manifest.model_dump_json(indent=2) + "\n").encode("utf-8"),
    )


__all__ = [
    "ArmGenerationRequest",
    "ArmGenerationResult",
    "ArmKeyRecord",
    "BlindReviewCase",
    "CandidateArmFailure",
    "CandidateGenerationBatch",
    "CandidateGenerationManifest",
    "CaseKeyRecord",
    "GenerationFailure",
    "ExactModelTieRecord",
    "GenerationProtocol",
    "GenerationSourceHashes",
    "OpaqueIdFactory",
    "PromptSource",
    "StrictFrozenInterviewerRenderer",
    "build_candidate_arm_request",
    "generate_candidate_batch",
    "load_frozen_generation_contexts",
    "load_frozen_prompt_sources",
    "sha256_file",
    "validate_candidate_batch",
    "validate_private_output_path",
    "validate_real_generation_environment",
    "write_blocked_audit",
    "write_complete_batch",
]
