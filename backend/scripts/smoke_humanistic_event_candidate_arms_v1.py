#!/usr/bin/env python3
"""Two-arm synthetic event smoke for generation reliability amendments."""

from __future__ import annotations

import argparse
import hashlib
import json
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
    write_private_smoke_audit,
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
EVENT_SMOKE_CONFIRMATION = "RUN_SYNTHETIC_EVENT_2_ARM_SMOKE"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def synthetic_event_smoke_context() -> HumanisticPilotContext:
    """Return an in-code, non-locked event context mirroring the failed shape."""
    return HumanisticPilotContext.model_validate(
        {
            "schema_version": "humanistic_pilot_context_v1",
            "context_id": "HIV1-S98",
            "split": "dev",
            "category": "event",
            "scenario_id": "synthetic-event-reliability-smoke-only",
            "status": "provisional_synthetic",
            "privacy": "synthetic_no_personal_data",
            "visible_history": [
                {
                    "turn_id": 7,
                    "turn_index": 7,
                    "stage_code": "s5_dynamic_adjustment",
                    "speaker": "user",
                    "content": "近一个月的满意度上升，所以我认为新流程有效。",
                    "content_type": "interview_answer",
                }
            ],
            "latest_user_turn_id": 7,
            "frozen_plan": {
                "response_intent": "assess_answer",
                "action": "RELEASE_EVENT",
                "active_topic": "信息依据",
                "target_dimension": "evidence_evaluation",
                "target_evidence": "说明证据来源、充分性或核实方式",
                "release_event_code": "counter_evidence",
                "release_unit_code": "evt_sample_drop",
                "delivery_mode": "event_link",
                "question_intent": "发布样本量变化并询问如何重新判断",
                "reflection_basis_turn_ids": [7],
                "reason": "仅用于两臂事件可靠性冒烟诊断的固定计划",
                "budget": {
                    "used_turns": 7,
                    "remaining_turns": 5,
                    "reserved_update_turns": 0,
                    "reserved_closure_turns": 1,
                },
            },
            "plan_protected_fields": [
                "response_intent",
                "action",
                "target_dimension",
                "delivery_mode",
                "question_intent",
                "release_event_code",
                "release_unit_code",
            ],
            "event_unit": {
                "event_code": "counter_evidence",
                "node_code": "s5_dynamic_adjustment",
                "unit_code": "evt_sample_drop",
                "text": "同期有效样本量下降一半。",
                "counterevidence_direction": "risk",
            },
            "allowed_facts": [
                "近一个月满意度上升",
                "用户据此认为新流程有效",
                "同期有效样本量下降一半。",
            ],
            "reflection_review": {
                "turn_ids": [7],
                "supported_summary": "用户用满意度上升支持流程有效判断",
                "unsupported_inferences": [
                    "不推断数据造假、流程无效或结论必然改变。"
                ],
            },
            "formal_answer": True,
        }
    )


def event_smoke_preflight_lock(
    protocol: GenerationProtocol,
    prompt_sources: dict[str, PromptSource],
) -> tuple[str, dict[str, object]]:
    context = synthetic_event_smoke_context()
    payload = {
        "schema_version": "humanistic_event_smoke_preflight_v1",
        "sources": {
            "event_smoke_cli_sha256": sha256_file(Path(__file__).resolve()),
            "base_smoke_cli_sha256": sha256_file(BASE_SMOKE_PATH),
            "generator_sha256": sha256_file(GENERATOR_PATH),
            "prompt_registry_sha256": sha256_file(PROMPT_REGISTRY_PATH),
            "interviewer_sha256": sha256_file(INTERVIEWER_PATH),
            "output_contract_module_sha256": sha256_file(
                OUTPUT_CONTRACT_PATH
            ),
            "validator_sha256": sha256_file(VALIDATOR_PATH),
            "model_gateway_sha256": sha256_file(MODEL_GATEWAY_PATH),
            "config_sha256": sha256_file(CONFIG_PATH),
            "synthetic_event_context_sha256": _sha256_text(
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one synthetic RELEASE_EVENT baseline/humanistic smoke without "
            "loading frozen, locked, or review-example assets."
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
        preflight_sha, preflight_lock = event_smoke_preflight_lock(
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
        blockers.append("event smoke preflight SHA-256 confirmation mismatch")
    if args.confirmation != EVENT_SMOKE_CONFIRMATION:
        blockers.append("synthetic event two-arm smoke confirmation missing")
    output_dir: Path | None = None
    if not args.output_dir:
        blockers.append("private event smoke output directory is required")
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
        context=synthetic_event_smoke_context(),
    )
    output_path = write_private_smoke_audit(audit, output_dir)
    status = str(audit["status"])
    print(
        json.dumps(
            {
                "status": status,
                "will_call_model": True,
                "remote_call_count": len(audit["arm_audits"]),
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
