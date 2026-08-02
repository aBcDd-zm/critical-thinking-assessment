#!/usr/bin/env python3
"""Evaluate the Humanistic Interviewer v1.1 release evidence.

The v1.1 evaluator reuses the frozen v1 48-context corpus and scoring
thresholds, but it deliberately rejects v1 evidence.  Every evidence record
must carry the v1.1 namespace and all files must be bound by a v1.1 receipt.
The evaluator never generates candidates, ratings, approvals, or UAT results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.evaluate_humanistic_interviewer_v1 import (
    DEFAULT_CONTEXTS_PATH,
    evaluate_release_gate as evaluate_v1_release_gate,
)


DEFAULT_CONFIG_PATH = (
    REPO_ROOT
    / "docs"
    / "humanistic_interviewer"
    / "evaluation_config_v1_1.json"
)
EVALUATOR_NAME = "humanistic_interviewer_v1_1"
CONFIG_SCHEMA = "humanistic_evaluation_config_v1_1"
RECEIPT_SCHEMA = "humanistic_evaluation_receipt_v1_1"
EVIDENCE_NAMESPACE = "humanistic_v1_1"
STYLE_VERSION = "humanistic_v1_1"
PROMPT_VERSION = "humanistic_compact_v1_1"
GENERATION_VERSION = "humanistic_candidate_generation_v1_1"
BLIND_REVIEW_VERSION = "humanistic_blind_review_v1_1"
MEASUREMENT_POLICY_VERSION = "ai_copy_exclusion_v1"
APPROVAL_SCHEMA = "humanistic_measurement_contract_approval_v1_1"
SOURCE_BUNDLE_VERSION = "humanistic_v1_1_runtime_source_bundle_v2"
RUNTIME_SOURCE_PATHS = {
    "humanistic_microstructure": (
        BACKEND_ROOT / "app" / "agents" / "humanistic_interviewer_v11.py"
    ),
    "candidate_intent_registry": (
        BACKEND_ROOT
        / "app"
        / "agents"
        / "humanistic_v11_intent_registry.py"
    ),
    "runtime_renderer": (
        BACKEND_ROOT / "app" / "agents" / "runtime_interviewer_agent.py"
    ),
    "question_validator": (
        BACKEND_ROOT / "app" / "agents" / "interview_question_validator.py"
    ),
    "session_integration": (
        BACKEND_ROOT / "app" / "services" / "session_service.py"
    ),
    "audit_export": (
        BACKEND_ROOT
        / "app"
        / "services"
        / "admin_session_review_service.py"
    ),
    "evidence_tracker": (
        BACKEND_ROOT / "app" / "services" / "evidence_tracker_service.py"
    ),
    "behavior_signal_extractor": (
        BACKEND_ROOT / "app" / "agents" / "behavior_signal_extractor.py"
    ),
    "deterministic_planner": (
        BACKEND_ROOT / "app" / "agents" / "interview_planner_agent.py"
    ),
    "runtime_prompt_seed": BACKEND_ROOT / "seeds" / "runtime_prompts.yaml",
}
SHA256_KEYS = (
    "candidate_packet",
    "ratings",
    "arm_key",
    "runtime_records",
    "uat_records",
    "measurement_approval",
)


class V11EvidenceError(ValueError):
    """Raised when v1.1 release evidence cannot be trusted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_source_hashes() -> dict[str, dict[str, str]]:
    return {
        key: {
            "path": path.relative_to(REPO_ROOT).as_posix(),
            "sha256": sha256_file(path),
        }
        for key, path in RUNTIME_SOURCE_PATHS.items()
    }


