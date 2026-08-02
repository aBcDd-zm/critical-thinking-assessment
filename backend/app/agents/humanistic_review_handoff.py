from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.agents.humanistic_candidate_generation import (
    ArmKeyRecord,
    BlindReviewCase,
    CandidateGenerationManifest,
    CaseKeyRecord,
    ExactModelTieRecord,
    GenerationSourceHashes,
)


RECEIPT_SCHEMA_VERSION = "humanistic_candidate_generation_receipt_v1"
RATING_TEMPLATE_SCHEMA_VERSION = "blind_candidate_ratings_template_v1"
RECEIPT_TOOL_VERSION = "humanistic_review_handoff_v1"
EMPTY_FILE_SHA256 = hashlib.sha256(b"").hexdigest()
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")
SAFE_REVIEWER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_EXACT_TIES_BYTES = 2 * 1024 * 1024
MAX_BLIND_PACKET_BYTES = 8 * 1024 * 1024
MAX_RATINGS_BYTES = 8 * 1024 * 1024
MAX_SEALED_KEY_BYTES = 2 * 1024 * 1024

EXPECTED_COMPLETE_OUTPUTS = (
    "reviewer/blind_review_packet_v1.jsonl",
    "sealed/case_key_v1.jsonl",
    "sealed/arm_key_v1.jsonl",
    "sealed/candidate_provenance_v1.jsonl",
    "sealed/generation_failures_v1.jsonl",
    "sealed/exact_model_ties_v1.jsonl",
)
EXACT_TIES_OUTPUT = "sealed/exact_model_ties_v1.jsonl"
FAILURES_OUTPUT = "sealed/generation_failures_v1.jsonl"


class HandoffValidationError(ValueError):
    """Raised when private handoff evidence is not safe to publish or review."""


class ReceiptContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReceiptCounts(ReceiptContractModel):
    context_count: int = Field(gt=0)
    attempted_context_count: int = Field(gt=0)
    candidate_count: int = Field(gt=0)
    case_key_count: int = Field(gt=0)
    arm_key_count: int = Field(gt=0)
    provenance_count: int = Field(gt=0)
    failure_count: int = Field(ge=0)
    exact_model_tie_count: int = Field(ge=0)


class ReceiptModelIdentity(ReceiptContractModel):
    provider: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$",
    )
    model: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$",
    )


class ReceiptVerificationScope(ReceiptContractModel):
    manifest: Literal["schema_complete_and_counts_validated"] = (
        "schema_complete_and_counts_validated"
    )
    output_sha256: Literal["manifest_declared"] = "manifest_declared"
    exact_ties: Literal[
        "file_hash_and_records_validated",
        "manifest_declares_zero_and_empty_file_hash",
    ]


class ReceiptRedaction(ReceiptContractModel):
    contains_candidate_text: Literal[False] = False
    contains_case_or_arm_key_records: Literal[False] = False
    contains_provenance_records: Literal[False] = False


