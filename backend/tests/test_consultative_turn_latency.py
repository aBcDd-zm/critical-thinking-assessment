from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.agents.consultative_turn_agent import (
    CONSULTATIVE_TURN_PROMPT_VARIANT,
    ConsultativeTurnAgent,
)
from app.agents.interview_blueprint import build_blueprint_from_generated
from app.agents.runtime_interviewer_agent import (
    BASELINE_INTERVIEWER_STYLE,
    INTERVIEWER_RENDER_FAST_RETRY_LIMIT,
    INTERVIEWER_RENDER_MAX_TOKENS,
    INTERVIEWER_RENDER_PROMPT_VARIANT,
    RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION,
    RUNTIME_INTERVIEWER_PROMPT_VERSION,
    InterviewerAgent,
)
from app.agents.measurement_contract import load_measurement_contract
from app.agents.progressive_schemas import DimensionSlotState, InterviewState
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    ParticipantContext,
    ScenarioContext,
    SessionContext,
    StageContext,
)
from app.schemas.model_gateway import ModelChatResponse
from app.services.occupation_skeleton_service import OccupationSkeletonService


def _blueprint():
    generated = OccupationSkeletonService._build_generated(  # noqa: SLF001
        "测试协作任务",
        "参与者",
        concrete_v33=True,
    )
    return build_blueprint_from_generated(
        generated,
        occupation_category="学生",
        occupation="大学生",
        user_role="参与者",
        task_domain="测试协作任务",
        skeleton_v3_3=True,
        **OccupationSkeletonService._arrangements("测试协作任务"),  # noqa: SLF001
    )


def _context() -> AgentRuntimeContext:
    prior = DialogueTurnContext(
        turn_id=90,
        turn_index=1,
        stage_id=11,
        stage_code="s1_problem_definition",
        speaker="ai",
        content="你会先确认什么？",
        content_type="interview_followup",
        analysis_json={"large_internal_state": "x" * 10_000},
    )
    latest = DialogueTurnContext(
        turn_id=91,
        turn_index=2,
        stage_id=11,
        stage_code="s1_problem_definition",
        speaker="user",
        content="我会先核实当前完成度和质量记录，再决定是否减少检查。",
        content_type="scenario_answer",
        analysis_json={"large_internal_state": "y" * 10_000},
    )
    return AgentRuntimeContext(
        session=SessionContext(
            session_id=7,
            session_uuid=str(uuid4()),
            assessment_mode="real",
            status="in_progress",
        ),
        participant=ParticipantContext(
            participant_id=3,
            nickname="延迟测试",
        ),
        scenario=ScenarioContext(
            scenario_id=10,
            scenario_code="latency-runtime-test",
            title="测试协作任务",
            background="五天后需要完成一项协作任务。",
        ),
        stage=StageContext(
            stage_id=11,
            stage_code="s1_problem_definition",
            stage_order=1,
            title="界定问题",
            stage_goal="观察问题界定",
            context="当前完成度和质量还没核实。",
            main_question="你会先确认什么？",
            max_followups=2,
        ),
        dialogue_history=[prior, latest],
        latest_user_turn=latest,
    )


def _state() -> InterviewState:
    slots = {
        dimension.dimension_key: DimensionSlotState(
            dimension_key=dimension.dimension_key,
            status=dimension.initial_status,
            missing_behavior_keys=[
                behavior.behavior_key for behavior in dimension.behaviors
            ],
        )
        for dimension in load_measurement_contract().dimensions
    }
    state = InterviewState(
        schema_version="interview_state_v3_3",
        current_node_code="s1_problem_definition",
        dimension_slots=slots,
        dimension_opportunity_counts={key: 0 for key in slots},
        dimension_opportunity_quality={key: 0 for key in slots},
        weak_evidence_turn_ids={key: [] for key in slots},
        turn_latency_budget_ms=15_000,
    )
    state.last_plan = {"large_internal_state": "z" * 10_000}
    state.evidence_timeline = [{"large_internal_state": "w" * 10_000}]
    return state


class ConsultativeTurnLatencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.core = ConsultativeTurnAgent()
        self.renderer = InterviewerAgent()
        self.context = _context()
        self.state = _state()
        self.blueprint = _blueprint()
        self.plan = self.core.fallback(
            self.context,
            self.state,
            self.blueprint,
        ).plan
        assert self.plan is not None
        self.real_settings = SimpleNamespace(
            MODEL_GATEWAY_MODE="real",
            INTERVIEWER_RENDER_TIMEOUT_SECONDS=8,
        )

    def _deterministic_message(self) -> str:
        return self.renderer._fallback(  # noqa: SLF001
            self.plan,
            self.blueprint,
            self.context,
            style_version=BASELINE_INTERVIEWER_STYLE,
        ).message

    def test_measurement_core_is_deterministic_and_never_calls_model(self) -> None:
        chat = AsyncMock()
        with patch(
            "app.services.model_gateway_service.ModelGatewayService.chat",
            chat,
        ):
            result = self.core.generate(
                self.context,
                self.state,
                self.blueprint,
            )

        chat.assert_not_awaited()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 0)
        self.assertEqual(
            result.model_name,
            "deterministic-measurement-core-v1",
        )
        self.assertEqual(
            CONSULTATIVE_TURN_PROMPT_VARIANT,
            "deterministic_measurement_core_v1",
        )
        self.assertIsNotNone(result.output.plan)
        self.assertTrue(result.output.plan.evidence_observations)

    def test_opening_is_deterministic(self) -> None:
        result = self.core.generate(
            self.context,
            self.state,
            self.blueprint,
            opening=True,
            nickname="延迟测试",
        )

        self.assertEqual(result.model_attempt_count, 0)
        self.assertEqual(result.model_name, "deterministic-opening-plan-v1")
        self.assertIsNone(result.output.plan)

    def test_compact_renderer_request_is_bounded_and_message_only(self) -> None:
        response = ModelChatResponse(
            provider="deepseek",
            model="deepseek-v4-pro",
            content=json.dumps(
                {"message": self._deterministic_message()},
                ensure_ascii=False,
            ),
        )
        chat = AsyncMock(return_value=response)
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=self.real_settings,
            ),
            patch(
                "app.agents.runtime_interviewer_agent.ModelGatewayService.chat",
                chat,
            ),
        ):
            result = self.renderer.render(
                self.context,
                self.blueprint,
                self.plan,
                previous_questions=[],
                template_content="只把确定性草稿润色为自然中文。",
                timeout_seconds=15,
                primary_timeout_seconds=8,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 1)
        request = chat.await_args.args[0]
        prompt_size = sum(len(item.content) for item in request.messages)
        self.assertLess(prompt_size, 2_000)
        self.assertEqual(request.max_tokens, INTERVIEWER_RENDER_MAX_TOKENS)
        self.assertEqual(request.max_tokens, 220)
        self.assertLessEqual(request.timeout_seconds, 8)
        payload = json.loads(request.messages[-1].content)
        self.assertEqual(
            set(payload) - {"instruction", "repair"},
            {
                "style_version",
                "validated_plan",
                "draft",
                "required_fact",
                "source_quote",
                "recent_questions",
                "event_intro_selector_version",
                "previous_event_intro_frame",
                "selected_event_intro_frame",
            },
        )
        self.assertNotIn("large_internal_state", request.messages[-1].content)
        self.assertEqual(
            INTERVIEWER_RENDER_PROMPT_VARIANT,
            "compact_message_v2",
        )
        self.assertEqual(
            RUNTIME_INTERVIEWER_PROMPT_VERSION,
            "progressive_interviewer_compact_v2",
        )
        self.assertEqual(
            RUNTIME_HUMANISTIC_INTERVIEWER_PROMPT_VERSION,
            "humanistic_interviewer_compact_v2",
        )

    def test_disconnect_retries_once_and_recovers(self) -> None:
        chat = AsyncMock(
            side_effect=[
                HTTPException(
                    status_code=502,
                    detail=(
                        "DeepSeek API request failed (RemoteProtocolError): "
                        "incomplete chunked read"
                    ),
                ),
                ModelChatResponse(
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    content=json.dumps(
                        {"message": self._deterministic_message()},
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=self.real_settings,
            ),
            patch(
                "app.agents.runtime_interviewer_agent.ModelGatewayService.chat",
                chat,
            ),
        ):
            result = self.renderer.render(
                self.context,
                self.blueprint,
                self.plan,
                previous_questions=[],
                timeout_seconds=15,
                primary_timeout_seconds=8,
            )

        self.assertEqual(INTERVIEWER_RENDER_FAST_RETRY_LIMIT, 1)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 2)
        self.assertEqual(result.retry_reason, "RemoteProtocolError")
        self.assertEqual(len(result.transport_errors), 1)
        self.assertEqual(chat.await_count, 2)

    def test_two_transport_failures_fall_back_without_third_attempt(self) -> None:
        disconnect = HTTPException(
            status_code=502,
            detail="DeepSeek API request failed (ReadTimeout): ",
        )
        chat = AsyncMock(side_effect=[disconnect, disconnect])
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=self.real_settings,
            ),
            patch(
                "app.agents.runtime_interviewer_agent.ModelGatewayService.chat",
                chat,
            ),
        ):
            result = self.renderer.render(
                self.context,
                self.blueprint,
                self.plan,
                previous_questions=[],
                timeout_seconds=15,
                primary_timeout_seconds=8,
            )

        self.assertEqual(result.status, "failed")
        self.assertTrue(result.output.fallback_used)
        self.assertEqual(result.model_attempt_count, 2)
        self.assertEqual(result.retry_reason, "ReadTimeout")
        self.assertEqual(chat.await_count, 2)

    def test_primary_hard_timeout_retries_with_only_remaining_budget(self) -> None:
        attempt_timeouts: list[float | None] = []

        def fail_call(*_args, timeout_seconds=None, **_kwargs):
            attempt_timeouts.append(timeout_seconds)
            raise TimeoutError()

        clock = iter(
            (0.0, 0.0, 0.0, 8.0, 8.0, 8.0, 8.0, 15.0, 15.0)
        )
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=self.real_settings,
            ),
            patch.object(self.renderer, "_call", side_effect=fail_call),
            patch(
                "app.agents.runtime_interviewer_agent.perf_counter",
                side_effect=lambda: next(clock),
            ),
        ):
            result = self.renderer.render(
                self.context,
                self.blueprint,
                self.plan,
                previous_questions=[],
                timeout_seconds=15,
                primary_timeout_seconds=8,
            )

        self.assertEqual(attempt_timeouts, [8, 7])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.model_attempt_count, 2)
        self.assertEqual(result.retry_reason, "TimeoutError")
        self.assertEqual(result.transport_errors, ["TimeoutError", "TimeoutError"])
        self.assertEqual(result.duration_ms, 15_000)

    def test_invalid_json_retries_once(self) -> None:
        chat = AsyncMock(
            side_effect=[
                ModelChatResponse(
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    content='{"wrong":"shape"}',
                ),
                ModelChatResponse(
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    content=json.dumps(
                        {"message": self._deterministic_message()},
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=self.real_settings,
            ),
            patch(
                "app.agents.runtime_interviewer_agent.ModelGatewayService.chat",
                chat,
            ),
        ):
            result = self.renderer.render(
                self.context,
                self.blueprint,
                self.plan,
                previous_questions=[],
                timeout_seconds=15,
                primary_timeout_seconds=8,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.model_attempt_count, 2)
        self.assertEqual(result.retry_reason, "invalid_json")
        self.assertEqual(chat.await_count, 2)

    def test_model_cannot_override_deterministic_metadata(self) -> None:
        base = self.renderer._fallback(  # noqa: SLF001
            self.plan,
            self.blueprint,
            self.context,
        )
        parsed = self.renderer._parse(  # noqa: SLF001
            json.dumps(
                {
                    "message": base.message,
                    "message_type": "closing",
                    "introduced_fact_codes": ["forged"],
                    "reflection_turn_ids": [999],
                    "quality_flags": {},
                },
                ensure_ascii=False,
            ),
            deterministic_output=base,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.message_type, base.message_type)
        self.assertEqual(parsed.introduced_fact_codes, base.introduced_fact_codes)
        self.assertEqual(parsed.reflection_turn_ids, base.reflection_turn_ids)
        self.assertEqual(
            parsed.reflection_source_quotes,
            base.reflection_source_quotes,
        )
        self.assertEqual(parsed.quality_flags, base.quality_flags)

    def test_hard_quality_error_falls_back_without_retry(self) -> None:
        chat = AsyncMock(
            return_value=ModelChatResponse(
                provider="deepseek",
                model="deepseek-v4-pro",
                content='{"message":"你应该直接照这个方案做。是不是应该马上执行？"}',
            )
        )
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=self.real_settings,
            ),
            patch(
                "app.agents.runtime_interviewer_agent.ModelGatewayService.chat",
                chat,
            ),
        ):
            result = self.renderer.render(
                self.context,
                self.blueprint,
                self.plan,
                previous_questions=[],
                timeout_seconds=15,
                primary_timeout_seconds=8,
            )

        self.assertEqual(result.status, "failed")
        self.assertTrue(result.output.fallback_used)
        self.assertIn("leading", result.validation_errors)
        self.assertEqual(result.model_attempt_count, 1)
        self.assertEqual(chat.await_count, 1)


if __name__ == "__main__":
    unittest.main()
