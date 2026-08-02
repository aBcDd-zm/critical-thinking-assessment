#!/usr/bin/env python3
"""Two-arm synthetic smoke test with a private raw-output audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.humanistic_candidate_generation import (
    CandidateArmFailure,
    GenerationProtocol,
    PromptSource,
    StrictFrozenInterviewerRenderer,
    build_candidate_arm_request,
    load_frozen_prompt_sources,
    sha256_file,
    validate_private_output_path,
    validate_real_generation_environment,
)
from app.agents.humanistic_evaluation_context import HumanisticPilotContext
from app.agents.interviewer_output_contract import (
    INTERVIEWER_OUTPUT_CONTRACT_SHA256,
    INTERVIEWER_OUTPUT_CONTRACT_VERSION,
)
from app.core.config import get_settings


PROMPT_REGISTRY_PATH = BACKEND_ROOT / "seeds" / "prompts.yaml"
GENERATOR_PATH = (
    BACKEND_ROOT / "app" / "agents" / "humanistic_candidate_generation.py"
)
INTERVIEWER_PATH = BACKEND_ROOT / "app" / "agents" / "interviewer_agent.py"
OUTPUT_CONTRACT_PATH = (
    BACKEND_ROOT / "app" / "agents" / "interviewer_output_contract.py"
)
VALIDATOR_PATH = (
    BACKEND_ROOT / "app" / "agents" / "interview_question_validator.py"
)
MODEL_GATEWAY_PATH = BACKEND_ROOT / "app" / "services" / "model_gateway_service.py"
CONFIG_PATH = BACKEND_ROOT / "app" / "core" / "config.py"
SMOKE_CONFIRMATION = "RUN_SYNTHETIC_2_ARM_SMOKE"
SMOKE_AUDIT_FILENAME = "synthetic_smoke_rejection_audit_v1.json"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def synthetic_smoke_context() -> HumanisticPilotContext:
    """Return one in-code provisional context unrelated to frozen/locked data."""
    return HumanisticPilotContext.model_validate(
        {
            "schema_version": "humanistic_pilot_context_v1",
            "context_id": "HIV1-S99",
            "split": "dev",
            "category": "opening",
            "scenario_id": "synthetic-two-arm-smoke-only",
            "status": "provisional_synthetic",
            "privacy": "synthetic_no_personal_data",
            "visible_history": [
                {
                    "turn_id": 1,
                    "turn_index": 1,
                    "stage_id": 1,
                    "stage_code": "s1_problem_definition",
                    "speaker": "user",
                    "content": "这是单条冒烟测试，我想先确认任务目标和时间限制。",
                    "content_type": "interview_answer",
                }
            ],
            "latest_user_turn_id": 1,
            "frozen_plan": {
                "response_intent": "assess_answer",
                "action": "PROBE",
                "active_topic": "合成任务界定",
                "target_dimension": "problem_definition",
                "target_evidence": "说明任务界定中仍需确认的一项信息",
                "delivery_mode": "reflective_probe",
                "question_intent": "询问用户接下来会先确认哪一项具体信息",
                "reflection_basis_turn_ids": [1],
                "reason": "仅用于双臂冒烟诊断的固定合成计划",
                "budget": {
                    "used_turns": 1,
                    "remaining_turns": 9,
                    "reserved_update_turns": 2,
                    "reserved_closure_turns": 1,
                },
            },
            "plan_protected_fields": [
                "response_intent",
                "action",
                "target_dimension",
                "delivery_mode",
                "question_intent",
            ],
            "event_unit": None,
            "allowed_facts": ["这是不含任何个人信息的单条合成冒烟测试。"],
            "reflection_review": {
                "turn_ids": [1],
                "supported_summary": "用户表示想先确认任务目标和时间限制。",
                "unsupported_inferences": ["不推断情绪、人格、动机或私人经历。"],
            },
            "formal_answer": True,
        }
    )


def smoke_preflight_lock(
    protocol: GenerationProtocol,
    prompt_sources: dict[str, PromptSource],
) -> tuple[str, dict[str, object]]:
    context = synthetic_smoke_context()
    payload = {
        "schema_version": "humanistic_two_arm_smoke_preflight_v1",
        "sources": {
            "smoke_cli_sha256": sha256_file(Path(__file__).resolve()),
            "generator_sha256": sha256_file(GENERATOR_PATH),
            "prompt_registry_sha256": sha256_file(PROMPT_REGISTRY_PATH),
            "interviewer_sha256": sha256_file(INTERVIEWER_PATH),
            "output_contract_module_sha256": sha256_file(
                OUTPUT_CONTRACT_PATH
            ),
            "validator_sha256": sha256_file(VALIDATOR_PATH),
            "model_gateway_sha256": sha256_file(MODEL_GATEWAY_PATH),
            "config_sha256": sha256_file(CONFIG_PATH),
            "synthetic_context_sha256": _sha256_text(
                context.model_dump_json()
            ),
        },
        "protocol": protocol.model_dump(mode="json"),
        "output_contract": {
            "version": INTERVIEWER_OUTPUT_CONTRACT_VERSION,
            "sha256": INTERVIEWER_OUTPUT_CONTRACT_SHA256,
        },
        "prompts": {
            arm: {
                "version": source.version,
                "content_sha256": source.content_sha256,
            }
            for arm, source in sorted(prompt_sources.items())
        },
        "scope": {
            "arms": ["baseline", "humanistic"],
            "attempts_per_arm": 1,
            "fallback": False,
            "reads_frozen_contexts": False,
            "reads_locked_contexts": False,
            "reads_review_examples": False,
            "raw_audit_directory_mode": "0700",
            "raw_audit_file_mode": "0600",
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(canonical), payload


def run_synthetic_two_arm_smoke(
    *,
    renderer,
    protocol: GenerationProtocol,
    prompt_sources: dict[str, PromptSource],
    smoke_preflight_sha256: str,
    context: HumanisticPilotContext | None = None,
) -> dict[str, object]:
    context = context or synthetic_smoke_context()
    arm_audits: list[dict[str, object]] = []
    for arm in ("baseline", "humanistic"):
        request = build_candidate_arm_request(context, arm, prompt_sources)
        try:
            result = renderer.render(request)
            arm_audits.append(
                {
                    "arm": arm,
                    "status": "accepted",
                    "error_code": None,
                    "fatal": False,
                    "validation_codes": result.validation_codes,
                    "quality_flag_mismatches": (
                        result.quality_flag_mismatches
                    ),
                    "contract_errors": [],
                    "configured_model": protocol.model,
                    "response_model": result.model,
                    "prompt_version": request.prompt_version,
                    "prompt_sha256": request.prompt_sha256,
                    "output_contract_version": (
                        request.output_contract_version
                    ),
                    "output_contract_sha256": (
                        request.output_contract_sha256
                    ),
                    "raw_output": result.raw_output,
                    "raw_output_sha256": _sha256_text(result.raw_output),
                    "duration_ms": result.duration_ms,
                }
            )
        except CandidateArmFailure as exc:
            raw_output = exc.raw_output
            arm_audits.append(
                {
                    "arm": arm,
                    "status": "rejected",
                    "error_code": exc.error_code,
                    "fatal": exc.fatal,
                    "validation_codes": exc.validation_codes,
                    "quality_flag_mismatches": (
                        exc.quality_flag_mismatches
                    ),
                    "contract_errors": [
                        item.model_dump(mode="json")
                        for item in exc.contract_errors
                    ],
                    "configured_model": protocol.model,
                    "response_model": exc.model,
                    "prompt_version": request.prompt_version,
                    "prompt_sha256": request.prompt_sha256,
                    "output_contract_version": (
                        request.output_contract_version
                    ),
                    "output_contract_sha256": (
                        request.output_contract_sha256
                    ),
                    "raw_output": raw_output,
                    "raw_output_sha256": (
                        _sha256_text(raw_output)
                        if raw_output is not None
                        else None
                    ),
                    "duration_ms": exc.duration_ms,
                }
            )
            if exc.fatal:
                break
    passed = (
        len(arm_audits) == 2
        and all(item["status"] == "accepted" for item in arm_audits)
    )
    return {
        "schema_version": "humanistic_two_arm_smoke_audit_v1",
        "status": "pass" if passed else "blocked",
        "smoke_run_id": f"smoke_{secrets.token_hex(16)}",
        "formal_candidate_generation": False,
        "context": {
            "context_id": context.context_id,
            "split": context.split,
            "status": context.status,
            "privacy": context.privacy,
            "content_sha256": _sha256_text(context.model_dump_json()),
        },
        "smoke_preflight_sha256": smoke_preflight_sha256,
        "arm_audits": arm_audits,
    }


def write_private_smoke_audit(
    audit: dict[str, object],
    output_dir: Path,
) -> Path:
    resolved_output = validate_private_output_path(
        output_dir,
        repo_root=REPO_ROOT,
    )
    resolved_output.mkdir(mode=0o700, parents=True, exist_ok=False)
    resolved_output.chmod(0o700)
    output_path = resolved_output / SMOKE_AUDIT_FILENAME
    content = (
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    descriptor = os.open(
        output_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preflight or run exactly one baseline and one humanistic call "
            "against an in-code provisional synthetic context."
        )
    )
    parser.add_argument("--execute-real-smoke", action="store_true")
    parser.add_argument("--expected-smoke-preflight-sha")
    parser.add_argument("--confirmation")
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
        protocol = GenerationProtocol(
            provider=settings.MODEL_PROVIDER,
            model=settings.DEEPSEEK_MODEL,
        )
        validate_real_generation_environment(protocol, settings)
        prompt_sources = load_frozen_prompt_sources(PROMPT_REGISTRY_PATH)
        preflight_sha, preflight_lock = smoke_preflight_lock(
            protocol,
            prompt_sources,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "will_call_model": False,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.execute_real_smoke:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "will_call_model": False,
                    "remote_call_count": 2,
                    "smoke_preflight_sha256": preflight_sha,
                    "preflight_lock": preflight_lock,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    blockers: list[str] = []
    if args.expected_smoke_preflight_sha != preflight_sha:
        blockers.append("smoke preflight SHA-256 confirmation mismatch")
    if args.confirmation != SMOKE_CONFIRMATION:
        blockers.append("synthetic two-arm smoke confirmation missing")
    output_dir: Path | None = None
    if not args.output_dir:
        blockers.append("private smoke output directory is required")
    else:
        try:
            output_dir = validate_private_output_path(
                Path(args.output_dir),
                repo_root=REPO_ROOT,
            )
        except (FileExistsError, ValueError) as exc:
            blockers.append(str(exc))
    if blockers:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "will_call_model": False,
                    "blockers": blockers,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    assert output_dir is not None
    audit = run_synthetic_two_arm_smoke(
        renderer=StrictFrozenInterviewerRenderer(settings),
        protocol=protocol,
        prompt_sources=prompt_sources,
        smoke_preflight_sha256=preflight_sha,
    )
    output_path = write_private_smoke_audit(audit, output_dir)
    print(
        json.dumps(
            {
                "status": audit["status"],
                "will_call_model": True,
                "attempted_arm_count": len(audit["arm_audits"]),
                "output_path": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if audit["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
