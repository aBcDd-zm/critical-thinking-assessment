#!/usr/bin/env python3
"""Preflight or explicitly execute the frozen 48x3 blind candidate batch."""

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
    GenerationSourceHashes,
    PromptSource,
    StrictFrozenInterviewerRenderer,
    generate_candidate_batch,
    load_frozen_generation_contexts,
    load_frozen_prompt_sources,
    sha256_file,
    validate_real_generation_environment,
    validate_private_output_path,
    write_blocked_audit,
    write_complete_batch,
)
from app.agents.interviewer_output_contract import (
    INTERVIEWER_OUTPUT_CONTRACT_SHA256,
    INTERVIEWER_OUTPUT_CONTRACT_VERSION,
)
from app.core.config import get_settings


CONTEXT_MANIFEST_PATH = (
    BACKEND_ROOT
    / "tests"
    / "fixtures"
    / "humanistic_interviewer"
    / "pilot_context_manifest_v1.json"
)
PROMPT_REGISTRY_PATH = BACKEND_ROOT / "seeds" / "prompts.yaml"
GENERATOR_PATH = (
    BACKEND_ROOT / "app" / "agents" / "humanistic_candidate_generation.py"
)
GENERATOR_CLI_PATH = Path(__file__).resolve()
INTERVIEWER_AGENT_PATH = BACKEND_ROOT / "app" / "agents" / "interviewer_agent.py"
OUTPUT_CONTRACT_PATH = (
    BACKEND_ROOT / "app" / "agents" / "interviewer_output_contract.py"
)
VALIDATOR_PATH = (
    BACKEND_ROOT / "app" / "agents" / "interview_question_validator.py"
)
CONTEXT_ADAPTER_PATH = (
    BACKEND_ROOT / "app" / "agents" / "humanistic_evaluation_context.py"
)
MODEL_GATEWAY_PATH = BACKEND_ROOT / "app" / "services" / "model_gateway_service.py"
CONFIG_PATH = BACKEND_ROOT / "app" / "core" / "config.py"
EXECUTION_CONFIRMATION = "GENERATE_48X3"


def _report(status: str, **values: object) -> dict[str, object]:
    return {
        "schema_version": "humanistic_candidate_generation_preflight_v1",
        "status": status,
        **values,
    }


