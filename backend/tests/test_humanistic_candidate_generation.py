from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import random
import stat
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents import humanistic_candidate_generation as candidate_generation
from app.agents.humanistic_candidate_generation import (
    ArmGenerationResult,
    BlindReviewCase,
    CandidateArmFailure,
    GenerationProtocol,
    GenerationSourceHashes,
    PromptSource,
    StrictFrozenInterviewerRenderer,
    build_candidate_arm_request,
    generate_candidate_batch,
    load_frozen_generation_contexts,
    load_frozen_prompt_sources,
    validate_candidate_batch,
    validate_private_output_path,
    validate_real_generation_environment,
    write_blocked_audit,
    write_complete_batch,
)
from app.agents.humanistic_evaluation_context import HumanisticPilotContext
from app.agents.interview_question_validator import INTERNAL_TERMS
from app.agents.interviewer_agent import (
    CANDIDATE_EVENT_SHAPE_INSTRUCTION,
    CANDIDATE_GENERATION_MODE,
    CANDIDATE_RELIABILITY_INSTRUCTION,
    InterviewerAgent,
)
from app.agents.interviewer_output_contract import (
    INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION,
    INTERVIEWER_OUTPUT_CONTRACT_SHA256,
    INTERVIEWER_OUTPUT_CONTRACT_VERSION,
    INTERVIEWER_OUTPUT_REQUIRED_FIELDS,
    OutputContractIssue,
)
from app.agents.progressive_schemas import (
    InterviewQualityFlags,
    InterviewerOutput,
)
from app.core.config import Settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import (
    ModelGatewayService,
    ModelIdentityResponseError,
)
from scripts import generate_humanistic_blind_candidates_v1 as candidate_cli
from scripts import smoke_humanistic_action_matrix_v1 as matrix_smoke_cli
from scripts import smoke_humanistic_event_candidate_arms_v1 as event_smoke_cli
from scripts import smoke_humanistic_candidate_arms_v1 as smoke_cli


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _unit_context(
    index: int,
    *,
    split: str = "dev",
    status: str = "frozen_v1",
) -> HumanisticPilotContext:
    chinese_number = "一" if index == 1 else "二"
    return HumanisticPilotContext.model_validate(
        {
            "schema_version": "humanistic_pilot_context_v1",
            "context_id": f"HIV1-U{index:02d}",
            "split": split,
            "category": "opening",
            "scenario_id": f"unit-scenario-{index:02d}",
            "status": status,
            "privacy": "synthetic_no_personal_data",
            "visible_history": [
                {
                    "turn_id": 1,
                    "turn_index": 1,
                    "stage_id": index,
                    "stage_code": "s1_problem_definition",
                    "speaker": "user",
                    "content": f"单元情境{chinese_number}的合成用户回答。",
                    "content_type": "interview_answer",
                }
            ],
            "latest_user_turn_id": 1,
            "frozen_plan": {
                "response_intent": "assess_answer",
                "action": "PROBE",
                "active_topic": "合成任务界定",
                "target_dimension": "problem_definition",
                "target_evidence": "说明首先需要界定的问题",
                "delivery_mode": "reflective_probe",
                "question_intent": f"询问合成参与者{chinese_number}会先界定什么",
                "reflection_basis_turn_ids": [1],
                "reason": "仅供生成器单元测试的固定计划",
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
            "allowed_facts": [f"单元情境{chinese_number}只包含合成信息。"],
            "reflection_review": {
                "turn_ids": [1],
                "supported_summary": f"用户只表达了单元情境{chinese_number}的回答。",
                "unsupported_inferences": ["不推断情绪、人格或隐藏动机。"],
            },
            "formal_answer": True,
        }
    )


def _quality_flags() -> InterviewQualityFlags:
    return InterviewQualityFlags(
        single_focus=True,
        faithful_reflection=True,
        non_judgmental=True,
        non_leading=True,
        no_internal_terms=True,
        no_unreleased_facts=True,
    )


class FakeRenderer:
    """Pure in-memory renderer; it never constructs or calls a model gateway."""

    def __init__(
        self,
        *,
        failures_before_success: dict[tuple[str, str], int] | None = None,
    ) -> None:
        self.failures_before_success = dict(failures_before_success or {})
        self.requests = []
        self.calls: Counter[tuple[str, str]] = Counter()

    def render(self, request):
        self.requests.append(request)
        content = request.renderer_input["specified_user_turn"]["content"]
        key = (content, request.arm)
        self.calls[key] += 1
        remaining = self.failures_before_success.get(key, 0)
        if remaining > 0:
            self.failures_before_success[key] = remaining - 1
            raise CandidateArmFailure(
                "synthetic_renderer_failure",
                validation_codes=["UNIT_ONLY"],
                raw_output="synthetic-invalid-output",
                provider="unit-provider",
                model="unit-model",
                duration_ms=1,
            )
        variant = {"baseline": "甲", "humanistic": "乙", "fallback": "丙"}[
            request.arm
        ]
        message = f"基于这条合成回答，你会先确认哪项信息{variant}？"
        output = InterviewerOutput(
            message=message,
            message_type="followup",
            question_count=1,
            quality_flags=_quality_flags(),
            fallback_used=request.arm == "fallback",
        )
        return ArmGenerationResult(
            output=output,
            raw_output=output.model_dump_json(),
            provider=(None if request.arm == "fallback" else "unit-provider"),
            model=(None if request.arm == "fallback" else "unit-model"),
            duration_ms=1,
        )


class ExactTieRenderer(FakeRenderer):
    def render(self, request):
        self.requests.append(request)
        content = request.renderer_input["specified_user_turn"]["content"]
        key = (content, request.arm)
        self.calls[key] += 1
        message = (
            "两条模型臂完全相同，你会先确认哪项信息？"
            if request.arm in {"baseline", "humanistic"}
            else "确定性兜底保持不同，你会怎样继续？"
        )
        output = InterviewerOutput(
            message=message,
            message_type="followup",
            question_count=1,
            quality_flags=_quality_flags(),
            fallback_used=request.arm == "fallback",
        )
        return ArmGenerationResult(
            output=output,
            raw_output=output.model_dump_json(),
            provider=(None if request.arm == "fallback" else "unit-provider"),
            model=(None if request.arm == "fallback" else "unit-model"),
            duration_ms=1,
        )


class NormalizedCollisionRenderer(FakeRenderer):
    def render(self, request):
        self.requests.append(request)
        content = request.renderer_input["specified_user_turn"]["content"]
        key = (content, request.arm)
        self.calls[key] += 1
        message = {
            "baseline": "归一化碰撞，你会先确认什么？",
            "humanistic": "归一化碰撞 你会先确认什么?",
            "fallback": "确定性兜底不同，你会怎样继续？",
        }[request.arm]
        output = InterviewerOutput(
            message=message,
            message_type="followup",
            question_count=1,
            quality_flags=_quality_flags(),
            fallback_used=request.arm == "fallback",
        )
        return ArmGenerationResult(
            output=output,
            raw_output=output.model_dump_json(),
            provider=(None if request.arm == "fallback" else "unit-provider"),
            model=(None if request.arm == "fallback" else "unit-model"),
            duration_ms=1,
        )


class FallbackCollisionRenderer(FakeRenderer):
    def __init__(self, *, three_way: bool = False) -> None:
        super().__init__()
        self.three_way = three_way

    def render(self, request):
        self.requests.append(request)
        content = request.renderer_input["specified_user_turn"]["content"]
        key = (content, request.arm)
        self.calls[key] += 1
        if self.three_way:
            message = "三臂完全相同，你会先确认什么？"
        else:
            message = {
                "baseline": "模型甲与兜底相同，你会确认什么？",
                "humanistic": "模型乙保持不同，你会确认什么？",
                "fallback": "模型甲与兜底相同，你会确认什么？",
            }[request.arm]
        output = InterviewerOutput(
            message=message,
            message_type="followup",
            question_count=1,
            quality_flags=_quality_flags(),
            fallback_used=request.arm == "fallback",
        )
        return ArmGenerationResult(
            output=output,
            raw_output=output.model_dump_json(),
            provider=(None if request.arm == "fallback" else "unit-provider"),
            model=(None if request.arm == "fallback" else "unit-model"),
            duration_ms=1,
        )


class FatalIdentityRenderer:
    def __init__(self) -> None:
        self.requests = []

    def render(self, request):
        self.requests.append(request)
        raise CandidateArmFailure(
            "model_identity_mismatch",
            validation_codes=["MODEL_IDENTITY_MISMATCH"],
            raw_output="unit-only-wrong-model-output",
            provider="unit-provider",
            model="wrong-model",
            duration_ms=1,
            fatal=True,
        )


class CrossRoundRenderer:
    """Each model arm succeeds in a different paired round."""

    def __init__(self) -> None:
        self.requests = []
        self.calls: Counter[str] = Counter()

    def render(self, request):
        self.requests.append(request)
        self.calls[request.arm] += 1
        call_number = self.calls[request.arm]
        if (
            request.arm == "humanistic" and call_number == 1
        ) or (
            request.arm == "baseline" and call_number > 1
        ):
            raise CandidateArmFailure(
                "synthetic_cross_round_failure",
                validation_codes=["UNIT_ONLY"],
                raw_output="synthetic-cross-round-invalid",
                provider="unit-provider",
                model="unit-model",
                duration_ms=1,
            )
        variant = {"baseline": "甲", "humanistic": "乙", "fallback": "丙"}[
            request.arm
        ]
        output = InterviewerOutput(
            message=f"跨轮合成候选{variant}，你会先确认哪项信息？",
            message_type="followup",
            question_count=1,
            quality_flags=_quality_flags(),
            fallback_used=request.arm == "fallback",
        )
        return ArmGenerationResult(
            output=output,
            raw_output=output.model_dump_json(),
            provider=(None if request.arm == "fallback" else "unit-provider"),
            model=(None if request.arm == "fallback" else "unit-model"),
            duration_ms=1,
        )


class RejectingSmokeRenderer:
    def __init__(self) -> None:
        self.requests = []

    def render(self, request):
        self.requests.append(request)
        raise CandidateArmFailure(
            "validator_rejected",
            validation_codes=["missing_reflection"],
            quality_flag_mismatches=[
                "faithful_reflection:claimed_true_but_failed:missing_reflection"
            ],
            raw_output='{"message":"unit-only rejected raw output"}',
            provider="unit-provider",
            model="unit-model",
            duration_ms=1,
        )


class ContractRejectingRenderer:
    def __init__(self) -> None:
        self.requests = []

    def render(self, request):
        self.requests.append(request)
        raise CandidateArmFailure(
            "output_contract_invalid",
            contract_errors=[
                OutputContractIssue(path=["message_type"], code="missing")
            ],
            raw_output='{"message":"unit-only incomplete output"}',
            provider="unit-provider",
            model="unit-model",
            duration_ms=1,
        )


class DeterministicIdFactory:
    def __init__(self) -> None:
        self.counters: Counter[str] = Counter()

    def _next(self, prefix: str) -> str:
        self.counters[prefix] += 1
        return f"{prefix}_{self.counters[prefix]:032x}"

    def run_id(self) -> str:
        return self._next("run")

    def case_id(self) -> str:
        return self._next("case")

    def candidate_id(self) -> str:
        return self._next("cand")

    def attempt_id(self) -> str:
        return self._next("attempt")

    def collision_id(self) -> str:
        return self._next("collision")


def _prompt_sources() -> dict[str, PromptSource]:
    result = {}
    for arm in candidate_generation.MODEL_ARMS:
        content = f"unit-only-{arm}-prompt"
        version = candidate_generation.PROMPT_VERSION_BY_ARM[arm]
        result[arm] = PromptSource(
            arm=arm,
            template_code=version,
            version=version,
            content=content,
            content_sha256=_sha256_text(content),
        )
    return result


def _protocol() -> GenerationProtocol:
    return GenerationProtocol(provider="unit-provider", model="unit-model")


def _source_hashes() -> GenerationSourceHashes:
    return GenerationSourceHashes(
        context_manifest_sha256="1" * 64,
        generator_sha256="2" * 64,
        generator_cli_sha256="3" * 64,
        prompt_registry_sha256="4" * 64,
        interviewer_agent_sha256="5" * 64,
        output_contract_module_sha256="6" * 64,
        validator_sha256="7" * 64,
        context_adapter_sha256="8" * 64,
        model_gateway_sha256="9" * 64,
        config_sha256="a" * 64,
    )


def _generate(records, renderer):
    return generate_candidate_batch(
        records,
        renderer=renderer,
        prompt_sources=_prompt_sources(),
        protocol=_protocol(),
        source_hashes=_source_hashes(),
        id_factory=DeterministicIdFactory(),
        rng=random.Random(20260728),
        enforce_production_count=False,
    )


class HumanisticCandidateGenerationTests(unittest.TestCase):
    def test_cg_rj01_only_frozen_contexts_and_production_count_are_accepted(
        self,
    ) -> None:
        provisional = _unit_context(1, status="provisional_synthetic")
        with self.assertRaisesRegex(ValueError, "only accepts frozen_v1"):
            _generate([provisional], FakeRenderer())
        with self.assertRaisesRegex(ValueError, "requires 48 contexts"):
            generate_candidate_batch(
                [_unit_context(1)],
                renderer=FakeRenderer(),
                prompt_sources=_prompt_sources(),
                protocol=_protocol(),
                source_hashes=_source_hashes(),
            )

    def test_cg_rj02_real_environment_must_match_frozen_protocol(self) -> None:
        base = {
            "_env_file": None,
            "MODEL_GATEWAY_MODE": "real",
            "DEEPSEEK_API_KEY": "unit-only-key",
            "MODEL_PROVIDER": "unit-provider",
            "DEEPSEEK_MODEL": "unit-model",
            "CANDIDATE_GENERATION_TIMEOUT_SECONDS": 15,
        }
        validate_real_generation_environment(_protocol(), Settings(**base))
        drift_cases = (
            ({"MODEL_GATEWAY_MODE": "mock"}, "MODE=real"),
            ({"DEEPSEEK_API_KEY": ""}, "configured API key"),
            ({"MODEL_PROVIDER": "other"}, "provider differs"),
            ({"DEEPSEEK_MODEL": "other"}, "model differs"),
            ({"CANDIDATE_GENERATION_TIMEOUT_SECONDS": 14}, "timeout differs"),
        )
        for changes, message in drift_cases:
            with self.subTest(changes=changes):
                values = {**base, **changes}
                with self.assertRaisesRegex(ValueError, message):
                    validate_real_generation_environment(
                        _protocol(), Settings(**values)
                    )
        protocol_drift_cases = (
            {"temperature": 0.3},
            {"max_tokens": 701},
            {"json_mode": False},
            {"thinking_enabled": True},
            {"reasoning_effort": "high"},
            {"timeout_seconds": 14},
            {"max_paired_rounds": 4},
            {"model_attempts_per_arm_per_round": 2},
            {"retry_selection_policy": "same_round_only"},
            {"baseline_repair_enabled": True},
            {"model_failure_substitutes_fallback": True},
            {"prompt_source": "database"},
            {"case_and_arm_double_blind": False},
            {"output_contract_version": "drifted"},
            {"output_contract_sha256": "0" * 64},
        )
        for changes in protocol_drift_cases:
            with self.subTest(protocol_changes=changes):
                with self.assertRaises(ValidationError):
                    GenerationProtocol(
                        provider="unit-provider",
                        model="unit-model",
                        **changes,
                    )

    def test_cg_rj03_prompt_registry_requires_exact_active_pair(self) -> None:
        templates = []
        for arm in candidate_generation.MODEL_ARMS:
            version = candidate_generation.PROMPT_VERSION_BY_ARM[arm]
            templates.append(
                {
                    "agent_name": "interviewer",
                    "template_code": version,
                    "version": version,
                    "status": "active",
                    "output_contract_version": (
                        INTERVIEWER_OUTPUT_CONTRACT_VERSION
                    ),
                    "output_schema_json": {
                        "required": list(INTERVIEWER_OUTPUT_REQUIRED_FIELDS)
                    },
                    "content": f"unit {arm}",
                }
            )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "prompts.yaml"
            path.write_text(
                yaml.safe_dump({"templates": templates}, allow_unicode=True),
                encoding="utf-8",
            )
            self.assertEqual(set(load_frozen_prompt_sources(path)), {"baseline", "humanistic"})

            path.write_text(
                yaml.safe_dump(
                    {"templates": templates + [dict(templates[0])]},
                    allow_unicode=True,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly one frozen baseline"):
                load_frozen_prompt_sources(path)

            drifted = [dict(item) for item in templates]
            drifted[1]["version"] = "drifted"
            path.write_text(
                yaml.safe_dump({"templates": drifted}, allow_unicode=True),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly one frozen humanistic"):
                load_frozen_prompt_sources(path)

    def test_cg_rj04_strict_renderer_never_repairs_or_substitutes(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        renderer = StrictFrozenInterviewerRenderer(settings)
        renderer.agent._call = MagicMock(  # noqa: SLF001
            return_value=("not-json", "unit-model")
        )
        renderer.agent._fallback = MagicMock()  # noqa: SLF001
        request = build_candidate_arm_request(
            _unit_context(1), "baseline", _prompt_sources()
        )

        with self.assertRaisesRegex(
            CandidateArmFailure,
            "output_contract_invalid",
        ) as invalid:
            renderer.render(request)

        self.assertEqual(
            [item.model_dump(mode="json") for item in invalid.exception.contract_errors],
            [{"path": [], "code": "json_invalid"}],
        )

        renderer.agent._call.assert_called_once()  # noqa: SLF001
        self.assertIsNone(renderer.agent._call.call_args.kwargs["repair"])  # noqa: SLF001
        renderer.agent._fallback.assert_not_called()  # noqa: SLF001

        renderer.agent._call = MagicMock(  # noqa: SLF001
            return_value=("{}", "unexpected-model")
        )
        with self.assertRaisesRegex(
            CandidateArmFailure, "model_identity_mismatch"
        ) as mismatch:
            renderer.render(request)
        self.assertTrue(mismatch.exception.fatal)
        renderer.agent._fallback.assert_not_called()  # noqa: SLF001

        renderer.agent._call = MagicMock(  # noqa: SLF001
            return_value=("{}", None)
        )
        with self.assertRaisesRegex(
            CandidateArmFailure, "model_identity_missing"
        ) as missing:
            renderer.render(request)
        self.assertTrue(missing.exception.fatal)
        renderer.agent._fallback.assert_not_called()  # noqa: SLF001

        renderer.agent._call = MagicMock(  # noqa: SLF001
            side_effect=ModelIdentityResponseError(
                raw_output='{"message":"unit-only raw response"}'
            )
        )
        with self.assertRaisesRegex(
            CandidateArmFailure, "model_identity_missing"
        ) as gateway_missing:
            renderer.render(request)
        self.assertTrue(gateway_missing.exception.fatal)
        self.assertEqual(
            gateway_missing.exception.raw_output,
            '{"message":"unit-only raw response"}',
        )
        renderer.agent._fallback.assert_not_called()  # noqa: SLF001

        renderer.agent._fallback = MagicMock(  # noqa: SLF001
            side_effect=RuntimeError("unit-only")
        )
        fallback_request = build_candidate_arm_request(
            _unit_context(1), "fallback", _prompt_sources()
        )
        with self.assertRaisesRegex(
            CandidateArmFailure, "deterministic_fallback_exception"
        ):
            renderer.render(fallback_request)

    def test_shared_output_contract_is_identical_for_both_model_arms(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        requests = []

        async def fake_chat(service, payload):
            del service
            requests.append(payload)
            return SimpleNamespace(content="{}", model="unit-model")

        agent = InterviewerAgent()
        with (
            patch(
                "app.agents.interviewer_agent.get_settings",
                return_value=settings,
            ),
            patch.object(ModelGatewayService, "chat", new=fake_chat),
        ):
            agent._call(  # noqa: SLF001
                {"generation_mode": CANDIDATE_GENERATION_MODE},
                "baseline-style-content",
                "baseline_v1",
                3,
                repair=None,
            )
            agent._call(  # noqa: SLF001
                {"generation_mode": CANDIDATE_GENERATION_MODE},
                "humanistic-style-content",
                "humanistic_v1",
                3,
                repair=None,
            )

        self.assertEqual(len(requests), 2)
        instructions = [
            json.loads(item.messages[1].content)["instruction"]
            for item in requests
        ]
        self.assertTrue(
            all(
                item.endswith(INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION)
                for item in instructions
            )
        )
        self.assertNotEqual(instructions[0], instructions[1])
        self.assertTrue(
            all(CANDIDATE_RELIABILITY_INSTRUCTION in item for item in instructions)
        )
        self.assertTrue(
            all(
                "、".join(sorted(INTERNAL_TERMS)) in item
                for item in instructions
            )
        )
        self.assertTrue(
            all(
                "introduced_fact_codes 必须且只能包含 release_unit_code"
                in item
                for item in instructions
            )
        )
        self.assertEqual(
            INTERVIEWER_OUTPUT_CONTRACT_VERSION,
            "interviewer_output_contract_v1",
        )
        self.assertRegex(INTERVIEWER_OUTPUT_CONTRACT_SHA256, r"^[0-9a-f]{64}$")
        for forbidden_style_term in ("baseline", "humanistic", "温暖", "共情"):
            self.assertNotIn(
                forbidden_style_term,
                INTERVIEWER_OUTPUT_CONTRACT_INSTRUCTION,
            )

    def test_v3_candidate_constraint_is_marker_scoped_and_arm_symmetric(self) -> None:
        requests = [
            build_candidate_arm_request(
                _unit_context(1), arm, _prompt_sources()
            )
            for arm in ("baseline", "humanistic")
        ]
        self.assertEqual(
            {item.renderer_input["generation_mode"] for item in requests},
            {CANDIDATE_GENERATION_MODE},
        )

        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        captured = []

        async def fake_chat(service, payload):
            del service
            captured.append(payload)
            return SimpleNamespace(content="{}", model="unit-model")

        agent = InterviewerAgent()
        with (
            patch(
                "app.agents.interviewer_agent.get_settings",
                return_value=settings,
            ),
            patch.object(ModelGatewayService, "chat", new=fake_chat),
        ):
            for request in requests:
                agent._call(  # noqa: SLF001
                    request.renderer_input,
                    request.prompt_content,
                    request.style_version,
                    15,
                    repair=None,
                )
            agent._call(  # noqa: SLF001
                {"validated_plan": {"action": "PROBE"}},
                "live-style-content",
                "baseline_v1",
                3,
                repair=None,
            )

        instructions = [
            json.loads(item.messages[1].content)["instruction"]
            for item in captured
        ]
        self.assertIn(CANDIDATE_RELIABILITY_INSTRUCTION, instructions[0])
        self.assertIn(CANDIDATE_RELIABILITY_INSTRUCTION, instructions[1])
        self.assertNotIn(CANDIDATE_RELIABILITY_INSTRUCTION, instructions[2])

    def test_v4_event_shape_is_candidate_only_and_arm_symmetric(self) -> None:
        event_context = event_smoke_cli.synthetic_event_smoke_context()
        event_requests = [
            build_candidate_arm_request(
                event_context,
                arm,
                _prompt_sources(),
            )
            for arm in ("baseline", "humanistic")
        ]
        probe_request = build_candidate_arm_request(
            _unit_context(1),
            "baseline",
            _prompt_sources(),
        )
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        captured = []

        async def fake_chat(service, payload):
            del service
            captured.append(payload)
            return SimpleNamespace(content="{}", model="unit-model")

        agent = InterviewerAgent()
        with (
            patch(
                "app.agents.interviewer_agent.get_settings",
                return_value=settings,
            ),
            patch.object(ModelGatewayService, "chat", new=fake_chat),
        ):
            for request in event_requests:
                agent._call(  # noqa: SLF001
                    request.renderer_input,
                    request.prompt_content,
                    request.style_version,
                    15,
                    repair=None,
                )
            agent._call(  # noqa: SLF001
                probe_request.renderer_input,
                probe_request.prompt_content,
                probe_request.style_version,
                15,
                repair=None,
            )
            live_input = dict(event_requests[0].renderer_input)
            live_input.pop("generation_mode")
            agent._call(  # noqa: SLF001
                live_input,
                event_requests[0].prompt_content,
                event_requests[0].style_version,
                3,
                repair=None,
            )

        instructions = [
            json.loads(item.messages[1].content)["instruction"]
            for item in captured
        ]
        self.assertIn(CANDIDATE_EVENT_SHAPE_INSTRUCTION, instructions[0])
        self.assertIn(CANDIDATE_EVENT_SHAPE_INSTRUCTION, instructions[1])
        self.assertNotIn(CANDIDATE_EVENT_SHAPE_INSTRUCTION, instructions[2])
        self.assertNotIn(CANDIDATE_EVENT_SHAPE_INSTRUCTION, instructions[3])
        self.assertIn("必须使用分号", instructions[0])
        self.assertIn("必须使用分号", instructions[1])

    def test_v4_event_semicolon_shape_matches_existing_validator(self) -> None:
        context = event_smoke_cli.synthetic_event_smoke_context()
        quote = context.visible_history[0].content
        fact = context.event_unit.text
        validator = candidate_generation.InterviewQuestionValidator()

        def validate(message: str) -> tuple[bool, list[str]]:
            output = InterviewerOutput(
                message=message,
                message_type="event",
                question_count=1,
                introduced_fact_codes=[context.frozen_plan.release_unit_code],
                reflection_turn_ids=[context.latest_user_turn_id],
                reflection_source_quotes=[
                    {
                        "turn_id": context.latest_user_turn_id,
                        "quote": quote,
                    }
                ],
                quality_flags=_quality_flags(),
            )
            return validator.validate(
                output,
                plan=context.frozen_plan,
                allowed_fact_codes={context.frozen_plan.release_unit_code},
                previous_questions=[],
                allowed_source_turn_ids={context.latest_user_turn_id},
                source_turn_texts={context.latest_user_turn_id: quote},
                allowed_fact_text=fact,
                enforce_humanistic_safety=True,
            )

        bad_valid, bad_errors = validate(
            f"你提到“{quote}”。{fact}你会怎样重新判断？"
        )
        good_valid, good_errors = validate(
            f"你提到“{quote}”；{fact}你会怎样重新判断？"
        )
        self.assertFalse(bad_valid)
        self.assertIn("too_many_sentences", bad_errors)
        self.assertTrue(good_valid)
        self.assertEqual(good_errors, [])

    def test_v3_true_internal_term_claim_is_rejected_and_audited(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        request = build_candidate_arm_request(
            _unit_context(1), "baseline", _prompt_sources()
        )
        source_text = next(
            item.content
            for item in request.runtime_context.dialogue_history
            if item.speaker == "user"
        )
        raw = json.dumps(
            {
                "message": (
                    f"你提到“{source_text}”，为了把问题界定得更清楚，"
                    "还需要先确认哪项信息？"
                ),
                "message_type": "followup",
                "question_count": 1,
                "introduced_fact_codes": [],
                "reflection_turn_ids": [1],
                "reflection_source_quotes": [
                    {"turn_id": 1, "quote": source_text}
                ],
                "quality_flags": _quality_flags().model_dump(mode="json"),
                "fallback_used": False,
                "warnings": [],
            },
            ensure_ascii=False,
        )
        renderer = StrictFrozenInterviewerRenderer(settings)
        renderer.agent._call = MagicMock(  # noqa: SLF001
            return_value=(raw, "unit-model")
        )

        with self.assertRaisesRegex(
            CandidateArmFailure, "validator_rejected"
        ) as rejected:
            renderer.render(request)

        self.assertIn("internal_terms", rejected.exception.validation_codes)
        self.assertEqual(rejected.exception.raw_output, raw)
        self.assertIn(
            "no_internal_terms:claimed_true_but_failed:internal_terms",
            rejected.exception.quality_flag_mismatches,
        )

    def test_v3_declared_false_flag_is_not_normalized(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        request = build_candidate_arm_request(
            _unit_context(1), "baseline", _prompt_sources()
        )
        source_text = next(
            item.content
            for item in request.runtime_context.dialogue_history
            if item.speaker == "user"
        )
        flags = _quality_flags().model_dump(mode="json")
        flags["no_internal_terms"] = False
        raw = json.dumps(
            {
                "message": f"你提到“{source_text}”，你会先确认哪项信息？",
                "message_type": "followup",
                "question_count": 1,
                "introduced_fact_codes": [],
                "reflection_turn_ids": [1],
                "reflection_source_quotes": [
                    {"turn_id": 1, "quote": source_text}
                ],
                "quality_flags": flags,
                "fallback_used": False,
                "warnings": [],
            },
            ensure_ascii=False,
        )
        renderer = StrictFrozenInterviewerRenderer(settings)
        renderer.agent._call = MagicMock(  # noqa: SLF001
            return_value=(raw, "unit-model")
        )

        with self.assertRaisesRegex(
            CandidateArmFailure, "validator_rejected"
        ) as rejected:
            renderer.render(request)

        self.assertIn("quality_flags", rejected.exception.validation_codes)
        self.assertEqual(rejected.exception.raw_output, raw)
        self.assertIn(
            "no_internal_terms:declared_false",
            rejected.exception.quality_flag_mismatches,
        )

    def test_conclude_instruction_does_not_request_a_question(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        requests = []

        async def fake_chat(service, payload):
            del service
            requests.append(payload)
            return SimpleNamespace(content="{}", model="unit-model")

        agent = InterviewerAgent()
        with (
            patch(
                "app.agents.interviewer_agent.get_settings",
                return_value=settings,
            ),
            patch.object(ModelGatewayService, "chat", new=fake_chat),
        ):
            agent._call(  # noqa: SLF001
                {"validated_plan": {"action": "CONCLUDE"}},
                "baseline-style-content",
                "baseline_v1",
                15,
                repair=None,
            )

        instruction = json.loads(requests[0].messages[1].content)["instruction"]
        self.assertIn("这是结束轮", instruction)
        self.assertNotIn("再提出一个开放或聚焦问题", instruction)
        self.assertIn("question_count=0", instruction)

    def test_strict_candidate_contract_accepts_complete_raw_envelope(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        request = build_candidate_arm_request(
            _unit_context(1), "baseline", _prompt_sources()
        )
        source_text = next(
            item.content
            for item in request.runtime_context.dialogue_history
            if item.speaker == "user"
        )
        raw = json.dumps(
            {
                "message": (
                    f"你提到“{source_text}”你接下来会先界定哪项具体问题？"
                ),
                "message_type": "followup",
                "question_count": 1,
                "introduced_fact_codes": [],
                "reflection_turn_ids": [1],
                "reflection_source_quotes": [
                    {"turn_id": 1, "quote": source_text}
                ],
                "quality_flags": _quality_flags().model_dump(mode="json"),
                "fallback_used": False,
                "warnings": [],
            },
            ensure_ascii=False,
        )
        renderer = StrictFrozenInterviewerRenderer(settings)
        renderer.agent._call = MagicMock(return_value=(raw, "unit-model"))  # noqa: SLF001
        result = renderer.render(request)

        self.assertEqual(result.raw_output, raw)
        self.assertEqual(result.output.reflection_turn_ids, [1])
        self.assertEqual(result.output.warnings, [])

        live_tolerant = InterviewerAgent._parse(  # noqa: SLF001
            '{"message":"你接下来会先界定哪项具体问题？"}',
            plan=request.plan,
            unit=None,
        )
        self.assertIsNotNone(live_tolerant)
        self.assertIn(
            "normalized minimal interviewer JSON envelope",
            live_tolerant.warnings,
        )

    def test_cg_rj05_success_has_three_arms_and_complete_provenance(self) -> None:
        batch = _generate(
            [_unit_context(1), _unit_context(2, split="locked_test")],
            FakeRenderer(),
        )

        self.assertEqual(batch.manifest.status, "complete")
        self.assertEqual(batch.manifest.candidate_count, 6)
        self.assertEqual(len(batch.blind_cases), 2)
        self.assertEqual(len(batch.case_key), 2)
        self.assertEqual(len(batch.arm_key), 2)
        self.assertEqual(
            {assignment.arm for key in batch.arm_key for assignment in key.assignments},
            {"baseline", "humanistic", "fallback"},
        )
        self.assertEqual(sum(item.selected for item in batch.provenance), 6)
        self.assertTrue(
            all(
                item.output_contract_version
                == INTERVIEWER_OUTPUT_CONTRACT_VERSION
                and item.output_contract_sha256
                == INTERVIEWER_OUTPUT_CONTRACT_SHA256
                for item in batch.provenance
            )
        )
        validate_candidate_batch(batch, expected_context_count=2)

    def test_output_contract_failures_keep_field_level_audit(self) -> None:
        renderer = ContractRejectingRenderer()
        batch = _generate([_unit_context(1)], renderer)

        self.assertEqual(batch.manifest.status, "blocked")
        self.assertEqual(len(renderer.requests), 6)
        self.assertTrue(
            all(
                item.error_code == "output_contract_invalid"
                and [issue.model_dump(mode="json") for issue in item.contract_errors]
                == [{"path": ["message_type"], "code": "missing"}]
                for item in batch.failures
            )
        )
        self.assertTrue(
            all(
                item.contract_errors
                and item.output_contract_sha256
                == INTERVIEWER_OUTPUT_CONTRACT_SHA256
                for item in batch.provenance
            )
        )

    def test_cg_rj06_blind_packet_forbids_identity_and_arm_metadata(self) -> None:
        batch = _generate([_unit_context(1)], FakeRenderer())
        blind_case = batch.blind_cases[0]
        payload = blind_case.model_dump(mode="json")
        forbidden_keys = {
            "context_id",
            "split",
            "category",
            "scenario_id",
            "arm",
            "provider",
            "model",
            "prompt_version",
            "prompt_sha256",
            "output_contract_version",
            "output_contract_sha256",
            "style_version",
        }

        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value), set())
            return set()

        self.assertFalse(keys(payload) & forbidden_keys)
        self.assertNotIn("HIV1-", blind_case.model_dump_json())
        self.assertRegex(blind_case.case_id, candidate_generation.CASE_ID_PATTERN)
        for candidate in blind_case.candidates:
            self.assertRegex(
                candidate.candidate_id,
                candidate_generation.CANDIDATE_ID_PATTERN,
            )

        leaked = blind_case.model_dump(mode="json")
        leaked["review_context"]["context_id"] = "HIV1-U01"
        with self.assertRaises(ValidationError):
            BlindReviewCase.model_validate(leaked)
        leaked = blind_case.model_dump(mode="json")
        leaked["candidates"][0]["arm"] = "baseline"
        with self.assertRaises(ValidationError):
            BlindReviewCase.model_validate(leaked)

    def test_cg_rj07_global_duplicate_candidate_id_is_rejected(self) -> None:
        batch = _generate([_unit_context(1), _unit_context(2)], FakeRenderer())
        batch.blind_cases[1].candidates[0].candidate_id = (
            batch.blind_cases[0].candidates[0].candidate_id
        )
        with self.assertRaisesRegex(ValueError, "globally unique"):
            validate_candidate_batch(batch, expected_context_count=2)

    def test_cg_rj08_duplicate_candidate_text_is_rejected(self) -> None:
        batch = _generate([_unit_context(1)], FakeRenderer())
        batch.blind_cases[0].candidates[1].candidate_text = (
            batch.blind_cases[0].candidates[0].candidate_text
        )
        with self.assertRaisesRegex(ValueError, "sealed tie record"):
            validate_candidate_batch(batch, expected_context_count=1)

    def test_v5_exact_model_pair_tie_is_declared_and_accepted(self) -> None:
        record = _unit_context(1)
        renderer = ExactTieRenderer()

        batch = _generate([record], renderer)

        self.assertEqual(batch.manifest.status, "complete")
        self.assertEqual(batch.manifest.candidate_count, 3)
        self.assertEqual(batch.manifest.exact_model_tie_count, 1)
        self.assertEqual(len(batch.exact_model_ties), 1)
        texts = Counter(
            item.candidate_text for item in batch.blind_cases[0].candidates
        )
        self.assertEqual(sorted(texts.values()), [1, 2])
        content = record.visible_history[0].content
        self.assertEqual(renderer.calls[(content, "baseline")], 1)
        self.assertEqual(renderer.calls[(content, "humanistic")], 1)
        self.assertEqual(renderer.calls[(content, "fallback")], 1)

    def test_v5_rj_03_invalid_model_output_cannot_become_an_exact_tie(self) -> None:
        record = _unit_context(1)
        content = record.visible_history[0].content
        renderer = FakeRenderer(
            failures_before_success={(content, "humanistic"): 3}
        )

        batch = _generate([record], renderer)

        self.assertEqual(batch.manifest.status, "blocked")
        self.assertEqual(batch.manifest.exact_model_tie_count, 0)
        self.assertEqual(batch.exact_model_ties, [])
        self.assertEqual(renderer.calls[(content, "fallback")], 0)

    def test_v5_rj_04_model_identity_failure_cannot_become_an_exact_tie(self) -> None:
        batch = _generate([_unit_context(1)], FatalIdentityRenderer())

        self.assertEqual(batch.manifest.status, "blocked")
        self.assertEqual(batch.manifest.stop_reason, "fatal_model_identity_mismatch")
        self.assertEqual(batch.exact_model_ties, [])

    def test_v5_rj_05_fallback_collision_remains_blocked(self) -> None:
        batch = _generate([_unit_context(1)], FallbackCollisionRenderer())

        self.assertEqual(batch.manifest.status, "blocked")
        collisions = [
            item for item in batch.failures
            if item.error_code == "candidate_text_collision"
        ]
        self.assertEqual(len(collisions), 3)
        self.assertTrue(
            all(item.collision_scope == "fallback_involved" for item in collisions)
        )

    def test_v5_rj_06_normalized_only_collision_is_blocked_and_caches_fallback(
        self,
    ) -> None:
        record = _unit_context(1)
        renderer = NormalizedCollisionRenderer()

        batch = _generate([record], renderer)

        self.assertEqual(batch.manifest.status, "blocked")
        self.assertEqual(batch.manifest.stop_reason, "candidate_text_collision_exhausted")
        self.assertEqual(batch.manifest.exact_model_tie_count, 0)
        self.assertEqual(batch.exact_model_ties, [])
        content = record.visible_history[0].content
        self.assertEqual(renderer.calls[(content, "baseline")], 3)
        self.assertEqual(renderer.calls[(content, "humanistic")], 3)
        self.assertEqual(renderer.calls[(content, "fallback")], 1)
        collisions = [
            item
            for item in batch.failures
            if item.error_code == "candidate_text_collision"
        ]
        self.assertEqual(len(collisions), 3)
        self.assertTrue(all(item.arm is None for item in collisions))
        self.assertTrue(all(item.attempt_id is None for item in collisions))
        self.assertTrue(all(item.collision_id is not None for item in collisions))
        self.assertTrue(
            all(
                item.collision_scope == "model_pair_normalized_only"
                for item in collisions
            )
        )

    def test_v5_rj_07_three_way_collision_remains_blocked(self) -> None:
        batch = _generate(
            [_unit_context(1)],
            FallbackCollisionRenderer(three_way=True),
        )

        self.assertEqual(batch.manifest.status, "blocked")
        collisions = [
            item for item in batch.failures
            if item.error_code == "candidate_text_collision"
        ]
        self.assertEqual(len(collisions), 3)
        self.assertTrue(
            all(item.collision_scope == "three_way" for item in collisions)
        )

    def test_v5_rj_08_undeclared_exact_duplicate_is_rejected(self) -> None:
        batch = _generate([_unit_context(1)], FakeRenderer())
        batch.blind_cases[0].candidates[1].candidate_text = (
            batch.blind_cases[0].candidates[0].candidate_text
        )

        with self.assertRaisesRegex(ValueError, "sealed tie record"):
            validate_candidate_batch(batch, expected_context_count=1)

    def test_v5_rj_09_exact_tie_preserves_opaque_blind_packet(self) -> None:
        batch = _generate([_unit_context(1)], ExactTieRenderer())
        blind_payload = batch.blind_cases[0].model_dump(mode="json")
        serialized = json.dumps(blind_payload, ensure_ascii=False)

        for forbidden in (
            '"arm"',
            '"model"',
            '"provider"',
            '"prompt_version"',
            '"style_version"',
        ):
            self.assertNotIn(forbidden, serialized)
        candidate_ids = [
            item["candidate_id"] for item in blind_payload["candidates"]
        ]
        self.assertEqual(len(set(candidate_ids)), 3)
        self.assertTrue(all(item.startswith("cand_") for item in candidate_ids))

    def test_cg_rj09_missing_or_swapped_arm_key_is_rejected(self) -> None:
        batch = _generate([_unit_context(1), _unit_context(2)], FakeRenderer())
        left = batch.arm_key[0].assignments[0]
        right = batch.arm_key[1].assignments[0]
        left.candidate_id, right.candidate_id = right.candidate_id, left.candidate_id
        with self.assertRaisesRegex(ValueError, "swapped across blind cases"):
            validate_candidate_batch(batch, expected_context_count=2)

        missing = _generate([_unit_context(1)], FakeRenderer())
        missing.arm_key[0].assignments.pop()
        with self.assertRaisesRegex(ValueError, "candidate IDs differ"):
            validate_candidate_batch(missing, expected_context_count=1)

    def test_cg_rj10_selected_provenance_hash_drift_is_rejected(self) -> None:
        batch = _generate([_unit_context(1)], FakeRenderer())
        selected = next(item for item in batch.provenance if item.selected)
        selected.candidate_text_sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "content hash is inconsistent"):
            validate_candidate_batch(batch, expected_context_count=1)

        contract_drift = _generate([_unit_context(1)], FakeRenderer())
        contract_drift.provenance[0].output_contract_sha256 = "0" * 64
        with self.assertRaisesRegex(
            ValueError,
            "provenance output contract lock",
        ):
            validate_candidate_batch(contract_drift, expected_context_count=1)

    def test_cg_rj11_loader_does_not_resolve_or_read_review_examples(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir) / "repo"
            manifest_path = repo_root / "manifest.json"
            development_path = repo_root / "development.jsonl"
            locked_path = repo_root / "locked.jsonl"
            forbidden_review_path = "forbidden/review_examples.jsonl"
            repo_root.mkdir()
            manifest_path.write_text("{}", encoding="utf-8")
            development_path.write_text(
                _unit_context(1).model_dump_json() + "\n", encoding="utf-8"
            )
            locked_path.write_text(
                _unit_context(2, split="locked_test").model_dump_json() + "\n",
                encoding="utf-8",
            )
            asset = lambda path: SimpleNamespace(  # noqa: E731
                repo_relative_path=path.name,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                count=1,
            )
            fake_manifest = SimpleNamespace(
                status="frozen_v1",
                freeze_record=object(),
                generation_reliability_amendment_v3=SimpleNamespace(
                    status="frozen_after_smoke",
                    action_matrix_status="pass",
                    generation_restart_authorized=True,
                ),
                candidate_generator_status="pending_before_generation",
                development_contexts=asset(development_path),
                locked_test_contexts=asset(locked_path),
                review_examples=SimpleNamespace(
                    repo_relative_path=forbidden_review_path
                ),
                freeze_artifacts=[],
                new_locked_context_ids=["HIV1-U02"],
            )
            original_resolver = candidate_generation._resolve_generation_asset
            resolver = MagicMock(wraps=original_resolver)
            with (
                patch.object(
                    candidate_generation.HumanisticContextManifest,
                    "model_validate_json",
                    return_value=fake_manifest,
                ),
                patch.object(
                    candidate_generation,
                    "_resolve_generation_asset",
                    resolver,
                ),
                patch.object(candidate_generation, "validate_context_manifest"),
            ):
                _, records = load_frozen_generation_contexts(
                    manifest_path,
                    repo_root=repo_root,
                )

            resolved_assets = {call.args[1] for call in resolver.call_args_list}
            self.assertEqual(resolved_assets, {"development.jsonl", "locked.jsonl"})
            self.assertNotIn(forbidden_review_path, resolved_assets)
            self.assertEqual(len(records), 2)

    def test_cg_rj12_paired_retry_replays_both_arms_and_stops_at_three(self) -> None:
        record = _unit_context(1)
        content = record.visible_history[0].content
        renderer = FakeRenderer(
            failures_before_success={(content, "humanistic"): 2}
        )
        batch = _generate([record], renderer)
        self.assertEqual(batch.manifest.status, "complete")
        self.assertEqual(renderer.calls[(content, "baseline")], 3)
        self.assertEqual(renderer.calls[(content, "humanistic")], 3)
        self.assertEqual(renderer.calls[(content, "fallback")], 1)
        self.assertEqual(batch.manifest.failure_count, 2)

        blocked_renderer = FakeRenderer(
            failures_before_success={(content, "humanistic"): 3}
        )
        blocked = _generate([record], blocked_renderer)
        self.assertEqual(blocked.manifest.status, "blocked")
        self.assertEqual(blocked.manifest.blocked_context_ids, ["HIV1-U01"])
        self.assertEqual(blocked.manifest.attempted_context_count, 1)
        self.assertEqual(blocked.manifest.stop_reason, "paired_rounds_exhausted")
        self.assertEqual(blocked.manifest.stop_context_id, "HIV1-U01")
        self.assertEqual(blocked.blind_cases, [])
        self.assertEqual(blocked.arm_key, [])
        self.assertEqual(blocked_renderer.calls[(content, "baseline")], 3)
        self.assertEqual(blocked_renderer.calls[(content, "humanistic")], 3)
        self.assertEqual(blocked_renderer.calls[(content, "fallback")], 0)

    def test_first_valid_per_arm_may_be_selected_across_paired_rounds(
        self,
    ) -> None:
        renderer = CrossRoundRenderer()
        batch = _generate([_unit_context(1)], renderer)

        self.assertEqual(batch.manifest.status, "complete")
        self.assertEqual(renderer.calls["baseline"], 2)
        self.assertEqual(renderer.calls["humanistic"], 2)
        self.assertEqual(renderer.calls["fallback"], 1)
        selected = {
            item.arm: item.paired_round
            for item in batch.provenance
            if item.selected
        }
        self.assertEqual(selected["baseline"], 1)
        self.assertEqual(selected["humanistic"], 2)
        self.assertEqual(selected["fallback"], 2)

    def test_stop_c_fatal_identity_error_aborts_arm_pair_and_remaining_contexts(
        self,
    ) -> None:
        renderer = FatalIdentityRenderer()
        batch = _generate([_unit_context(1), _unit_context(2)], renderer)

        self.assertEqual(batch.manifest.status, "blocked")
        self.assertEqual(batch.manifest.attempted_context_count, 1)
        self.assertEqual(
            batch.manifest.stop_reason,
            "fatal_model_identity_mismatch",
        )
        self.assertEqual(len(renderer.requests), 1)
        self.assertEqual(len(batch.case_key), 1)
        self.assertEqual(len(batch.provenance), 1)
        self.assertEqual(len(batch.failures), 1)
        self.assertTrue(batch.failures[0].fatal)
        self.assertEqual(batch.blind_cases, [])
        self.assertEqual(batch.arm_key, [])

    def test_stop_d_exhausted_context_prevents_calls_for_remaining_contexts(
        self,
    ) -> None:
        records = [_unit_context(1), _unit_context(2)]
        failure_schedule = {
            (record.visible_history[0].content, "humanistic"): 3
            for record in records
        }
        renderer = FakeRenderer(failures_before_success=failure_schedule)
        batch = _generate(records, renderer)

        self.assertEqual(batch.manifest.status, "blocked")
        self.assertEqual(batch.manifest.attempted_context_count, 1)
        self.assertEqual(batch.manifest.stop_reason, "paired_rounds_exhausted")
        self.assertEqual(len(batch.case_key), 1)
        self.assertEqual(sum(renderer.calls.values()), 6)
        attempted_content = batch.case_key[0].context_id
        self.assertEqual(batch.manifest.stop_context_id, attempted_content)

    def test_model_arm_first_position_is_balanced(self) -> None:
        records = [_unit_context(1), _unit_context(2, split="locked_test")]
        renderer = FakeRenderer()
        _generate(records, renderer)
        first_model_arm_by_content = {}
        for request in renderer.requests:
            if request.arm == "fallback":
                continue
            content = request.renderer_input["specified_user_turn"]["content"]
            first_model_arm_by_content.setdefault(content, request.arm)
        self.assertEqual(
            set(first_model_arm_by_content.values()),
            {"baseline", "humanistic"},
        )

    def test_complete_and_blocked_writers_keep_reviewer_and_sealed_outputs_apart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_root = root / "repo"
            repo_root.mkdir()
            complete = _generate([_unit_context(1)], FakeRenderer())
            complete_path = root / "private" / "complete"
            original_umask = os.umask(0)
            try:
                write_complete_batch(
                    complete,
                    complete_path,
                    repo_root=repo_root,
                )
            finally:
                os.umask(original_umask)
            self.assertTrue(
                (complete_path / "reviewer/blind_review_packet_v1.jsonl").is_file()
            )
            self.assertTrue((complete_path / "sealed/arm_key_v1.jsonl").is_file())
            for directory in (
                complete_path,
                complete_path / "reviewer",
                complete_path / "sealed",
            ):
                self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            for path in complete_path.rglob("*"):
                if path.is_file():
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            reviewer_payload = json.loads(
                (complete_path / "reviewer/blind_review_packet_v1.jsonl")
                .read_text(encoding="utf-8")
                .strip()
            )
            self.assertNotIn("context_id", json.dumps(reviewer_payload))
            with self.assertRaises(FileExistsError):
                write_complete_batch(
                    complete,
                    complete_path,
                    repo_root=repo_root,
                )

            inside_repo = repo_root / "candidate-output"
            with self.assertRaisesRegex(ValueError, "outside the Git repository"):
                validate_private_output_path(
                    inside_repo,
                    repo_root=repo_root,
                )
            with self.assertRaisesRegex(ValueError, "outside the Git repository"):
                write_complete_batch(
                    complete,
                    inside_repo,
                    repo_root=repo_root,
                )

            record = _unit_context(1)
            blocked = _generate(
                [record],
                FakeRenderer(
                    failures_before_success={
                        (record.visible_history[0].content, "humanistic"): 3
                    }
                ),
            )
            blocked_path = root / "blocked"
            write_blocked_audit(
                blocked,
                blocked_path,
                repo_root=repo_root,
            )
            self.assertFalse((blocked_path / "reviewer").exists())
            self.assertFalse((blocked_path / "sealed/arm_key_v1.jsonl").exists())
            self.assertTrue(
                (blocked_path / "sealed/generation_failures_v1.jsonl").is_file()
            )
            blocked_manifest = json.loads(
                (blocked_path / "candidate_generation_manifest_v1.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(blocked_manifest["attempted_context_count"], 1)
            self.assertEqual(
                blocked_manifest["stop_reason"],
                "paired_rounds_exhausted",
            )
            self.assertEqual(blocked_manifest["stop_context_id"], "HIV1-U01")
            self.assertEqual(stat.S_IMODE(blocked_path.stat().st_mode), 0o700)
            self.assertEqual(
                stat.S_IMODE((blocked_path / "sealed").stat().st_mode),
                0o700,
            )
            for path in blocked_path.rglob("*"):
                if path.is_file():
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_execute_gate_rejects_missing_preflight_hash_and_repo_output(self) -> None:
        locked_hash = "a" * 64
        locked_source_hashes = GenerationSourceHashes(
            context_manifest_sha256=locked_hash,
            generator_sha256=locked_hash,
            generator_cli_sha256=locked_hash,
            prompt_registry_sha256=locked_hash,
            interviewer_agent_sha256=locked_hash,
            output_contract_module_sha256=locked_hash,
            validator_sha256=locked_hash,
            context_adapter_sha256=locked_hash,
            model_gateway_sha256=locked_hash,
            config_sha256=locked_hash,
        )
        prompts = _prompt_sources()
        protocol = _protocol()
        preflight_sha, _ = candidate_cli._preflight_fingerprint(  # noqa: SLF001
            locked_source_hashes,
            protocol,
            prompts,
        )
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        common_args = [
            "--execute-real",
            "--expected-context-manifest-sha",
            locked_hash,
            "--confirmation",
            candidate_cli.EXECUTION_CONFIRMATION,
        ]
        outside_path = Path(tempfile.gettempdir()) / "unit-output-never-created"
        inside_path = candidate_cli.REPO_ROOT / "unit-output-never-created"
        with (
            patch.object(
                candidate_cli,
                "load_frozen_generation_contexts",
                return_value=(object(), [_unit_context(1)]),
            ),
            patch.object(
                candidate_cli,
                "load_frozen_prompt_sources",
                return_value=prompts,
            ),
            patch.object(candidate_cli, "get_settings", return_value=settings),
            patch.object(candidate_cli, "sha256_file", return_value=locked_hash),
            patch.object(candidate_cli, "generate_candidate_batch") as generate,
            patch.object(candidate_cli, "write_complete_batch") as writer,
        ):
            with redirect_stdout(io.StringIO()) as output:
                result = candidate_cli.main(
                    [*common_args, "--output-dir", str(outside_path)]
                )
            self.assertEqual(result, 2)
            self.assertIn("preflight SHA-256", output.getvalue())
            generate.assert_not_called()

            with redirect_stdout(io.StringIO()) as output:
                result = candidate_cli.main(
                    [
                        *common_args,
                        "--expected-preflight-sha",
                        preflight_sha,
                        "--output-dir",
                        str(inside_path),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertIn("outside the Git repository", output.getvalue())
            generate.assert_not_called()

            complete = _generate([_unit_context(1)], FakeRenderer())
            generate.return_value = complete
            with tempfile.TemporaryDirectory() as private_root:
                valid_output = Path(private_root) / "valid-output"
                with redirect_stdout(io.StringIO()):
                    result = candidate_cli.main(
                        [
                            *common_args,
                            "--expected-preflight-sha",
                            preflight_sha,
                            "--output-dir",
                            str(valid_output),
                        ]
                    )
            self.assertEqual(result, 0)
            generate.assert_called_once()
            writer.assert_called_once()

        drifted_hashes = locked_source_hashes.model_copy(
            update={"generator_cli_sha256": "b" * 64}
        )
        drifted_preflight_sha, _ = candidate_cli._preflight_fingerprint(  # noqa: SLF001
            drifted_hashes,
            protocol,
            prompts,
        )
        self.assertNotEqual(preflight_sha, drifted_preflight_sha)

    def test_gateway_uses_and_requires_raw_api_model_identity(self) -> None:
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeAsyncClient:
            def __init__(self, payload):
                self.response = FakeResponse(payload)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def post(self, endpoint, *, headers, json):
                return self.response

        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="deepseek",
            DEEPSEEK_MODEL="configured-model",
        )
        request = ModelChatRequest(
            messages=[ChatMessage(role="user", content="unit-only")],
            json_mode=True,
        )
        valid_payload = {
            "model": "actual-api-model",
            "choices": [{"message": {"content": "{}"}}],
        }
        with patch(
            "app.services.model_gateway_service.httpx.AsyncClient",
            return_value=FakeAsyncClient(valid_payload),
        ):
            response = asyncio.run(ModelGatewayService(settings).chat(request))
        self.assertEqual(response.model, "actual-api-model")

        missing_model = {
            "choices": [{"message": {"content": "{}"}}],
        }
        with patch(
            "app.services.model_gateway_service.httpx.AsyncClient",
            return_value=FakeAsyncClient(missing_model),
        ):
            with self.assertRaisesRegex(HTTPException, "model identity"):
                asyncio.run(ModelGatewayService(settings).chat(request))

    def test_synthetic_smoke_is_two_arm_private_and_non_formal(self) -> None:
        context = smoke_cli.synthetic_smoke_context()
        self.assertEqual(context.context_id, "HIV1-S99")
        self.assertEqual(context.split, "dev")
        self.assertEqual(context.status, "provisional_synthetic")
        source = Path(smoke_cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("pilot_contexts_locked_v1.jsonl", source)
        self.assertNotIn("review_examples_v1.jsonl", source)

        protocol = _protocol()
        prompts = _prompt_sources()
        renderer = FakeRenderer()
        audit = smoke_cli.run_synthetic_two_arm_smoke(
            renderer=renderer,
            protocol=protocol,
            prompt_sources=prompts,
            smoke_preflight_sha256="a" * 64,
        )
        self.assertEqual(audit["status"], "pass")
        self.assertFalse(audit["formal_candidate_generation"])
        self.assertEqual(len(renderer.requests), 2)
        self.assertEqual(
            [item["arm"] for item in audit["arm_audits"]],
            ["baseline", "humanistic"],
        )
        self.assertTrue(
            all(
                item["output_contract_version"]
                == INTERVIEWER_OUTPUT_CONTRACT_VERSION
                and item["output_contract_sha256"]
                == INTERVIEWER_OUTPUT_CONTRACT_SHA256
                and item["contract_errors"] == []
                for item in audit["arm_audits"]
            )
        )

        rejecting = RejectingSmokeRenderer()
        rejected = smoke_cli.run_synthetic_two_arm_smoke(
            renderer=rejecting,
            protocol=protocol,
            prompt_sources=prompts,
            smoke_preflight_sha256="b" * 64,
        )
        self.assertEqual(rejected["status"], "blocked")
        self.assertEqual(len(rejecting.requests), 2)
        self.assertTrue(
            all(
                item["raw_output"]
                == '{"message":"unit-only rejected raw output"}'
                for item in rejected["arm_audits"]
            )
        )
        self.assertTrue(
            all(
                item["quality_flag_mismatches"]
                == [
                    "faithful_reflection:claimed_true_but_failed:missing_reflection"
                ]
                for item in rejected["arm_audits"]
            )
        )

        contract_rejected = smoke_cli.run_synthetic_two_arm_smoke(
            renderer=ContractRejectingRenderer(),
            protocol=protocol,
            prompt_sources=prompts,
            smoke_preflight_sha256="e" * 64,
        )
        self.assertEqual(contract_rejected["status"], "blocked")
        self.assertEqual(
            contract_rejected["arm_audits"][0]["error_code"],
            "output_contract_invalid",
        )
        self.assertEqual(
            contract_rejected["arm_audits"][0]["contract_errors"],
            [{"path": ["message_type"], "code": "missing"}],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "private-smoke"
            output_path = smoke_cli.write_private_smoke_audit(
                rejected,
                output_dir,
            )
            self.assertEqual(stat.S_IMODE(output_dir.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(output_path.stat().st_mode), 0o600)
            stored = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["status"], "blocked")
            self.assertEqual(len(stored["arm_audits"]), 2)

        fatal = FatalIdentityRenderer()
        fatal_audit = smoke_cli.run_synthetic_two_arm_smoke(
            renderer=fatal,
            protocol=protocol,
            prompt_sources=prompts,
            smoke_preflight_sha256="c" * 64,
        )
        self.assertEqual(fatal_audit["status"], "blocked")
        self.assertEqual(len(fatal.requests), 1)
        self.assertTrue(fatal_audit["arm_audits"][0]["fatal"])

    def test_event_reliability_smoke_is_synthetic_and_isolated(self) -> None:
        context = event_smoke_cli.synthetic_event_smoke_context()
        self.assertEqual(context.context_id, "HIV1-S98")
        self.assertEqual(context.category, "event")
        self.assertEqual(context.frozen_plan.action, "RELEASE_EVENT")
        self.assertEqual(
            context.event_unit.unit_code,
            context.frozen_plan.release_unit_code,
        )
        source = Path(event_smoke_cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("pilot_contexts_locked_v1.jsonl", source)
        self.assertNotIn("review_examples_v1.jsonl", source)

        renderer = FakeRenderer()
        audit = smoke_cli.run_synthetic_two_arm_smoke(
            renderer=renderer,
            protocol=_protocol(),
            prompt_sources=_prompt_sources(),
            smoke_preflight_sha256="f" * 64,
            context=context,
        )
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(len(renderer.requests), 2)
        self.assertTrue(
            all(
                request.plan.action == "RELEASE_EVENT"
                for request in renderer.requests
            )
        )

    def test_action_matrix_covers_six_actions_without_frozen_assets(self) -> None:
        contexts = matrix_smoke_cli.synthetic_action_smoke_contexts()
        self.assertEqual(len(contexts), 6)
        self.assertEqual(
            {context.frozen_plan.action for context in contexts},
            {
                "PROBE",
                "CHALLENGE",
                "RELEASE_EVENT",
                "CLARIFY",
                "INTEGRATE",
                "CONCLUDE",
            },
        )
        self.assertTrue(
            all(
                context.status == "provisional_synthetic"
                and context.split == "dev"
                for context in contexts
            )
        )
        source = Path(matrix_smoke_cli.__file__).read_text(encoding="utf-8")
        self.assertNotIn("pilot_contexts_locked_v1.jsonl", source)
        self.assertNotIn("review_examples_v1.jsonl", source)

        renderer = FakeRenderer()
        audit = matrix_smoke_cli.run_action_matrix_smoke(
            renderer=renderer,
            protocol=_protocol(),
            prompt_sources=_prompt_sources(),
            preflight_sha256="1" * 64,
        )
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(len(audit["case_audits"]), 6)
        self.assertEqual(len(renderer.requests), 12)

    def test_synthetic_smoke_default_is_read_only(self) -> None:
        with redirect_stdout(io.StringIO()) as output:
            result = smoke_cli.main([])
        payload = json.loads(output.getvalue())
        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertFalse(payload["will_call_model"])
        self.assertEqual(payload["remote_call_count"], 2)
        self.assertEqual(
            payload["preflight_lock"]["output_contract"],
            {
                "version": INTERVIEWER_OUTPUT_CONTRACT_VERSION,
                "sha256": INTERVIEWER_OUTPUT_CONTRACT_SHA256,
            },
        )

    def test_synthetic_smoke_missing_confirmation_stops_before_calls(self) -> None:
        settings = Settings(
            _env_file=None,
            MODEL_GATEWAY_MODE="real",
            DEEPSEEK_API_KEY="unit-only-key",
            MODEL_PROVIDER="unit-provider",
            DEEPSEEK_MODEL="unit-model",
        )
        preflight_sha = "d" * 64
        with tempfile.TemporaryDirectory() as private_root:
            output_dir = Path(private_root) / "never-created"
            with (
                patch.object(smoke_cli, "get_settings", return_value=settings),
                patch.object(
                    smoke_cli,
                    "validate_real_generation_environment",
                ),
                patch.object(
                    smoke_cli,
                    "load_frozen_prompt_sources",
                    return_value=_prompt_sources(),
                ),
                patch.object(
                    smoke_cli,
                    "smoke_preflight_lock",
                    return_value=(preflight_sha, {"unit": True}),
                ),
                patch.object(
                    smoke_cli,
                    "StrictFrozenInterviewerRenderer",
                ) as renderer,
            ):
                with redirect_stdout(io.StringIO()) as output:
                    result = smoke_cli.main(
                        [
                            "--execute-real-smoke",
                            "--expected-smoke-preflight-sha",
                            preflight_sha,
                            "--output-dir",
                            str(output_dir),
                        ]
                    )
            payload = json.loads(output.getvalue())
            self.assertEqual(result, 2)
            self.assertEqual(payload["status"], "blocked")
            self.assertFalse(payload["will_call_model"])
            self.assertIn(
                "synthetic two-arm smoke confirmation missing",
                payload["blockers"],
            )
            renderer.assert_not_called()
            self.assertFalse(output_dir.exists())

    def test_cli_help_is_executable_without_loading_data_or_calling_model(self) -> None:
        backend_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/generate_humanistic_blind_candidates_v1.py",
                "--help",
            ],
            cwd=backend_root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--execute-real", completed.stdout)
        self.assertIn("--expected-preflight-sha", completed.stdout)
        self.assertIn("--confirmation", completed.stdout)


if __name__ == "__main__":
    unittest.main()
