#!/usr/bin/env python3
"""Six-action, two-arm synthetic smoke for the frozen candidate protocol."""

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
    GenerationProtocol,
    PromptSource,
    StrictFrozenInterviewerRenderer,
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
from scripts.smoke_humanistic_candidate_arms_v1 import (
    run_synthetic_two_arm_smoke,
)
from scripts.smoke_humanistic_event_candidate_arms_v1 import (
    synthetic_event_smoke_context,
)


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
BASE_SMOKE_PATH = SCRIPT_DIR / "smoke_humanistic_candidate_arms_v1.py"
EVENT_SMOKE_PATH = SCRIPT_DIR / "smoke_humanistic_event_candidate_arms_v1.py"
MATRIX_CONFIRMATION = "RUN_SYNTHETIC_6_ACTION_2_ARM_SMOKE"
MATRIX_AUDIT_FILENAME = "synthetic_action_matrix_smoke_audit_v1.json"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _synthetic_action_context(
    *,
    context_id: str,
    category: str,
    action: str,
    target_dimension: str,
    stage_code: str,
    delivery_mode: str,
    content: str,
    question_intent: str,
) -> HumanisticPilotContext:
    turn_id = int(context_id[-2:])
    return HumanisticPilotContext.model_validate(
        {
            "schema_version": "humanistic_pilot_context_v1",
            "context_id": context_id,
            "split": "dev",
            "category": category,
            "scenario_id": f"synthetic-action-{action.lower()}-smoke-only",
            "status": "provisional_synthetic",
            "privacy": "synthetic_no_personal_data",
            "visible_history": [
                {
                    "turn_id": turn_id,
                    "turn_index": turn_id,
                    "stage_code": stage_code,
                    "speaker": "user",
                    "content": content,
                    "content_type": "interview_answer",
                }
            ],
            "latest_user_turn_id": turn_id,
            "frozen_plan": {
                "response_intent": "assess_answer",
                "action": action,
                "active_topic": "合成动作结构验证",
                "target_dimension": target_dimension,
                "target_evidence": "验证计划动作对应的输出结构",
                "release_event_code": None,
                "release_unit_code": None,
                "delivery_mode": delivery_mode,
                "question_intent": question_intent,
                "reflection_basis_turn_ids": [turn_id],
                "reason": "仅用于两臂动作矩阵冒烟的固定合成计划",
                "budget": {
                    "used_turns": turn_id,
                    "remaining_turns": 2,
                    "reserved_update_turns": 0,
                    "reserved_closure_turns": 0,
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
            "allowed_facts": [content],
            "reflection_review": {
                "turn_ids": [turn_id],
                "supported_summary": "只概括合成用户明确表达的内容",
                "unsupported_inferences": [
                    "不推断情绪、人格、动机或私人经历。"
                ],
            },
            "formal_answer": True,
        }
    )


def synthetic_action_smoke_contexts() -> list[HumanisticPilotContext]:
    contexts = [
        _synthetic_action_context(
            context_id="HIV1-S91",
            category="opening",
            action="PROBE",
            target_dimension="problem_definition",
            stage_code="s1_problem_definition",
            delivery_mode="reflective_probe",
            content="我会先确认任务目标和时间限制。",
            question_intent="询问还需要先界定哪项信息",
        ),
        _synthetic_action_context(
            context_id="HIV1-S92",
            category="probe",
            action="CHALLENGE",
            target_dimension="reasoning_argumentation",
            stage_code="s4_reasoning_decision",
            delivery_mode="perspective_shift",
            content="目前这个结论主要来自一次内部讨论。",
            question_intent="询问什么情况会使当前理由不成立",
        ),
        _synthetic_action_context(
            context_id="HIV1-S94",
            category="clarify",
            action="CLARIFY",
            target_dimension="problem_definition",
            stage_code="s1_problem_definition",
            delivery_mode="clarification",
            content="我说的尽快是指本周内形成初步方案。",
            question_intent="确认本周内需要形成方案的具体范围",
        ),
        _synthetic_action_context(
            context_id="HIV1-S95",
            category="integrate_close",
            action="INTEGRATE",
            target_dimension="integrative_decision",
            stage_code="s6_integrated_plan",
            delivery_mode="integration",
            content="我会先小范围试行，再根据结果决定是否扩大。",
            question_intent="询问试行、观察和扩大安排如何衔接",
        ),
        _synthetic_action_context(
            context_id="HIV1-S96",
            category="integrate_close",
            action="CONCLUDE",
            target_dimension="evidence_evaluation",
            stage_code="s6_integrated_plan",
            delivery_mode="closing",
            content="我会同时观察结果指标和成本指标再决定。",
            question_intent="总结用户将同时依据结果和成本作出决定",
        ),
    ]
    event_payload = synthetic_event_smoke_context().model_dump(mode="json")
    event_payload["context_id"] = "HIV1-S93"
    event_payload["scenario_id"] = "synthetic-action-release-event-smoke-only"
    contexts.insert(2, HumanisticPilotContext.model_validate(event_payload))
    return contexts


def action_matrix_preflight_lock(
    protocol: GenerationProtocol,
    prompt_sources: dict[str, PromptSource],
) -> tuple[str, dict[str, object]]:
    contexts = synthetic_action_smoke_contexts()
    payload = {
        "schema_version": "humanistic_action_matrix_preflight_v1",
        "sources": {
            "matrix_smoke_cli_sha256": sha256_file(Path(__file__).resolve()),
            "base_smoke_cli_sha256": sha256_file(BASE_SMOKE_PATH),
            "event_smoke_cli_sha256": sha256_file(EVENT_SMOKE_PATH),
            "generator_sha256": sha256_file(GENERATOR_PATH),
            "prompt_registry_sha256": sha256_file(PROMPT_REGISTRY_PATH),
            "interviewer_sha256": sha256_file(INTERVIEWER_PATH),
            "output_contract_module_sha256": sha256_file(
                OUTPUT_CONTRACT_PATH
            ),
            "validator_sha256": sha256_file(VALIDATOR_PATH),
            "model_gateway_sha256": sha256_file(MODEL_GATEWAY_PATH),
            "config_sha256": sha256_file(CONFIG_PATH),
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
        "contexts": [
            {
                "context_id": context.context_id,
                "action": context.frozen_plan.action,
                "sha256": _sha256_text(context.model_dump_json()),
            }
            for context in contexts
        ],
        "scope": {
            "context_count": 6,
            "arms": ["baseline", "humanistic"],
            "maximum_remote_calls": 12,
            "fallback": False,
            "formal_candidate_generation": False,
            "reads_frozen_contexts": False,
            "reads_locked_contexts": False,
            "reads_review_examples": False,
            "directory_mode": "0700",
            "file_mode": "0600",
        },
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(canonical), payload


def run_action_matrix_smoke(
    *,
    renderer,
    protocol: GenerationProtocol,
    prompt_sources: dict[str, PromptSource],
    preflight_sha256: str,
) -> dict[str, object]:
    case_audits: list[dict[str, object]] = []
    for context in synthetic_action_smoke_contexts():
        audit = run_synthetic_two_arm_smoke(
            renderer=renderer,
            protocol=protocol,
            prompt_sources=prompt_sources,
            smoke_preflight_sha256=preflight_sha256,
            context=context,
        )
        case_audits.append(audit)
        if audit["status"] != "pass":
            break
    passed = len(case_audits) == 6 and all(
        item["status"] == "pass" for item in case_audits
    )
    return {
        "schema_version": "humanistic_action_matrix_smoke_audit_v1",
        "status": "pass" if passed else "blocked",
        "matrix_run_id": f"matrix_{secrets.token_hex(16)}",
        "formal_candidate_generation": False,
        "preflight_sha256": preflight_sha256,
        "case_audits": case_audits,
    }


def write_private_matrix_audit(audit: dict[str, object], output_dir: Path) -> Path:
    resolved_output = validate_private_output_path(
        output_dir,
        repo_root=REPO_ROOT,
    )
    resolved_output.mkdir(mode=0o700, parents=True, exist_ok=False)
    resolved_output.chmod(0o700)
    output_path = resolved_output / MATRIX_AUDIT_FILENAME
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
        description="Run a private six-action baseline/humanistic smoke matrix."
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
        preflight_sha, preflight_lock = action_matrix_preflight_lock(
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
                    "maximum_remote_call_count": 12,
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
        blockers.append("action matrix preflight SHA-256 confirmation mismatch")
    if args.confirmation != MATRIX_CONFIRMATION:
        blockers.append("synthetic action matrix confirmation missing")
    output_dir: Path | None = None
    if not args.output_dir:
        blockers.append("private action matrix output directory is required")
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
    audit = run_action_matrix_smoke(
        renderer=StrictFrozenInterviewerRenderer(settings),
        protocol=protocol,
        prompt_sources=prompt_sources,
        preflight_sha256=preflight_sha,
    )
    output_path = write_private_matrix_audit(audit, output_dir)
    call_count = sum(
        len(case["arm_audits"]) for case in audit["case_audits"]
    )
    status = str(audit["status"])
    print(
        json.dumps(
            {
                "status": status,
                "will_call_model": True,
                "remote_call_count": call_count,
                "smoke_preflight_sha256": preflight_sha,
                "output_path": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