def _preflight_fingerprint(
    source_hashes: GenerationSourceHashes,
    protocol: GenerationProtocol,
    prompt_sources: dict[str, PromptSource],
) -> tuple[str, dict[str, object]]:
    prompt_lock = {
        arm: {
            "template_code": source.template_code,
            "version": source.version,
            "content_sha256": source.content_sha256,
        }
        for arm, source in sorted(prompt_sources.items())
    }
    payload = {
        "schema_version": "humanistic_candidate_preflight_lock_v1",
        "source_hashes": source_hashes.model_dump(mode="json"),
        "protocol": protocol.model_dump(mode="json"),
        "prompt_sources": prompt_lock,
        "output_contract": {
            "version": INTERVIEWER_OUTPUT_CONTRACT_VERSION,
            "sha256": INTERVIEWER_OUTPUT_CONTRACT_SHA256,
        },
        "output_policy": {
            "must_be_outside_git_repository": True,
            "directory_mode": "0700",
            "file_mode": "0600",
            "existing_output_refused": True,
        },
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest(), payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the frozen Humanistic Interviewer candidate-generation "
            "contract. No model is called unless --execute-real, the manifest "
            "hash, complete preflight hash, and explicit confirmation token "
            "are all supplied."
        )
    )
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help="Perform the real 48x3 generation after every preflight gate passes",
    )
    parser.add_argument(
        "--expected-context-manifest-sha",
        help="Required exact SHA-256 of the frozen context manifest for execution",
    )
    parser.add_argument(
        "--expected-preflight-sha",
        help=(
            "Required SHA-256 of the complete source, Prompt, protocol, and "
            "output-policy preflight lock"
        ),
    )
    parser.add_argument(
        "--confirmation",
        help=f"Required literal token for execution: {EXECUTION_CONFIRMATION}",
    )
    parser.add_argument(
        "--output-dir",
        help="New output directory; existing paths are never overwritten",
    )
    args = parser.parse_args(argv)

    try:
        _, records = load_frozen_generation_contexts(
            CONTEXT_MANIFEST_PATH,
            repo_root=REPO_ROOT,
        )
        prompt_sources = load_frozen_prompt_sources(PROMPT_REGISTRY_PATH)
        settings = get_settings()
        protocol = GenerationProtocol(
            provider=settings.MODEL_PROVIDER,
            model=settings.DEEPSEEK_MODEL,
        )
        validate_real_generation_environment(protocol, settings)
        context_manifest_sha = sha256_file(CONTEXT_MANIFEST_PATH)
        source_hashes = GenerationSourceHashes(
            context_manifest_sha256=context_manifest_sha,
            generator_sha256=sha256_file(GENERATOR_PATH),
            generator_cli_sha256=sha256_file(GENERATOR_CLI_PATH),
            prompt_registry_sha256=sha256_file(PROMPT_REGISTRY_PATH),
            interviewer_agent_sha256=sha256_file(INTERVIEWER_AGENT_PATH),
            output_contract_module_sha256=sha256_file(
                OUTPUT_CONTRACT_PATH
            ),
            validator_sha256=sha256_file(VALIDATOR_PATH),
            context_adapter_sha256=sha256_file(CONTEXT_ADAPTER_PATH),
            model_gateway_sha256=sha256_file(MODEL_GATEWAY_PATH),
            config_sha256=sha256_file(CONFIG_PATH),
        )
        preflight_sha, preflight_lock = _preflight_fingerprint(
            source_hashes,
            protocol,
            prompt_sources,
        )
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                _report(
                    "BLOCKED",
                    will_call_model=False,
                    error=f"{type(exc).__name__}: {exc}",
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    if not args.execute_real:
        print(
            json.dumps(
                _report(
                    "READY",
                    will_call_model=False,
                    context_count=len(records),
                    expected_candidate_count=len(records) * 3,
                    context_manifest_sha256=context_manifest_sha,
                    preflight_sha256=preflight_sha,
                    provider=protocol.provider,
                    model=protocol.model,
                    normal_remote_model_calls=len(records) * 2,
                    maximum_remote_model_calls=(
                        len(records) * 2 * protocol.max_paired_rounds
                    ),
                    preflight_lock=preflight_lock,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    blockers: list[str] = []
    if args.expected_context_manifest_sha != context_manifest_sha:
        blockers.append("frozen context manifest SHA-256 confirmation mismatch")
    if args.expected_preflight_sha != preflight_sha:
        blockers.append("complete preflight SHA-256 confirmation mismatch")
    if args.confirmation != EXECUTION_CONFIRMATION:
        blockers.append("explicit 48x3 generation confirmation token missing")
    output_dir: Path | None = None
    if not args.output_dir:
        blockers.append("output directory is required for real execution")
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
                _report(
                    "BLOCKED",
                    will_call_model=False,
                    blockers=blockers,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    assert output_dir is not None
    renderer = StrictFrozenInterviewerRenderer(settings)
    batch = generate_candidate_batch(
        records,
        renderer=renderer,
        prompt_sources=prompt_sources,
        protocol=protocol,
        source_hashes=source_hashes,
        enforce_production_count=True,
    )
    if batch.manifest.status == "complete":
        write_complete_batch(batch, output_dir, repo_root=REPO_ROOT)
        status = "COMPLETE"
        exit_code = 0
    else:
        write_blocked_audit(batch, output_dir, repo_root=REPO_ROOT)
        status = "BLOCKED"
        exit_code = 2
    print(
        json.dumps(
            _report(
                status,
                will_call_model=True,
                run_id=batch.manifest.run_id,
                context_count=batch.manifest.context_count,
                attempted_context_count=(
                    batch.manifest.attempted_context_count
                ),
                candidate_count=batch.manifest.candidate_count,
                failure_count=batch.manifest.failure_count,
                blocked_context_ids=batch.manifest.blocked_context_ids,
                stop_reason=batch.manifest.stop_reason,
                stop_context_id=batch.manifest.stop_context_id,
                output_dir=str(output_dir),
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