def runtime_source_bundle_sha256() -> str:
    payload = {
        "bundle_version": SOURCE_BUNDLE_VERSION,
        "sources": runtime_source_hashes(),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise V11EvidenceError(f"{label}: cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise V11EvidenceError(
            f"{label}: invalid JSON at line {exc.lineno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise V11EvidenceError(f"{label}: root value must be an object")
    return value


def _require_exact(value: Any, expected: str, label: str) -> None:
    if value != expected:
        raise V11EvidenceError(
            f"{label}: expected {expected!r}, got {value!r}"
        )


def _load_config(path: Path) -> dict[str, Any]:
    try:
        supplied = path.resolve(strict=True)
        canonical = DEFAULT_CONFIG_PATH.resolve(strict=True)
    except OSError as exc:
        raise V11EvidenceError(
            f"config: cannot resolve canonical config: {exc}"
        ) from exc
    if supplied != canonical:
        raise V11EvidenceError(
            "config: release evaluation requires the canonical v1.1 config"
        )
    config = _load_json_object(supplied, "config")
    _require_exact(config.get("schema_version"), CONFIG_SCHEMA, "config.schema_version")
    _require_exact(config.get("evaluator"), EVALUATOR_NAME, "config.evaluator")
    _require_exact(config.get("style_version"), STYLE_VERSION, "config.style_version")
    _require_exact(
        config.get("prompt_version"),
        PROMPT_VERSION,
        "config.prompt_version",
    )

    evidence = config.get("evidence_contract")
    if not isinstance(evidence, dict):
        raise V11EvidenceError("config.evidence_contract must be an object")
    _require_exact(
        evidence.get("namespace"),
        EVIDENCE_NAMESPACE,
        "config.evidence_contract.namespace",
    )
    _require_exact(
        evidence.get("receipt_schema"),
        RECEIPT_SCHEMA,
        "config.evidence_contract.receipt_schema",
    )
    _require_exact(
        evidence.get("candidate_generation_version"),
        GENERATION_VERSION,
        "config.evidence_contract.candidate_generation_version",
    )
    _require_exact(
        evidence.get("blind_review_version"),
        BLIND_REVIEW_VERSION,
        "config.evidence_contract.blind_review_version",
    )
    _require_exact(
        evidence.get("measurement_policy_version"),
        MEASUREMENT_POLICY_VERSION,
        "config.evidence_contract.measurement_policy_version",
    )
    if evidence.get("legacy_v1_evidence_accepted") is not False:
        raise V11EvidenceError(
            "config.evidence_contract.legacy_v1_evidence_accepted must be false"
        )
    source_binding = evidence.get("runtime_source_binding")
    if not isinstance(source_binding, dict):
        raise V11EvidenceError(
            "config.evidence_contract.runtime_source_binding must be an object"
        )
    _require_exact(
        source_binding.get("bundle_version"),
        SOURCE_BUNDLE_VERSION,
        "config.evidence_contract.runtime_source_binding.bundle_version",
    )
    if source_binding.get("required_sources") != list(RUNTIME_SOURCE_PATHS):
        raise V11EvidenceError(
            "config.evidence_contract.runtime_source_binding.required_sources "
            "must match the canonical runtime source list"
        )
    return config


def _validate_namespaced_jsonl(path: Path, label: str) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise V11EvidenceError(f"{label}: cannot read {path}: {exc}") from exc
    record_count = 0
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        record_count += 1
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise V11EvidenceError(
                f"{label}: invalid JSON on line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise V11EvidenceError(
                f"{label}: line {line_number} must be an object"
            )
        _require_exact(
            record.get("evidence_namespace"),
            EVIDENCE_NAMESPACE,
            f"{label} line {line_number}.evidence_namespace",
        )
        if label in {"runtime_records", "uat_records"}:
            for field, expected in (
                ("style_version", STYLE_VERSION),
                ("prompt_version", PROMPT_VERSION),
                (
                    "runtime_source_bundle_sha256",
                    runtime_source_bundle_sha256(),
                ),
            ):
                _require_exact(
                    record.get(field),
                    expected,
                    f"{label} line {line_number}.{field}",
                )
    if record_count == 0:
        raise V11EvidenceError(f"{label}: no records")


def _validate_measurement_approval(path: Path) -> None:
    approval = _load_json_object(path, "measurement approval")
    _require_exact(
        approval.get("schema_version"),
        APPROVAL_SCHEMA,
        "measurement approval.schema_version",
    )
    _require_exact(
        approval.get("evidence_namespace"),
        EVIDENCE_NAMESPACE,
        "measurement approval.evidence_namespace",
    )
    _require_exact(
        approval.get("measurement_policy_version"),
        MEASUREMENT_POLICY_VERSION,
        "measurement approval.measurement_policy_version",
    )
    _require_exact(
        approval.get("approver_role"),
        "member_a",
        "measurement approval.approver_role",
    )
    if approval.get("approved") is not True:
        raise V11EvidenceError("measurement approval.approved must be true")
    evidence_ref = approval.get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise V11EvidenceError(
            "measurement approval.evidence_ref must be a non-empty string"
        )


def _validate_receipt(
    path: Path,
    *,
    config_path: Path,
    contexts_path: Path,
    evidence_paths: dict[str, Path],
) -> None:
    receipt = _load_json_object(path, "receipt")
    _require_exact(
        receipt.get("schema_version"),
        RECEIPT_SCHEMA,
        "receipt.schema_version",
    )
    _require_exact(
        receipt.get("receipt_status"),
        "VERIFIED_COMPLETE_V1_1_EVIDENCE",
        "receipt.receipt_status",
    )
    for field, expected in (
        ("evidence_namespace", EVIDENCE_NAMESPACE),
        ("style_version", STYLE_VERSION),
        ("prompt_version", PROMPT_VERSION),
        ("candidate_generation_version", GENERATION_VERSION),
        ("blind_review_version", BLIND_REVIEW_VERSION),
        ("measurement_policy_version", MEASUREMENT_POLICY_VERSION),
    ):
        _require_exact(receipt.get(field), expected, f"receipt.{field}")
    _require_exact(
        receipt.get("config_sha256"),
        sha256_file(config_path),
        "receipt.config_sha256",
    )
    _require_exact(
        receipt.get("context_manifest_sha256"),
        sha256_file(contexts_path),
        "receipt.context_manifest_sha256",
    )
    _require_exact(
        receipt.get("runtime_source_bundle_version"),
        SOURCE_BUNDLE_VERSION,
        "receipt.runtime_source_bundle_version",
    )
    _require_exact(
        receipt.get("runtime_source_bundle_sha256"),
        runtime_source_bundle_sha256(),
        "receipt.runtime_source_bundle_sha256",
    )
    supplied_sources = receipt.get("runtime_sources")
    expected_sources = runtime_source_hashes()
    if not isinstance(supplied_sources, dict):
        raise V11EvidenceError("receipt.runtime_sources must be an object")
    if set(supplied_sources) != set(expected_sources):
        raise V11EvidenceError(
            "receipt.runtime_sources must contain exactly: "
            + ", ".join(expected_sources)
        )
    for key, expected in expected_sources.items():
        supplied = supplied_sources.get(key)
        if not isinstance(supplied, dict):
            raise V11EvidenceError(
                f"receipt.runtime_sources.{key} must be an object"
            )
        _require_exact(
            supplied.get("path"),
            expected["path"],
            f"receipt.runtime_sources.{key}.path",
        )
        _require_exact(
            supplied.get("sha256"),
            expected["sha256"],
            f"receipt.runtime_sources.{key}.sha256",
        )
    files = receipt.get("files")
    if not isinstance(files, dict):
        raise V11EvidenceError("receipt.files must be an object")
    if set(files) != set(SHA256_KEYS):
        raise V11EvidenceError(
            "receipt.files must contain exactly: " + ", ".join(SHA256_KEYS)
        )
    for key in SHA256_KEYS:
        item = files.get(key)
        if not isinstance(item, dict):
            raise V11EvidenceError(f"receipt.files.{key} must be an object")
        _require_exact(
            item.get("sha256"),
            sha256_file(evidence_paths[key]),
            f"receipt.files.{key}.sha256",
        )


def _blocked_report(
    *,
    config_path: Path,
    contexts_path: Path,
    receipt_path: Path | None,
    evidence_paths: dict[str, Path | None],
) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "evaluator": EVALUATOR_NAME,
        "style_version": STYLE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "status": "BLOCKED",
        "inputs": {
            "config": str(config_path),
            "contexts": str(contexts_path),
            "receipt": str(receipt_path) if receipt_path else None,
            **{
                key: str(value) if value else None
                for key, value in evidence_paths.items()
            },
        },
        "metrics": {},
        "gates": [],
        "blockers": [],
        "failures": [],
    }


def evaluate_release_gate(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    contexts_path: Path = DEFAULT_CONTEXTS_PATH,
    receipt_path: Path | None = None,
    candidate_packet_path: Path | None = None,
    ratings_path: Path | None = None,
    arm_key_path: Path | None = None,
    runtime_records_path: Path | None = None,
    uat_records_path: Path | None = None,
    measurement_approval_path: Path | None = None,
) -> dict[str, Any]:
    """Validate an independently versioned v1.1 evidence bundle."""

    optional_paths = {
        "candidate_packet": candidate_packet_path,
        "ratings": ratings_path,
        "arm_key": arm_key_path,
        "runtime_records": runtime_records_path,
        "uat_records": uat_records_path,
        "measurement_approval": measurement_approval_path,
    }
    report = _blocked_report(
        config_path=config_path,
        contexts_path=contexts_path,
        receipt_path=receipt_path,
        evidence_paths=optional_paths,
    )
    blockers: list[str] = report["blockers"]

    try:
        _load_config(config_path)
        if contexts_path.resolve(strict=True) != DEFAULT_CONTEXTS_PATH.resolve(
            strict=True
        ):
            raise V11EvidenceError(
                "contexts: v1.1 release evaluation requires the frozen "
                "canonical 48-context v1 manifest"
            )
    except (OSError, V11EvidenceError) as exc:
        blockers.append(str(exc))
        return report

    if receipt_path is None:
        blockers.append("receipt: v1.1 evidence receipt not supplied")
    elif not receipt_path.is_file():
        blockers.append(f"receipt: file not found: {receipt_path}")
    for label, path in optional_paths.items():
        if path is None:
            blockers.append(f"{label}: v1.1 evidence not supplied")
        elif not path.is_file():
            blockers.append(f"{label}: file not found: {path}")
    if blockers:
        return report

    evidence_paths = {
        key: value for key, value in optional_paths.items() if value is not None
    }
    try:
        for label in (
            "candidate_packet",
            "ratings",
            "arm_key",
            "runtime_records",
            "uat_records",
        ):
            _validate_namespaced_jsonl(evidence_paths[label], label)
        _validate_measurement_approval(evidence_paths["measurement_approval"])
        _validate_receipt(
            receipt_path,
            config_path=config_path,
            contexts_path=contexts_path,
            evidence_paths=evidence_paths,
        )
    except V11EvidenceError as exc:
        blockers.append(str(exc))
        return report

    inherited = evaluate_v1_release_gate(
        contexts_path=contexts_path,
        candidate_packet_path=evidence_paths["candidate_packet"],
        ratings_path=evidence_paths["ratings"],
        arm_key_path=evidence_paths["arm_key"],
        runtime_records_path=evidence_paths["runtime_records"],
        uat_records_path=evidence_paths["uat_records"],
        enforce_context_contract=True,
    )
    inherited["schema_version"] = "1.1"
    inherited["evaluator"] = EVALUATOR_NAME
    inherited["style_version"] = STYLE_VERSION
    inherited["prompt_version"] = PROMPT_VERSION
    inherited["evidence_namespace"] = EVIDENCE_NAMESPACE
    inherited["inherited_thresholds_from"] = "humanistic_interviewer_v1"
    inherited["inputs"].update(
        {
            "config": str(config_path),
            "receipt": str(receipt_path),
            "measurement_approval": str(measurement_approval_path),
        }
    )
    return inherited


def _path_or_none(value: str | None) -> Path | None:
    return Path(value).expanduser().resolve() if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Humanistic Interviewer v1.1 independently versioned "
            "blind-review, runtime, UAT, and Member A approval evidence."
        )
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--contexts", default=str(DEFAULT_CONTEXTS_PATH))
    parser.add_argument("--receipt")
    parser.add_argument("--candidate-packet")
    parser.add_argument("--ratings")
    parser.add_argument("--arm-key")
    parser.add_argument("--runtime-records")
    parser.add_argument("--uat-records")
    parser.add_argument("--measurement-approval")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    report = evaluate_release_gate(
        config_path=Path(args.config).expanduser().resolve(),
        contexts_path=Path(args.contexts).expanduser().resolve(),
        receipt_path=_path_or_none(args.receipt),
        candidate_packet_path=_path_or_none(args.candidate_packet),
        ratings_path=_path_or_none(args.ratings),
        arm_key_path=_path_or_none(args.arm_key),
        runtime_records_path=_path_or_none(args.runtime_records),
        uat_records_path=_path_or_none(args.uat_records),
        measurement_approval_path=_path_or_none(args.measurement_approval),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