class CandidateGenerationReceipt(ReceiptContractModel):
    schema_version: Literal[
        "humanistic_candidate_generation_receipt_v1"
    ] = RECEIPT_SCHEMA_VERSION
    receipt_tool_version: Literal["humanistic_review_handoff_v1"] = (
        RECEIPT_TOOL_VERSION
    )
    receipt_status: Literal["VERIFIED_COMPLETE_MANIFEST"] = (
        "VERIFIED_COMPLETE_MANIFEST"
    )
    generation_status: Literal["complete"] = "complete"
    run_id: str = Field(pattern=r"^run_[0-9a-f]{32}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    counts: ReceiptCounts
    model_identity: ReceiptModelIdentity
    source_sha256: GenerationSourceHashes
    output_sha256: dict[str, str]
    verification_scope: ReceiptVerificationScope
    redaction: ReceiptRedaction = Field(default_factory=ReceiptRedaction)

    @model_validator(mode="after")
    def validate_public_output_hashes(self) -> "CandidateGenerationReceipt":
        _validate_hash_map(self.output_sha256)
        return self


class BlindRatingContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CompletedCandidateRating(BlindRatingContractModel):
    candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")
    naturalness: int = Field(ge=1, le=5)
    warmth: int = Field(ge=1, le=5)
    clarity: int = Field(ge=1, le=5)
    faithfulness_pass: bool
    non_leading_pass: bool
    single_question_pass: bool
    fact_whitelist_pass: bool
    reflection_basis_pass: bool
    hard_error_codes: list[
        Literal[
            "unsupported_hidden_meaning",
            "relational_attachment",
            "role_substitution",
            "fabricated_self_disclosure",
            "prescriptive_authority",
            "clinical_role_claim",
        ]
    ]

    @model_validator(mode="after")
    def validate_hard_error_codes(self) -> "CompletedCandidateRating":
        if len(self.hard_error_codes) != len(set(self.hard_error_codes)):
            raise ValueError("hard_error_codes must not contain duplicates")
        return self


class CompletedPairwisePreference(BlindRatingContractModel):
    candidate_ids: list[str] = Field(min_length=2, max_length=2)
    preferred_candidate_id: str = Field(pattern=r"^cand_[0-9a-f]{32}$")

    @model_validator(mode="after")
    def validate_pair(self) -> "CompletedPairwisePreference":
        if len(set(self.candidate_ids)) != 2:
            raise ValueError("pairwise candidate IDs must be unique")
        if any(
            re.fullmatch(r"^cand_[0-9a-f]{32}$", candidate_id) is None
            for candidate_id in self.candidate_ids
        ):
            raise ValueError("pairwise candidate ID format is invalid")
        if self.preferred_candidate_id not in self.candidate_ids:
            raise ValueError(
                "preferred_candidate_id must select one candidate in the pair"
            )
        return self


class CompletedBlindRatingRecord(BlindRatingContractModel):
    schema_version: Literal["blind_candidate_ratings_template_v1"]
    review_status: Literal["completed"]
    case_id: str = Field(pattern=r"^case_[0-9a-f]{32}$")
    reviewer_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
    candidate_ratings: list[CompletedCandidateRating] = Field(
        min_length=3,
        max_length=3,
    )
    pairwise_preferences: list[CompletedPairwisePreference] = Field(
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_complete_review(self) -> "CompletedBlindRatingRecord":
        candidate_ids = [
            rating.candidate_id for rating in self.candidate_ratings
        ]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate ratings must use three unique IDs")
        expected_pairs = {
            frozenset(pair)
            for pair in combinations(candidate_ids, 2)
        }
        actual_pairs = {
            frozenset(preference.candidate_ids)
            for preference in self.pairwise_preferences
        }
        if actual_pairs != expected_pairs or len(actual_pairs) != 3:
            raise ValueError(
                "pairwise preferences must cover all three candidate pairs once"
            )
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HandoffValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_schema_error(exc: Exception) -> str:
    if not isinstance(exc, ValidationError):
        return type(exc).__name__
    details: list[str] = []
    for error in exc.errors(include_input=False, include_url=False)[:5]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        details.append(f"{location}: {error['type']}")
    return "; ".join(details) or "validation_error"


def _read_bounded(path: Path, *, maximum_bytes: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise HandoffValidationError(f"{label} cannot be read: {exc}") from exc
    if not path.is_file():
        raise HandoffValidationError(f"{label} must be a regular file")
    if size > maximum_bytes:
        raise HandoffValidationError(
            f"{label} exceeds the {maximum_bytes}-byte safety limit"
        )
    try:
        return path.read_bytes()
    except OSError as exc:
        raise HandoffValidationError(f"{label} cannot be read: {exc}") from exc


def _decode_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffValidationError(f"{label} must be UTF-8") from exc
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except HandoffValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise HandoffValidationError(
            f"{label} is not valid JSON: line {exc.lineno}, column {exc.colno}"
        ) from exc


def _decode_jsonl(raw: bytes, *, label: str) -> list[Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HandoffValidationError(f"{label} must be UTF-8") from exc
    if not text:
        return []
    records: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise HandoffValidationError(
                f"{label} line {line_number} must not be blank"
            )
        try:
            records.append(
                json.loads(line, object_pairs_hook=_reject_duplicate_keys)
            )
        except HandoffValidationError:
            raise
        except json.JSONDecodeError as exc:
            raise HandoffValidationError(
                f"{label} line {line_number} is not valid JSON: "
                f"column {exc.colno}"
            ) from exc
    return records


def _resolve_external_input(
    path: Path,
    *,
    repo_root: Path,
    label: str,
) -> Path:
    try:
        resolved_repo = repo_root.expanduser().resolve(strict=True)
        resolved_path = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise HandoffValidationError(f"{label} path cannot be resolved: {exc}") from exc
    if resolved_path == resolved_repo or resolved_path.is_relative_to(resolved_repo):
        raise HandoffValidationError(
            f"{label} must remain outside the Git repository"
        )
    if not resolved_path.is_file():
        raise HandoffValidationError(f"{label} must be a regular file")
    return resolved_path


def _validate_hash_map(
    output_sha256: dict[str, str],
) -> dict[str, str]:
    if set(output_sha256) != set(EXPECTED_COMPLETE_OUTPUTS):
        missing = sorted(set(EXPECTED_COMPLETE_OUTPUTS) - set(output_sha256))
        extra = sorted(set(output_sha256) - set(EXPECTED_COMPLETE_OUTPUTS))
        raise HandoffValidationError(
            "generation manifest output_sha256 must contain the exact formal "
            f"output set; missing={missing}, extra={extra}"
        )
    if any(not SHA256_PATTERN.fullmatch(value) for value in output_sha256.values()):
        raise HandoffValidationError(
            "generation manifest output_sha256 contains an invalid SHA-256"
        )
    return {
        relative_path: output_sha256[relative_path]
        for relative_path in EXPECTED_COMPLETE_OUTPUTS
    }


def _validate_receipt_counts(
    receipt: CandidateGenerationReceipt,
) -> None:
    counts = receipt.counts
    if counts.attempted_context_count != counts.context_count:
        raise HandoffValidationError(
            "generation receipt must cover every attempted context"
        )
    if counts.candidate_count != counts.context_count * 3:
        raise HandoffValidationError(
            "generation receipt must declare three candidates per context"
        )
    if counts.case_key_count != counts.context_count:
        raise HandoffValidationError(
            "generation receipt must declare one case key per context"
        )
    if counts.arm_key_count != counts.context_count:
        raise HandoffValidationError(
            "generation receipt must declare one arm key per context"
        )
    if counts.provenance_count < counts.candidate_count:
        raise HandoffValidationError(
            "generation receipt provenance count is incomplete"
        )
    if counts.exact_model_tie_count > counts.context_count:
        raise HandoffValidationError(
            "generation receipt exact-tie count exceeds context count"
        )
    exact_tie_hash = receipt.output_sha256[EXACT_TIES_OUTPUT]
    if (
        counts.exact_model_tie_count == 0
        and exact_tie_hash != EMPTY_FILE_SHA256
    ):
        raise HandoffValidationError(
            "zero receipt exact ties require the empty-file SHA-256"
        )
    if (
        counts.exact_model_tie_count > 0
        and exact_tie_hash == EMPTY_FILE_SHA256
    ):
        raise HandoffValidationError(
            "non-zero receipt exact ties cannot use the empty-file SHA-256"
        )
    if (
        counts.exact_model_tie_count > 0
        and receipt.verification_scope.exact_ties
        != "file_hash_and_records_validated"
    ):
        raise HandoffValidationError(
            "non-zero receipt exact ties require validated file evidence"
        )
    failure_hash = receipt.output_sha256[FAILURES_OUTPUT]
    if counts.failure_count == 0 and failure_hash != EMPTY_FILE_SHA256:
        raise HandoffValidationError(
            "zero receipt failures require the empty-file SHA-256"
        )
    if counts.failure_count > 0 and failure_hash == EMPTY_FILE_SHA256:
        raise HandoffValidationError(
            "non-zero receipt failures cannot use the empty-file SHA-256"
        )
    required_non_empty_outputs = (
        "reviewer/blind_review_packet_v1.jsonl",
        "sealed/case_key_v1.jsonl",
        "sealed/arm_key_v1.jsonl",
        "sealed/candidate_provenance_v1.jsonl",
    )
    if any(
        receipt.output_sha256[relative_path] == EMPTY_FILE_SHA256
        for relative_path in required_non_empty_outputs
    ):
        raise HandoffValidationError(
            "generation receipt declares an empty required output"
        )


def load_generation_receipt(
    receipt_path: Path,
) -> CandidateGenerationReceipt:
    try:
        resolved_path = receipt_path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise HandoffValidationError(
            f"generation receipt path cannot be resolved: {exc}"
        ) from exc
    raw = _read_bounded(
        resolved_path,
        maximum_bytes=MAX_RECEIPT_BYTES,
        label="generation receipt",
    )
    payload = _decode_json(raw, label="generation receipt")
    try:
        receipt = CandidateGenerationReceipt.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise HandoffValidationError(
            "generation receipt violates schema "
            f"({_safe_schema_error(exc)})"
        ) from exc
    _validate_receipt_counts(receipt)
    return receipt


def _validate_complete_manifest(
    manifest: CandidateGenerationManifest,
    *,
    expected_context_count: int,
) -> dict[str, str]:
    if manifest.status != "complete":
        raise HandoffValidationError(
            "generation receipt requires manifest status=complete"
        )
    if manifest.context_count != expected_context_count:
        raise HandoffValidationError(
            "generation manifest context_count mismatch: "
            f"expected {expected_context_count}, got {manifest.context_count}"
        )
    if manifest.attempted_context_count != manifest.context_count:
        raise HandoffValidationError(
            "complete generation must attempt every context"
        )
    if manifest.candidate_count != manifest.context_count * 3:
        raise HandoffValidationError(
            "complete generation must contain exactly three candidates per context"
        )
    if manifest.case_key_count != manifest.context_count:
        raise HandoffValidationError(
            "complete generation must contain one case-key record per context"
        )
    if manifest.arm_key_count != manifest.context_count:
        raise HandoffValidationError(
            "complete generation must contain one arm-key record per context"
        )
    if manifest.provenance_count < manifest.candidate_count:
        raise HandoffValidationError(
            "complete generation provenance cannot cover fewer records than candidates"
        )
    if manifest.exact_model_tie_count > manifest.context_count:
        raise HandoffValidationError(
            "exact-model-tie count cannot exceed context count"
        )
    if not SAFE_MODEL_ID_PATTERN.fullmatch(manifest.protocol.provider):
        raise HandoffValidationError(
            "generation manifest provider is not a safe public model identifier"
        )
    if not SAFE_MODEL_ID_PATTERN.fullmatch(manifest.protocol.model):
        raise HandoffValidationError(
            "generation manifest model is not a safe public model identifier"
        )
    output_sha256 = _validate_hash_map(manifest.output_sha256)
    if (
        manifest.exact_model_tie_count == 0
        and output_sha256[EXACT_TIES_OUTPUT] != EMPTY_FILE_SHA256
    ):
        raise HandoffValidationError(
            "zero exact ties require the generated empty-file SHA-256"
        )
    if (
        manifest.failure_count == 0
        and output_sha256[FAILURES_OUTPUT] != EMPTY_FILE_SHA256
    ):
        raise HandoffValidationError(
            "zero failures require the generated empty-file SHA-256"
        )
    if (
        manifest.failure_count > 0
        and output_sha256[FAILURES_OUTPUT] == EMPTY_FILE_SHA256
    ):
        raise HandoffValidationError(
            "non-zero failures cannot use the empty-file SHA-256"
        )
    required_non_empty_outputs = (
        "reviewer/blind_review_packet_v1.jsonl",
        "sealed/case_key_v1.jsonl",
        "sealed/arm_key_v1.jsonl",
        "sealed/candidate_provenance_v1.jsonl",
    )
    if any(
        output_sha256[relative_path] == EMPTY_FILE_SHA256
        for relative_path in required_non_empty_outputs
    ):
        raise HandoffValidationError(
            "complete generation cannot declare an empty required output"
        )
    return output_sha256


def _validate_exact_ties(
    *,
    manifest: CandidateGenerationManifest,
    exact_ties_path: Path | None,
    repo_root: Path,
    output_sha256: dict[str, str],
) -> Literal[
    "file_hash_and_records_validated",
    "manifest_declares_zero_and_empty_file_hash",
]:
    expected_count = manifest.exact_model_tie_count
    if exact_ties_path is None:
        if expected_count:
            raise HandoffValidationError(
                "sealed exact-model-tie evidence is required when the "
                "manifest declares one or more exact ties"
            )
        return "manifest_declares_zero_and_empty_file_hash"

    resolved_path = _resolve_external_input(
        exact_ties_path,
        repo_root=repo_root,
        label="sealed exact-model-tie file",
    )
    raw = _read_bounded(
        resolved_path,
        maximum_bytes=MAX_EXACT_TIES_BYTES,
        label="sealed exact-model-tie file",
    )
    if hashlib.sha256(raw).hexdigest() != output_sha256[EXACT_TIES_OUTPUT]:
        raise HandoffValidationError(
            "sealed exact-model-tie file SHA-256 differs from the manifest"
        )
    payloads = _decode_jsonl(raw, label="sealed exact-model-tie file")
    records: list[ExactModelTieRecord] = []
    for line_number, payload in enumerate(payloads, start=1):
        try:
            records.append(ExactModelTieRecord.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(
                "sealed exact-model-tie line "
                f"{line_number} violates schema "
                f"({_safe_schema_error(exc)})"
            ) from exc
    if len(records) != expected_count:
        raise HandoffValidationError(
            "sealed exact-model-tie record count differs from the manifest: "
            f"expected {expected_count}, got {len(records)}"
        )
    if any(record.run_id != manifest.run_id for record in records):
        raise HandoffValidationError(
            "sealed exact-model-tie record belongs to a different run"
        )
    case_ids = [record.case_id for record in records]
    context_ids = [record.context_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise HandoffValidationError(
            "sealed exact-model-tie case IDs must be unique"
        )
    if len(context_ids) != len(set(context_ids)):
        raise HandoffValidationError(
            "sealed exact-model-tie context IDs must be unique"
        )
    return "file_hash_and_records_validated"


def build_generation_receipt(
    *,
    manifest_path: Path,
    exact_ties_path: Path | None,
    repo_root: Path,
    expected_context_count: int = 48,
) -> CandidateGenerationReceipt:
    if expected_context_count <= 0:
        raise HandoffValidationError("expected context count must be positive")
    resolved_manifest = _resolve_external_input(
        manifest_path,
        repo_root=repo_root,
        label="candidate-generation manifest",
    )
    raw_manifest = _read_bounded(
        resolved_manifest,
        maximum_bytes=MAX_MANIFEST_BYTES,
        label="candidate-generation manifest",
    )
    payload = _decode_json(raw_manifest, label="candidate-generation manifest")
    try:
        manifest = CandidateGenerationManifest.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise HandoffValidationError(
            "candidate-generation manifest violates schema "
            f"({_safe_schema_error(exc)})"
        ) from exc
    output_sha256 = _validate_complete_manifest(
        manifest,
        expected_context_count=expected_context_count,
    )
    exact_tie_scope = _validate_exact_ties(
        manifest=manifest,
        exact_ties_path=exact_ties_path,
        repo_root=repo_root,
        output_sha256=output_sha256,
    )
    return CandidateGenerationReceipt(
        run_id=manifest.run_id,
        manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
        counts=ReceiptCounts(
            context_count=manifest.context_count,
            attempted_context_count=manifest.attempted_context_count,
            candidate_count=manifest.candidate_count,
            case_key_count=manifest.case_key_count,
            arm_key_count=manifest.arm_key_count,
            provenance_count=manifest.provenance_count,
            failure_count=manifest.failure_count,
            exact_model_tie_count=manifest.exact_model_tie_count,
        ),
        model_identity=ReceiptModelIdentity(
            provider=manifest.protocol.provider,
            model=manifest.protocol.model,
        ),
        source_sha256=manifest.source_hashes.model_dump(mode="json"),
        output_sha256=output_sha256,
        verification_scope=ReceiptVerificationScope(exact_ties=exact_tie_scope),
    )


def _exclusive_write(path: Path, content: bytes, *, mode: int) -> None:
    resolved_path = path.expanduser().resolve(strict=False)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            resolved_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            mode,
        )
    except FileExistsError:
        raise
    except OSError as exc:
        raise HandoffValidationError(f"output cannot be created: {exc}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        resolved_path.chmod(mode)
    except Exception:
        try:
            resolved_path.unlink(missing_ok=True)
        finally:
            raise


def write_generation_receipt(
    receipt: CandidateGenerationReceipt,
    output_path: Path,
) -> None:
    content = (
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    _exclusive_write(output_path, content, mode=0o644)


def _rating_template_record(
    case: BlindReviewCase,
    *,
    reviewer_id: str,
) -> dict[str, Any]:
    candidate_ids = [candidate.candidate_id for candidate in case.candidates]
    return {
        "schema_version": RATING_TEMPLATE_SCHEMA_VERSION,
        "review_status": "blank",
        "case_id": case.case_id,
        "reviewer_id": reviewer_id,
        "candidate_ratings": [
            {
                "candidate_id": candidate_id,
                "naturalness": None,
                "warmth": None,
                "clarity": None,
                "faithfulness_pass": None,
                "non_leading_pass": None,
                "single_question_pass": None,
                "fact_whitelist_pass": None,
                "reflection_basis_pass": None,
                "hard_error_codes": None,
            }
            for candidate_id in candidate_ids
        ],
        "pairwise_preferences": [
            {
                "candidate_ids": [left_id, right_id],
                "preferred_candidate_id": None,
            }
            for left_id, right_id in combinations(candidate_ids, 2)
        ],
    }


def build_blank_rating_templates(
    *,
    packet_path: Path,
    receipt_path: Path,
    reviewer_id: str,
    repo_root: Path,
) -> list[dict[str, Any]]:
    if not SAFE_REVIEWER_ID_PATTERN.fullmatch(reviewer_id):
        raise HandoffValidationError(
            "reviewer_id must be a 3-64 character pseudonym using only "
            "letters, digits, dot, underscore, or hyphen"
        )
    receipt = load_generation_receipt(receipt_path)
    cases = _load_blind_cases(
        packet_path=packet_path,
        receipt=receipt,
        repo_root=repo_root,
    )
    return [
        _rating_template_record(case, reviewer_id=reviewer_id)
        for case in cases
    ]


def _load_blind_cases(
    *,
    packet_path: Path,
    receipt: CandidateGenerationReceipt,
    repo_root: Path,
) -> list[BlindReviewCase]:
    resolved_packet = _resolve_external_input(
        packet_path,
        repo_root=repo_root,
        label="blind review packet",
    )
    raw_packet = _read_bounded(
        resolved_packet,
        maximum_bytes=MAX_BLIND_PACKET_BYTES,
        label="blind review packet",
    )
    packet_sha256 = hashlib.sha256(raw_packet).hexdigest()
    expected_packet_sha256 = receipt.output_sha256[
        "reviewer/blind_review_packet_v1.jsonl"
    ]
    if packet_sha256 != expected_packet_sha256:
        raise HandoffValidationError(
            "blind review packet SHA-256 differs from the generation receipt"
        )
    payloads = _decode_jsonl(raw_packet, label="blind review packet")
    cases: list[BlindReviewCase] = []
    for line_number, payload in enumerate(payloads, start=1):
        try:
            cases.append(BlindReviewCase.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(
                "blind review packet line "
                f"{line_number} violates schema "
                f"({_safe_schema_error(exc)})"
            ) from exc
    expected_case_count = receipt.counts.context_count
    if len(cases) != expected_case_count:
        raise HandoffValidationError(
            "blind review packet case count mismatch: "
            f"expected {expected_case_count}, got {len(cases)}"
        )
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise HandoffValidationError(
            "blind review packet case IDs must be globally unique"
        )
    candidate_ids = [
        candidate.candidate_id
        for case in cases
        for candidate in case.candidates
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HandoffValidationError(
            "blind review packet candidate IDs must be globally unique"
        )
    return cases


def write_blank_rating_templates(
    templates: list[dict[str, Any]],
    output_path: Path,
    *,
    repo_root: Path,
) -> None:
    try:
        resolved_repo = repo_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise HandoffValidationError(
            f"repository root cannot be resolved: {exc}"
        ) from exc
    resolved_output = output_path.expanduser().resolve(strict=False)
    if resolved_output == resolved_repo or resolved_output.is_relative_to(
        resolved_repo
    ):
        raise HandoffValidationError(
            "blank rating templates must remain outside the Git repository"
        )
    _assert_arm_blind_templates(templates)
    content = (
        "".join(
            json.dumps(
                record,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
            for record in templates
        )
    ).encode("utf-8")
    _exclusive_write(resolved_output, content, mode=0o600)


def _assert_arm_blind_templates(templates: list[dict[str, Any]]) -> None:
    if not templates:
        raise HandoffValidationError(
            "blank rating templates must contain at least one case"
        )
    forbidden_keys = {
        "arm",
        "assignments",
        "candidate_text",
        "context_id",
        "model",
        "prompt",
        "prompt_version",
        "provenance",
        "review_context",
        "split",
        "style_version",
    }

    def visit(value: Any, *, path: str) -> None:
        if isinstance(value, dict):
            leaked = forbidden_keys.intersection(value)
            if leaked:
                raise HandoffValidationError(
                    f"{path} contains forbidden blind field(s): "
                    + ", ".join(sorted(leaked))
                )
            for key, child in value.items():
                visit(child, path=f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path=f"{path}[{index}]")

    visit(templates, path="rating templates")


def _load_completed_ratings(
    *,
    ratings_paths: list[Path],
    repo_root: Path,
) -> list[CompletedBlindRatingRecord]:
    if not ratings_paths:
        raise HandoffValidationError(
            "at least one frozen reviewer-ratings file is required"
        )
    resolved_paths: list[Path] = []
    records: list[CompletedBlindRatingRecord] = []
    for file_index, path in enumerate(ratings_paths, start=1):
        resolved_path = _resolve_external_input(
            path,
            repo_root=repo_root,
            label=f"frozen reviewer-ratings file {file_index}",
        )
        if resolved_path in resolved_paths:
            raise HandoffValidationError(
                "the same frozen reviewer-ratings file was supplied more than once"
            )
        resolved_paths.append(resolved_path)
        raw = _read_bounded(
            resolved_path,
            maximum_bytes=MAX_RATINGS_BYTES,
            label=f"frozen reviewer-ratings file {file_index}",
        )
        payloads = _decode_jsonl(
            raw,
            label=f"frozen reviewer-ratings file {file_index}",
        )
        for line_number, payload in enumerate(payloads, start=1):
            try:
                records.append(
                    CompletedBlindRatingRecord.model_validate(payload)
                )
            except Exception as exc:  # noqa: BLE001
                raise HandoffValidationError(
                    "frozen reviewer-ratings file "
                    f"{file_index} line {line_number} violates schema "
                    f"({_safe_schema_error(exc)})"
                ) from exc
    if not records:
        raise HandoffValidationError("frozen reviewer ratings contain no records")
    return records


def _load_case_key(
    *,
    path: Path,
    repo_root: Path,
    expected_context_count: int,
    expected_sha256: str,
) -> list[CaseKeyRecord]:
    resolved_path = _resolve_external_input(
        path,
        repo_root=repo_root,
        label="sealed case-key file",
    )
    raw = _read_bounded(
        resolved_path,
        maximum_bytes=MAX_SEALED_KEY_BYTES,
        label="sealed case-key file",
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise HandoffValidationError(
            "sealed case-key SHA-256 differs from the generation receipt"
        )
    payloads = _decode_jsonl(raw, label="sealed case-key file")
    records: list[CaseKeyRecord] = []
    for line_number, payload in enumerate(payloads, start=1):
        try:
            records.append(CaseKeyRecord.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(
                f"sealed case-key line {line_number} violates schema "
                f"({_safe_schema_error(exc)})"
            ) from exc
    if len(records) != expected_context_count:
        raise HandoffValidationError(
            "sealed case-key coverage mismatch: "
            f"expected {expected_context_count}, got {len(records)}"
        )
    case_ids = [record.case_id for record in records]
    context_ids = [record.context_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise HandoffValidationError("sealed case-key case IDs must be unique")
    if len(context_ids) != len(set(context_ids)):
        raise HandoffValidationError(
            "sealed case-key context IDs must be unique"
        )
    return records


def _load_arm_key(
    *,
    path: Path,
    repo_root: Path,
    expected_context_count: int,
    expected_sha256: str,
) -> list[ArmKeyRecord]:
    resolved_path = _resolve_external_input(
        path,
        repo_root=repo_root,
        label="sealed arm-key file",
    )
    raw = _read_bounded(
        resolved_path,
        maximum_bytes=MAX_SEALED_KEY_BYTES,
        label="sealed arm-key file",
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise HandoffValidationError(
            "sealed arm-key SHA-256 differs from the generation receipt"
        )
    payloads = _decode_jsonl(raw, label="sealed arm-key file")
    records: list[ArmKeyRecord] = []
    for line_number, payload in enumerate(payloads, start=1):
        try:
            records.append(ArmKeyRecord.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(
                f"sealed arm-key line {line_number} violates schema "
                f"({_safe_schema_error(exc)})"
            ) from exc
    if len(records) != expected_context_count:
        raise HandoffValidationError(
            "sealed arm-key coverage mismatch: "
            f"expected {expected_context_count}, got {len(records)}"
        )
    case_ids = [record.case_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise HandoffValidationError("sealed arm-key case IDs must be unique")
    candidate_ids = [
        assignment.candidate_id
        for record in records
        for assignment in record.assignments
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HandoffValidationError(
            "sealed arm-key candidate IDs must be globally unique"
        )
    return records


def _load_optional_exact_ties(
    *,
    path: Path | None,
    repo_root: Path,
    expected_count: int,
    expected_run_id: str,
    expected_sha256: str,
) -> list[ExactModelTieRecord]:
    if path is None:
        if expected_count > 0:
            raise HandoffValidationError(
                "the generation receipt requires a sealed exact-model-tie file"
            )
        return []
    resolved_path = _resolve_external_input(
        path,
        repo_root=repo_root,
        label="sealed exact-model-tie file",
    )
    raw = _read_bounded(
        resolved_path,
        maximum_bytes=MAX_EXACT_TIES_BYTES,
        label="sealed exact-model-tie file",
    )
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise HandoffValidationError(
            "sealed exact-model-tie SHA-256 differs from the generation receipt"
        )
    payloads = _decode_jsonl(raw, label="sealed exact-model-tie file")
    records: list[ExactModelTieRecord] = []
    for line_number, payload in enumerate(payloads, start=1):
        try:
            records.append(ExactModelTieRecord.model_validate(payload))
        except Exception as exc:  # noqa: BLE001
            raise HandoffValidationError(
                "sealed exact-model-tie line "
                f"{line_number} violates schema "
                f"({_safe_schema_error(exc)})"
            ) from exc
    case_ids = [record.case_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise HandoffValidationError(
            "sealed exact-model-tie case IDs must be unique"
        )
    if len({record.run_id for record in records}) > 1:
        raise HandoffValidationError(
            "sealed exact-model-tie records must belong to one run"
        )
    if len(records) != expected_count:
        raise HandoffValidationError(
            "sealed exact-model-tie count differs from the generation receipt: "
            f"expected {expected_count}, got {len(records)}"
        )
    if any(record.run_id != expected_run_id for record in records):
        raise HandoffValidationError(
            "sealed exact-model-tie run differs from the generation receipt"
        )
    return records


def _rating_comparison_payload(
    rating: CompletedCandidateRating,
) -> tuple[Any, ...]:
    return (
        rating.naturalness,
        rating.warmth,
        rating.clarity,
        rating.faithfulness_pass,
        rating.non_leading_pass,
        rating.single_question_pass,
        rating.fact_whitelist_pass,
        rating.reflection_basis_pass,
        tuple(sorted(rating.hard_error_codes)),
    )


def build_unblinded_evaluator_ratings(
    *,
    ratings_paths: list[Path],
    receipt_path: Path,
    case_key_path: Path,
    arm_key_path: Path,
    exact_ties_path: Path | None,
    repo_root: Path,
) -> list[dict[str, Any]]:
    receipt = load_generation_receipt(receipt_path)
    expected_context_count = receipt.counts.context_count
    ratings = _load_completed_ratings(
        ratings_paths=ratings_paths,
        repo_root=repo_root,
    )
    case_key = _load_case_key(
        path=case_key_path,
        repo_root=repo_root,
        expected_context_count=expected_context_count,
        expected_sha256=receipt.output_sha256[
            "sealed/case_key_v1.jsonl"
        ],
    )
    arm_key = _load_arm_key(
        path=arm_key_path,
        repo_root=repo_root,
        expected_context_count=expected_context_count,
        expected_sha256=receipt.output_sha256[
            "sealed/arm_key_v1.jsonl"
        ],
    )
    exact_ties = _load_optional_exact_ties(
        path=exact_ties_path,
        repo_root=repo_root,
        expected_count=receipt.counts.exact_model_tie_count,
        expected_run_id=receipt.run_id,
        expected_sha256=receipt.output_sha256[EXACT_TIES_OUTPUT],
    )

    case_keys_by_id = {record.case_id: record for record in case_key}
    arm_keys_by_id = {record.case_id: record for record in arm_key}
    if set(case_keys_by_id) != set(arm_keys_by_id):
        missing = sorted(set(case_keys_by_id) - set(arm_keys_by_id))
        extra = sorted(set(arm_keys_by_id) - set(case_keys_by_id))
        raise HandoffValidationError(
            "sealed case/arm key coverage differs; "
            f"missing_arm_key={missing}, unknown_arm_key={extra}"
        )

    reviews_by_case: dict[str, list[CompletedBlindRatingRecord]] = {}
    seen_reviews: set[tuple[str, str]] = set()
    for record in ratings:
        if record.case_id not in case_keys_by_id:
            raise HandoffValidationError(
                f"frozen ratings contain unknown case_id {record.case_id}"
            )
        review_key = (record.case_id, record.reviewer_id)
        if review_key in seen_reviews:
            raise HandoffValidationError(
                "duplicate frozen review by "
                f"{record.reviewer_id} for {record.case_id}"
            )
        seen_reviews.add(review_key)
        reviews_by_case.setdefault(record.case_id, []).append(record)

    missing_rating_cases = sorted(set(case_keys_by_id) - set(reviews_by_case))
    if missing_rating_cases:
        raise HandoffValidationError(
            "frozen ratings do not cover all sealed cases; "
            f"missing={missing_rating_cases}"
        )
    insufficient_reviewers = {
        case_id: len({record.reviewer_id for record in case_reviews})
        for case_id, case_reviews in reviews_by_case.items()
        if len({record.reviewer_id for record in case_reviews}) < 2
    }
    if insufficient_reviewers:
        raise HandoffValidationError(
            "each sealed case requires at least two independent reviewers; "
            f"insufficient={dict(sorted(insufficient_reviewers.items()))}"
        )

    assignments_by_case: dict[str, dict[str, str]] = {
        record.case_id: {
            assignment.candidate_id: assignment.arm
            for assignment in record.assignments
        }
        for record in arm_key
    }
    for case_id, case_reviews in reviews_by_case.items():
        expected_candidate_ids = set(assignments_by_case[case_id])
        for review in case_reviews:
            rated_candidate_ids = {
                rating.candidate_id for rating in review.candidate_ratings
            }
            if rated_candidate_ids != expected_candidate_ids:
                raise HandoffValidationError(
                    "frozen rating candidate set differs from sealed arm key "
                    f"for {case_id} reviewer {review.reviewer_id}"
                )

    for tie in exact_ties:
        case_record = case_keys_by_id.get(tie.case_id)
        if case_record is None:
            raise HandoffValidationError(
                f"sealed exact tie uses unknown case_id {tie.case_id}"
            )
        if (
            tie.context_id != case_record.context_id
            or tie.split != case_record.split
        ):
            raise HandoffValidationError(
                f"sealed exact tie differs from case key for {tie.case_id}"
            )
        assignments = assignments_by_case[tie.case_id]
        expected_model_pair = {
            candidate_id
            for candidate_id, arm in assignments.items()
            if arm in {"baseline", "humanistic"}
        }
        if set(tie.candidate_ids) != expected_model_pair:
            raise HandoffValidationError(
                f"sealed exact tie model pair differs from arm key for {tie.case_id}"
            )
        expected_fallback = next(
            candidate_id
            for candidate_id, arm in assignments.items()
            if arm == "fallback"
        )
        if tie.fallback_candidate_id != expected_fallback:
            raise HandoffValidationError(
                f"sealed exact tie fallback differs from arm key for {tie.case_id}"
            )
        for review in reviews_by_case[tie.case_id]:
            ratings_by_candidate = {
                rating.candidate_id: rating
                for rating in review.candidate_ratings
            }
            left, right = (
                ratings_by_candidate[candidate_id]
                for candidate_id in tie.candidate_ids
            )
            if _rating_comparison_payload(left) != _rating_comparison_payload(
                right
            ):
                raise HandoffValidationError(
                    "exact-tie candidates require identical frozen ratings "
                    f"for {tie.case_id} reviewer {review.reviewer_id}"
                )

    evaluator_records: list[dict[str, Any]] = []
    for case_id, case_reviews in reviews_by_case.items():
        case_record = case_keys_by_id[case_id]
        assignments = assignments_by_case[case_id]
        baseline_id = next(
            candidate_id
            for candidate_id, arm in assignments.items()
            if arm == "baseline"
        )
        humanistic_id = next(
            candidate_id
            for candidate_id, arm in assignments.items()
            if arm == "humanistic"
        )
        target_pair = frozenset((baseline_id, humanistic_id))
        for review in case_reviews:
            preference_by_pair = {
                frozenset(preference.candidate_ids): (
                    preference.preferred_candidate_id
                )
                for preference in review.pairwise_preferences
            }
            preferred_candidate_id = preference_by_pair.get(target_pair)
            if preferred_candidate_id is None:
                raise HandoffValidationError(
                    "frozen pairwise preferences do not contain the sealed "
                    f"baseline-humanistic pair for {case_id}"
                )
            evaluator_records.append(
                {
                    "context_id": case_record.context_id,
                    "reviewer_id": review.reviewer_id,
                    "candidate_ratings": [
                        {
                            **rating.model_dump(mode="json"),
                            "hard_error_codes": sorted(
                                rating.hard_error_codes
                            ),
                        }
                        for rating in review.candidate_ratings
                    ],
                    "baseline_humanistic_preference": (
                        preferred_candidate_id
                    ),
                }
            )
    evaluator_records.sort(
        key=lambda record: (record["context_id"], record["reviewer_id"])
    )
    _assert_evaluator_ratings_contain_no_arm(evaluator_records)
    return evaluator_records


def _assert_evaluator_ratings_contain_no_arm(
    records: list[dict[str, Any]],
) -> None:
    if not records:
        raise HandoffValidationError(
            "evaluator ratings must contain at least one review"
        )
    expected_record_keys = {
        "context_id",
        "reviewer_id",
        "candidate_ratings",
        "baseline_humanistic_preference",
    }
    expected_rating_keys = {
        "candidate_id",
        "naturalness",
        "warmth",
        "clarity",
        "faithfulness_pass",
        "non_leading_pass",
        "single_question_pass",
        "fact_whitelist_pass",
        "reflection_basis_pass",
        "hard_error_codes",
    }
    for index, record in enumerate(records, start=1):
        if set(record) != expected_record_keys:
            raise HandoffValidationError(
                f"evaluator rating {index} contains unexpected fields"
            )
        raw_ratings = record.get("candidate_ratings")
        if (
            not isinstance(raw_ratings, list)
            or len(raw_ratings) != 3
            or any(
                not isinstance(rating, dict)
                or set(rating) != expected_rating_keys
                for rating in raw_ratings
            )
        ):
            raise HandoffValidationError(
                f"evaluator rating {index} candidate fields are invalid"
            )
        if "arm" in record or any("arm" in rating for rating in raw_ratings):
            raise HandoffValidationError(
                f"evaluator rating {index} must not contain arm fields"
            )


def write_unblinded_evaluator_ratings(
    records: list[dict[str, Any]],
    output_path: Path,
    *,
    repo_root: Path,
) -> None:
    try:
        resolved_repo = repo_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise HandoffValidationError(
            f"repository root cannot be resolved: {exc}"
        ) from exc
    resolved_output = output_path.expanduser().resolve(strict=False)
    if resolved_output == resolved_repo or resolved_output.is_relative_to(
        resolved_repo
    ):
        raise HandoffValidationError(
            "unblinded evaluator ratings must remain outside the Git repository"
        )
    _assert_evaluator_ratings_contain_no_arm(records)
    content = "".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for record in records
    ).encode("utf-8")
    _exclusive_write(resolved_output, content, mode=0o600)


@dataclass(frozen=True)
class EvaluatorInputBundle:
    run_id: str
    context_count: int
    independent_review_count: int
    candidate_packet: list[dict[str, Any]]
    ratings: list[dict[str, Any]]
    arm_key: list[dict[str, Any]]
    input_sha256: dict[str, Any]


def build_evaluator_input_bundle(
    *,
    packet_path: Path,
    ratings_paths: list[Path],
    receipt_path: Path,
    case_key_path: Path,
    arm_key_path: Path,
    exact_ties_path: Path | None,
    repo_root: Path,
) -> EvaluatorInputBundle:
    receipt = load_generation_receipt(receipt_path)
    cases = _load_blind_cases(
        packet_path=packet_path,
        receipt=receipt,
        repo_root=repo_root,
    )
    evaluator_ratings = build_unblinded_evaluator_ratings(
        ratings_paths=ratings_paths,
        receipt_path=receipt_path,
        case_key_path=case_key_path,
        arm_key_path=arm_key_path,
        exact_ties_path=exact_ties_path,
        repo_root=repo_root,
    )
    case_key = _load_case_key(
        path=case_key_path,
        repo_root=repo_root,
        expected_context_count=receipt.counts.context_count,
        expected_sha256=receipt.output_sha256[
            "sealed/case_key_v1.jsonl"
        ],
    )
    arm_key = _load_arm_key(
        path=arm_key_path,
        repo_root=repo_root,
        expected_context_count=receipt.counts.context_count,
        expected_sha256=receipt.output_sha256[
            "sealed/arm_key_v1.jsonl"
        ],
    )
    case_key_by_id = {record.case_id: record for record in case_key}
    arm_key_by_id = {record.case_id: record for record in arm_key}
    blind_case_by_id = {record.case_id: record for record in cases}
    if (
        set(blind_case_by_id) != set(case_key_by_id)
        or set(blind_case_by_id) != set(arm_key_by_id)
    ):
        raise HandoffValidationError(
            "blind packet and sealed case/arm keys must cover the same cases"
        )

    candidate_packet: list[dict[str, Any]] = []
    evaluator_arm_key: list[dict[str, Any]] = []
    for case in cases:
        case_record = case_key_by_id[case.case_id]
        arm_record = arm_key_by_id[case.case_id]
        blind_candidate_ids = {
            candidate.candidate_id for candidate in case.candidates
        }
        keyed_candidate_ids = {
            assignment.candidate_id
            for assignment in arm_record.assignments
        }
        if blind_candidate_ids != keyed_candidate_ids:
            raise HandoffValidationError(
                "blind packet candidate set differs from sealed arm key "
                f"for {case.case_id}"
            )
        candidate_packet.append(
            {
                "context_id": case_record.context_id,
                "candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in case.candidates
                ],
            }
        )
        evaluator_arm_key.append(
            {
                "context_id": case_record.context_id,
                "assignments": [
                    assignment.model_dump(mode="json")
                    for assignment in arm_record.assignments
                ],
            }
        )
    candidate_packet.sort(key=lambda record: record["context_id"])
    evaluator_arm_key.sort(key=lambda record: record["context_id"])

    input_sha256: dict[str, Any] = {
        "generation_receipt_sha256": sha256_file(receipt_path),
        "blind_review_packet_sha256": sha256_file(packet_path),
        "reviewer_ratings_sha256": [
            sha256_file(path) for path in ratings_paths
        ],
        "case_key_sha256": sha256_file(case_key_path),
        "arm_key_sha256": sha256_file(arm_key_path),
        "exact_ties_sha256": (
            sha256_file(exact_ties_path)
            if exact_ties_path is not None
            else None
        ),
    }
    bound_hashes = {
        "blind_review_packet_sha256": receipt.output_sha256[
            "reviewer/blind_review_packet_v1.jsonl"
        ],
        "case_key_sha256": receipt.output_sha256[
            "sealed/case_key_v1.jsonl"
        ],
        "arm_key_sha256": receipt.output_sha256[
            "sealed/arm_key_v1.jsonl"
        ],
    }
    for field, expected_sha256 in bound_hashes.items():
        if input_sha256[field] != expected_sha256:
            raise HandoffValidationError(
                f"{field} changed during evaluator-input preparation"
            )
    if (
        exact_ties_path is not None
        and input_sha256["exact_ties_sha256"]
        != receipt.output_sha256[EXACT_TIES_OUTPUT]
    ):
        raise HandoffValidationError(
            "exact_ties_sha256 changed during evaluator-input preparation"
        )
    return EvaluatorInputBundle(
        run_id=receipt.run_id,
        context_count=receipt.counts.context_count,
        independent_review_count=len(evaluator_ratings),
        candidate_packet=candidate_packet,
        ratings=evaluator_ratings,
        arm_key=evaluator_arm_key,
        input_sha256=input_sha256,
    )


def _dict_jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for record in records
    ).encode("utf-8")


def write_evaluator_input_bundle(
    bundle: EvaluatorInputBundle,
    output_dir: Path,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    try:
        resolved_repo = repo_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise HandoffValidationError(
            f"repository root cannot be resolved: {exc}"
        ) from exc
    resolved_output = output_dir.expanduser().resolve(strict=False)
    if resolved_output == resolved_repo or resolved_output.is_relative_to(
        resolved_repo
    ):
        raise HandoffValidationError(
            "evaluator input bundle must remain outside the Git repository"
        )
    if resolved_output.exists():
        raise FileExistsError(
            f"evaluator input bundle directory already exists: {resolved_output}"
        )
    outputs = {
        "candidate_packet_v1.jsonl": _dict_jsonl_bytes(
            bundle.candidate_packet
        ),
        "ratings_v1.jsonl": _dict_jsonl_bytes(bundle.ratings),
        "arm_key_v1.jsonl": _dict_jsonl_bytes(bundle.arm_key),
    }
    output_sha256 = {
        filename: hashlib.sha256(content).hexdigest()
        for filename, content in outputs.items()
    }
    manifest = {
        "schema_version": "humanistic_evaluator_input_bundle_receipt_v1",
        "status": "COMPLETE",
        "run_id": bundle.run_id,
        "context_count": bundle.context_count,
        "independent_review_count": bundle.independent_review_count,
        "input_sha256": bundle.input_sha256,
        "output_sha256": output_sha256,
        "redaction": {
            "manifest_contains_candidate_text": False,
            "manifest_contains_arm_assignments": False,
            "manifest_contains_case_mapping": False,
        },
    }
    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")

    created_paths: list[Path] = []
    try:
        resolved_output.mkdir(mode=0o700, parents=True, exist_ok=False)
        resolved_output.chmod(0o700)
        for filename, content in outputs.items():
            path = resolved_output / filename
            _exclusive_write(path, content, mode=0o600)
            created_paths.append(path)
        manifest_path = resolved_output / "evaluator_input_manifest_v1.json"
        _exclusive_write(manifest_path, manifest_bytes, mode=0o600)
        created_paths.append(manifest_path)
    except Exception:
        for path in reversed(created_paths):
            path.unlink(missing_ok=True)
        try:
            resolved_output.rmdir()
        except OSError:
            pass
        raise
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.expanduser().resolve(strict=True).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "CandidateGenerationReceipt",
    "EvaluatorInputBundle",
    "HandoffValidationError",
    "build_blank_rating_templates",
    "build_generation_receipt",
    "build_evaluator_input_bundle",
    "build_unblinded_evaluator_ratings",
    "load_generation_receipt",
    "sha256_file",
    "write_blank_rating_templates",
    "write_generation_receipt",
    "write_evaluator_input_bundle",
    "write_unblinded_evaluator_ratings",
]
