from __future__ import annotations

import json
import unittest
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.consultative_turn_agent import (
    ConsultativeTurnAgent,
    ConsultativeTurnAgentResult,
)
from app.agents.interview_blueprint import build_blueprint_from_generated
from app.agents.interview_planner_agent import (
    InterviewPlannerAgent,
    PlannerAgentResult,
)
from app.agents.runtime_interviewer_agent import (
    BASELINE_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
    InterviewerAgent,
    InterviewerAgentResult,
)
from app.agents.progressive_schemas import (
    ConsultativeTurnOutput,
    InterviewPlanOutput,
    InterviewQualityFlags,
    InterviewState,
    InterviewerOutput,
    PlannerBudget,
)
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    ParticipantContext,
    ScenarioContext,
    SessionContext,
    StageContext,
)
from app.models import Base
from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.participant import Participant
from app.models.scenario import Scenario
from app.core.runtime_interview_config import RuntimeInterviewSettings
from app.schemas.session import CreateSessionRequest
from app.services.admin_session_review_service import AdminSessionReviewService
from app.services.evidence_tracker_service import EvidenceTrackerService
from app.services.interview_state_service import InterviewStateService
from app.services.occupation_skeleton_service import OccupationSkeletonService
from app.services.session_service import (
    SessionService,
    _applied_interviewer_style,
    _compact_humanistic_runtime_fallback,
    _default_interviewer_style,
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


def _interviewer_output(message: str) -> InterviewerOutput:
    return InterviewerOutput(
        message=message,
        message_type="followup",
        question_count=message.count("？") + message.count("?"),
        quality_flags=_quality_flags(),
    )


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


def _plan() -> InterviewPlanOutput:
    return InterviewPlanOutput(
        response_intent="assess_answer",
        action="PROBE",
        active_topic="信息核实",
        target_dimension="evidence_evaluation",
        target_evidence="说明一项需要核实的信息",
        delivery_mode="reflective_probe",
        question_intent="询问用户会先核实哪一类信息",
        reflection_basis_turn_ids=[],
        reason="固定计划用于 Renderer 隔离回放",
        budget=PlannerBudget(
            used_turns=1,
            remaining_turns=9,
            reserved_update_turns=2,
            reserved_closure_turns=1,
        ),
    )


def _context() -> AgentRuntimeContext:
    latest = DialogueTurnContext(
        turn_id=91,
        turn_index=2,
        stage_id=11,
        stage_code="s1_problem_definition",
        speaker="user",
        content="我会先核实当前完成度和质量记录，再决定是否减少检查。",
        content_type="interview_answer",
    )
    return AgentRuntimeContext(
        session=SessionContext(
            session_id=7,
            session_uuid=str(uuid4()),
            assessment_mode="mock",
            status="in_progress",
        ),
        participant=ParticipantContext(
            participant_id=3,
            nickname="运行测试",
        ),
        scenario=ScenarioContext(
            scenario_id=10,
            scenario_code="humanistic-runtime-test",
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
        dialogue_history=[latest],
        latest_user_turn=latest,
    )


class HumanisticInterviewerRuntimeTests(unittest.TestCase):
    def test_baseline_grounded_reflection_tracks_latest_answer(self) -> None:
        cases = {
            ("返工从5%升到18%，我会立即暂停试用并恢复逐项检查。"): "你根据返工变化调整了原来的安排",
            ("只有两人持续配合，我会收缩范围并保留核心主流程。"): "你在人员限制下重新收缩了交付范围",
            ("先在非关键部分试用，并设置停止条件和回滚方案。"): "你为小范围试用设置了检查和停止条件",
            ("我会比较历史样本与当前数据，并交叉核实信息来源。"): "你把信息来源和适用范围纳入了核实",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                context = SimpleNamespace(
                    latest_user_turn=SimpleNamespace(content=text)
                )
                self.assertEqual(
                    InterviewerAgent._grounded_reflection(  # noqa: SLF001
                        context,
                        "reflective_probe",
                    ),
                    expected,
                )

    def test_flag_defaults_to_baseline_and_existing_session_style_is_frozen(
        self,
    ) -> None:
        disabled = SimpleNamespace(
            INTERVIEWER_STYLE_ENABLED=False,
            INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE,
        )
        with patch(
            "app.services.session_service.get_settings",
            return_value=disabled,
        ):
            self.assertEqual(
                _default_interviewer_style(),
                BASELINE_INTERVIEWER_STYLE,
            )

        enabled = SimpleNamespace(
            INTERVIEWER_STYLE_ENABLED=True,
            INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE,
        )
        with patch(
            "app.services.session_service.get_settings",
            return_value=enabled,
        ):
            frozen_style = _default_interviewer_style()
            session = AssessmentSession(
                session_uuid=str(uuid4()),
                participant_id=1,
                scenario_id=1,
                selection_mode="test",
                status="created",
                assessment_mode="mock",
                flow_version="progressive_v3_3",
                interviewer_style_version=frozen_style,
            )
            enabled.INTERVIEWER_STYLE_DEFAULT = BASELINE_INTERVIEWER_STYLE
            self.assertEqual(
                session.interviewer_style_version,
                HUMANISTIC_INTERVIEWER_STYLE,
            )
            self.assertEqual(
                _applied_interviewer_style(session),
                HUMANISTIC_INTERVIEWER_STYLE,
            )
            for flow_version in (
                "legacy_v2",
                "progressive_v3",
                "progressive_v3_2",
            ):
                with self.subTest(flow_version=flow_version):
                    self.assertEqual(
                        _default_interviewer_style(flow_version),
                        BASELINE_INTERVIEWER_STYLE,
                    )
                    non_v33_session = AssessmentSession(
                        session_uuid=str(uuid4()),
                        participant_id=1,
                        scenario_id=1,
                        selection_mode="test",
                        status="created",
                        assessment_mode="mock",
                        flow_version=flow_version,
                        interviewer_style_version=HUMANISTIC_INTERVIEWER_STYLE,
                    )
                    self.assertEqual(
                        _applied_interviewer_style(non_v33_session),
                        BASELINE_INTERVIEWER_STYLE,
                    )

        self.assertNotIn(
            "interviewer_style_version",
            CreateSessionRequest.model_fields,
        )

    def test_humanistic_opening_has_independent_renderer_trace_linkage(
        self,
    ) -> None:
        blueprint = _blueprint()
        context = _context()
        state = InterviewState(
            schema_version="interview_state_v3_3",
            current_node_code="s1_problem_definition",
            opening_status="pending",
            task_domain=blueprint.task_domain,
        )
        session = AssessmentSession(
            id=7,
            session_uuid=context.session.session_uuid,
            participant_id=3,
            scenario_id=10,
            current_stage_id=11,
            selection_mode="test",
            status="opening_pending",
            assessment_mode="mock",
            flow_version="progressive_v3_3",
            interviewer_style_version=HUMANISTIC_INTERVIEWER_STYLE,
            interview_state_json=deepcopy(state.model_dump(mode="json")),
            state_version=1,
        )
        db = MagicMock()
        added: list[object] = []
        next_id = 300

        def add(item):
            nonlocal next_id
            if isinstance(item, (AgentTrace, DialogueTurn)) and item.id is None:
                item.id = next_id
                next_id += 1
            if isinstance(item, DialogueTurn) and item.created_at is None:
                item.created_at = datetime.utcnow()
            added.append(item)

        db.add.side_effect = add
        service = SessionService(db)
        service.repo = MagicMock()
        service.repo.list_turns.return_value = []
        service.repo.try_mark_opening_generating.return_value = True
        service.repo.get_participant.return_value = SimpleNamespace(
            id=3,
            nickname="评分观察员",
        )
        service.repo.get_scenario.return_value = SimpleNamespace(id=10)
        service.repo.next_turn_index.return_value = 2
        settings = SimpleNamespace(
            INTERVIEWER_STYLE_ENABLED=True,
            INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE,
            MODEL_GATEWAY_MODE="mock",
        )

        with (
            patch(
                "app.services.session_service.get_settings",
                return_value=settings,
            ),
            patch.object(
                service,
                "_get_session_or_404",
                return_value=session,
            ),
            patch.object(
                service,
                "_build_agent_context",
                return_value=context,
            ),
            patch.object(
                InterviewStateService,
                "blueprint",
                return_value=blueprint,
            ),
            patch.object(
                InterviewStateService,
                "load",
                return_value=state,
            ),
        ):
            events = [
                json.loads(item)
                for item in service.stream_start_interview(session.session_uuid)
            ]

        self.assertNotIn("error", {item["event"] for item in events})
        completed = next(item for item in events if item["event"] == "agent_completed")
        traces = {
            item.agent_name: item for item in added if isinstance(item, AgentTrace)
        }
        self.assertEqual(
            set(traces),
            {"consultative_turn", "interviewer_renderer"},
        )
        consultative_trace = traces["consultative_turn"]
        renderer_trace = traces["interviewer_renderer"]
        visible_turn = next(
            item
            for item in added
            if isinstance(item, DialogueTurn)
            and item.content_type == "interview_opening"
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["parent_trace_id"],
            consultative_trace.id,
        )
        self.assertEqual(
            renderer_trace.input_json["validated_plan"]["action"],
            "OPENING",
        )
        self.assertEqual(
            renderer_trace.input_json["validated_plan"]["release_unit_code"],
            blueprint.event_cards[0].presentation_units[0].unit_code,
        )
        self.assertIsNone(renderer_trace.prompt_template_id)
        self.assertEqual(
            renderer_trace.config_snapshot_json["model_attempt_count"],
            0,
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["model_call_status"],
            "not_called",
        )
        self.assertEqual(visible_turn.source_agent_trace_id, renderer_trace.id)
        self.assertEqual(
            completed["ai_turn"]["content"],
            visible_turn.content,
        )
        self.assertTrue(visible_turn.content.startswith("评分观察员，"))

    def test_opening_validation_excludes_only_user_nickname_prefix(
        self,
    ) -> None:
        blueprint = _blueprint()
        nickname = "评分观察员"
        output = InterviewerAgent().render_opening(
            blueprint,
            nickname,
            style_version=HUMANISTIC_INTERVIEWER_STYLE,
        )
        validator = ConsultativeTurnAgent()

        self.assertEqual(
            validator.validate_opening(
                output,
                blueprint,
                participant_nickname=nickname,
                enforce_humanistic_safety=True,
            ),
            [],
        )

        leaked_output = output.model_copy(
            update={
                "message": output.message.replace(
                    "你愿意先说说",
                    "访谈员会参考评分维度。你愿意先说说",
                    1,
                ),
            }
        )
        self.assertIn(
            "internal_terms",
            validator.validate_opening(
                leaked_output,
                blueprint,
                participant_nickname=nickname,
                enforce_humanistic_safety=True,
            ),
        )

    def test_user_nickname_is_not_treated_as_assistant_internal_language(
        self,
    ) -> None:
        blueprint = _blueprint()
        output = InterviewerAgent().render_opening(
            blueprint,
            "评分测试",
            style_version=HUMANISTIC_INTERVIEWER_STYLE,
        )
        agent = ConsultativeTurnAgent()
        self.assertIn(
            "internal_terms",
            agent.validate_opening(
                output,
                blueprint,
                enforce_humanistic_safety=True,
            ),
        )
        self.assertNotIn(
            "internal_terms",
            agent.validate_opening(
                output,
                blueprint,
                enforce_humanistic_safety=True,
                participant_nickname="评分测试",
            ),
        )

    def test_humanistic_event_fallback_compacts_without_losing_fact(
        self,
    ) -> None:
        blueprint = _blueprint()
        context = _context()
        event = blueprint.event_cards[1]
        unit = event.presentation_units[0]
        plan = _plan().model_copy(
            update={
                "action": "RELEASE_EVENT",
                "release_event_code": event.event_code,
                "release_unit_code": unit.unit_code,
                "delivery_mode": "event_link",
                "reflection_basis_turn_ids": [91],
            }
        )
        frozen_output = InterviewerAgent()._fallback(  # noqa: SLF001
            plan,
            blueprint,
            context,
            style_version=HUMANISTIC_INTERVIEWER_STYLE,
        )
        output = _compact_humanistic_runtime_fallback(
            frozen_output,
            plan=plan,
            blueprint=blueprint,
            context=context,
        )
        self.assertLessEqual(len(output.message), 90)
        self.assertIn(unit.text.rstrip("。！？!?"), output.message)
        self.assertEqual(output.question_count, 1)
        self.assertEqual(output.introduced_fact_codes, [unit.unit_code])

    def test_humanistic_renderer_uses_one_model_attempt_and_falls_back(
        self,
    ) -> None:
        blueprint = _blueprint()
        context = _context()
        plan = _plan()
        plan_before = plan.model_dump(mode="json")
        context_before = context.model_dump(mode="json")
        violating = _interviewer_output("我感到此刻和你很亲近。你愿意继续说吗？")
        call_count = 0

        def fake_call(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            return violating.model_dump_json(), "forced-unsafe-model"

        settings = SimpleNamespace(MODEL_GATEWAY_MODE="real")
        agent = InterviewerAgent()
        with (
            patch(
                "app.agents.runtime_interviewer_agent.get_settings",
                return_value=settings,
            ),
            patch.object(agent, "_call", side_effect=fake_call),
        ):
            result = agent.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                template_content="humanistic_interviewer_v1",
                style_version=HUMANISTIC_INTERVIEWER_STYLE,
                timeout_seconds=3,
            )

        self.assertEqual(call_count, 1)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, "HUMANISTIC_RENDERER_FALLBACK")
        self.assertEqual(result.model_name, "forced-unsafe-model")
        self.assertEqual(
            result.fallback_type,
            "humanistic_deterministic_renderer",
        )
        self.assertIn("relational_attachment", result.validation_errors)
        self.assertNotIn("亲近", result.output.message)
        self.assertEqual(plan.model_dump(mode="json"), plan_before)
        self.assertEqual(context.model_dump(mode="json"), context_before)

    def test_missing_exact_prompt_never_calls_model_and_audits_null_prompt(
        self,
    ) -> None:
        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE,
            prompt_present=False,
            model_mode="real",
        )
        renderer_trace = next(
            trace
            for trace in outcome["added"]
            if isinstance(trace, AgentTrace)
            and trace.agent_name == "interviewer_renderer"
        )
        self.assertEqual(outcome["render_mock"].call_count, 0)
        self.assertIsNone(renderer_trace.prompt_template_id)
        self.assertEqual(
            renderer_trace.error_code,
            "HUMANISTIC_PROMPT_TEMPLATE_MISSING",
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["fallback_reason"],
            "HUMANISTIC_PROMPT_TEMPLATE_MISSING",
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["validation_codes"],
            ["prompt_template_missing"],
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["model_attempt_count"],
            0,
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["model_call_status"],
            "not_called",
        )
        self.assertTrue(renderer_trace.output_json["fallback_used"])

    def test_timeout_and_prompt_audit_match_actual_model_attempts(self) -> None:
        with self.assertRaises(ValidationError):
            RuntimeInterviewSettings(
                RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS=9,
                _env_file=None,
            )

        real_attempt = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE,
            model_mode="real",
            configured_timeout=99,
        )
        real_trace = next(
            trace
            for trace in real_attempt["added"]
            if isinstance(trace, AgentTrace)
            and trace.agent_name == "interviewer_renderer"
        )
        self.assertEqual(real_attempt["render_mock"].call_count, 1)
        self.assertAlmostEqual(
            real_attempt["render_mock"].call_args.kwargs["timeout_seconds"],
            14.983,
            places=3,
        )
        self.assertEqual(
            real_attempt["render_mock"].call_args.kwargs["primary_timeout_seconds"],
            6.0,
        )
        self.assertTrue(
            real_attempt["render_mock"].call_args.kwargs["allow_model_call"]
        )
        self.assertEqual(real_trace.config_snapshot_json["timeout_ms"], 14983)
        self.assertEqual(
            real_trace.config_snapshot_json["primary_timeout_ms"],
            6000,
        )
        self.assertTrue(
            real_trace.config_snapshot_json["shared_planner_renderer_budget"]
        )
        self.assertEqual(
            real_trace.config_snapshot_json["model_attempt_count"],
            1,
        )
        self.assertEqual(
            real_trace.config_snapshot_json["model_call_status"],
            "success",
        )
        self.assertEqual(real_trace.prompt_template_id, 77)

        deterministic = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE,
            model_mode="mock",
        )
        deterministic_trace = next(
            trace
            for trace in deterministic["added"]
            if isinstance(trace, AgentTrace)
            and trace.agent_name == "interviewer_renderer"
        )
        self.assertEqual(
            deterministic_trace.config_snapshot_json["model_attempt_count"],
            0,
        )
        self.assertEqual(
            deterministic_trace.config_snapshot_json["model_call_status"],
            "not_called",
        )
        self.assertIsNone(deterministic_trace.prompt_template_id)
        self.assertFalse(
            deterministic["render_mock"].call_args.kwargs["allow_model_call"]
        )

    def test_renderer_input_is_bounded_to_visible_turn_fields(self) -> None:
        context = _context().model_copy(deep=True)
        latest = context.latest_user_turn.model_copy(
            update={
                "dynamic_info_id": 42,
                "selected_dynamic_info_code": "hidden_event_code",
                "analysis_json": {"hidden_target_dimension": "secret"},
            }
        )
        prior_ai = DialogueTurnContext(
            turn_id=90,
            turn_index=1,
            stage_id=11,
            stage_code="s1_problem_definition",
            speaker="ai",
            content="你会先核实哪一项？",
            content_type="interview_followup",
            dynamic_info_id=41,
            selected_dynamic_info_code="hidden_ai_event",
            analysis_json={"private": True},
        )
        context.dialogue_history = [prior_ai, latest]
        context.latest_user_turn = latest
        plan = _plan().model_copy(
            update={"reflection_basis_turn_ids": [prior_ai.turn_id, latest.turn_id]}
        )

        payload = InterviewerAgent.runtime_renderer_input_payload(
            context,
            _blueprint(),
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE,
        )

        self.assertEqual(payload["source_quote"]["turn_id"], latest.turn_id)
        self.assertIn(payload["source_quote"]["quote"], latest.content)
        self.assertEqual(
            set(payload),
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
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("analysis_json", serialized)
        self.assertNotIn("dynamic_info", serialized)
        self.assertNotIn("hidden_event_code", serialized)

    def test_hidden_planner_copy_does_not_count_as_humanistic_measurement_fallback(
        self,
    ) -> None:
        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE,
            model_mode="real",
            planner_validation_errors=["too_long"],
        )
        planner_trace = next(
            trace
            for trace in outcome["added"]
            if isinstance(trace, AgentTrace) and trace.agent_name == "consultative_turn"
        )
        self.assertEqual(planner_trace.status, "success")
        self.assertIsNone(planner_trace.error_code)
        self.assertTrue(
            planner_trace.config_snapshot_json["planner_interviewer_discarded"]
        )
        self.assertIn(
            "too_long",
            planner_trace.config_snapshot_json["validation_errors"],
        )

    def test_independent_renderer_trace_and_measurement_state_are_isolated(
        self,
    ) -> None:
        baseline = self._run_consultative_turn(BASELINE_INTERVIEWER_STYLE)
        humanistic = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE,
            force_unsafe_renderer=True,
        )

        self.assertEqual(
            baseline["session"].interview_state_json,
            humanistic["session"].interview_state_json,
        )
        self.assertEqual(
            baseline["user_turn"].analysis_json["evidence_delta"],
            humanistic["user_turn"].analysis_json["evidence_delta"],
        )
        self.assertEqual(
            baseline["user_turn"].analysis_json["formal_answer"],
            humanistic["user_turn"].analysis_json["formal_answer"],
        )

        humanistic_traces = {
            trace.agent_name: trace
            for trace in humanistic["added"]
            if isinstance(trace, AgentTrace)
        }
        self.assertEqual(
            set(humanistic_traces),
            {"consultative_turn", "interviewer_renderer"},
        )
        planner_trace = humanistic_traces["consultative_turn"]
        renderer_trace = humanistic_traces["interviewer_renderer"]
        visible_turn = humanistic["ai_turn"]
        self.assertEqual(
            renderer_trace.config_snapshot_json["parent_trace_id"],
            planner_trace.id,
        )
        self.assertEqual(visible_turn.source_agent_trace_id, renderer_trace.id)
        self.assertEqual(
            humanistic["user_turn"].analysis_json["renderer_trace_id"],
            renderer_trace.id,
        )
        self.assertEqual(
            renderer_trace.input_json["validated_plan"],
            {
                "action": _plan().action,
                "delivery_mode": _plan().delivery_mode,
                "question_intent": _plan().question_intent,
                "release_event_code": _plan().release_event_code,
                "release_unit_code": _plan().release_unit_code,
            },
        )
        self.assertEqual(
            renderer_trace.input_json,
            humanistic["render_mock"].call_args.kwargs["renderer_input"],
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["action"],
            planner_trace.config_snapshot_json["action"],
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["target_dimension"],
            planner_trace.config_snapshot_json["hidden_target_dimension"],
        )
        self.assertEqual(
            renderer_trace.error_code,
            "HUMANISTIC_HARD_GATE_FALLBACK",
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["validation_codes"],
            ["relational_attachment"],
        )
        self.assertNotIn("亲近", visible_turn.content)

    def test_event_frame_selection_is_persisted_in_renderer_trace(self) -> None:
        blueprint = _blueprint()
        event = next(
            item
            for item in blueprint.event_cards
            if item.event_code == "stakeholder_conflict"
        )
        unit = event.presentation_units[0]
        event_plan = _plan().model_copy(
            update={
                "action": "RELEASE_EVENT",
                "delivery_mode": "event_link",
                "release_event_code": event.event_code,
                "release_unit_code": unit.unit_code,
            }
        )
        result = self._run_consultative_turn(
            BASELINE_INTERVIEWER_STYLE,
            plan_override=event_plan,
        )
        renderer_trace = next(
            trace
            for trace in result["added"]
            if isinstance(trace, AgentTrace)
            and trace.agent_name == "interviewer_renderer"
        )

        self.assertEqual(
            renderer_trace.input_json["event_intro_selector_version"],
            "adjacent_visible_frame_v1",
        )
        self.assertIsNone(
            renderer_trace.input_json["previous_event_intro_frame"]
        )
        self.assertEqual(
            renderer_trace.input_json["selected_event_intro_frame"],
            "speaker_supplement",
        )
        for key in (
            "event_intro_selector_version",
            "previous_event_intro_frame",
            "selected_event_intro_frame",
        ):
            self.assertEqual(
                renderer_trace.config_snapshot_json[key],
                renderer_trace.input_json[key],
        )
        self.assertIn("我补充一条新", result["ai_turn"].content)

    def test_compact_event_frame_keeps_grounded_reflection_preface(self) -> None:
        fact = (
            "一部分参与者想减少交接和检查以赶进度，"
            "另一部分担心这样会增加返工和质量风险"
        )
        preface = "你已经给出了一个初步判断；"

        message = InterviewerAgent._event_intro_message(  # noqa: SLF001
            frame="speaker_supplement",
            reason="为了继续判断",
            fact=fact,
            question="面对赶进度和避免返工这两个诉求，你会先怎么协调？",
            preface=preface,
        )

        self.assertTrue(message.startswith(preface))
        self.assertIn(fact, message)
        self.assertIn("我补充一条新的信息", message)
        self.assertEqual(message.count("？"), 1)
        self.assertLessEqual(len(message), 90)
        self.assertEqual(
            InterviewerAgent.runtime_expression_errors(
                message,
                {
                    "validated_plan": {"action": "RELEASE_EVENT"},
                    "selected_event_intro_frame": "speaker_supplement",
                },
            ),
            [],
        )

    def test_progressive_v3_uses_exact_baseline_prompt_version(self) -> None:
        blueprint = _blueprint()
        context = _context()
        plan = _plan()
        state = InterviewState(
            schema_version="interview_state_v3",
            current_node_code="s1_problem_definition",
            released_event_codes=["opening_context"],
            released_unit_codes=[
                blueprint.event_cards[0].presentation_units[0].unit_code
            ],
        )
        session = AssessmentSession(
            id=7,
            session_uuid=context.session.session_uuid,
            participant_id=3,
            scenario_id=10,
            current_stage_id=11,
            selection_mode="test",
            status="in_progress",
            assessment_mode="mock",
            flow_version="progressive_v3",
            interviewer_style_version=BASELINE_INTERVIEWER_STYLE,
            interview_state_json=deepcopy(state.model_dump(mode="json")),
            state_version=1,
        )
        user_turn = DialogueTurn(
            id=91,
            session_id=session.id,
            stage_id=session.current_stage_id,
            turn_index=2,
            speaker="user",
            content=context.latest_user_turn.content,
            content_type="interview_answer",
        )
        planner_result = PlannerAgentResult(
            output=plan.model_copy(deep=True),
            raw_output=plan.model_dump_json(),
            model_name="fixed-planner",
            duration_ms=4,
        )
        interviewer_result = InterviewerAgentResult(
            output=_interviewer_output("为了判断得更稳妥，你会先核实哪一类信息？"),
            raw_output="{}",
            model_name="fixed-interviewer",
            duration_ms=5,
        )
        planner_prompt = SimpleNamespace(id=70, content="planner-template")
        baseline_prompt = SimpleNamespace(
            id=71,
            content="baseline-v3-template",
        )
        db = MagicMock()
        added: list[object] = []
        next_trace_id = 200

        def add(item):
            nonlocal next_trace_id
            if isinstance(item, AgentTrace) and item.id is None:
                item.id = next_trace_id
                next_trace_id += 1
            added.append(item)

        db.add.side_effect = add
        service = SessionService(db)
        service.repo = MagicMock()
        service.repo.get_scenario.return_value = SimpleNamespace(id=10)
        service.repo.list_turns.return_value = []
        service.repo.next_turn_index.return_value = 3
        exact_prompt_lookup = MagicMock(return_value=baseline_prompt)

        with (
            patch(
                "app.services.session_service.get_settings",
                return_value=SimpleNamespace(MODEL_GATEWAY_MODE="mock"),
            ),
            patch.object(
                InterviewStateService,
                "blueprint",
                return_value=blueprint,
            ),
            patch.object(
                InterviewStateService,
                "load",
                return_value=state,
            ),
            patch.object(
                SessionService,
                "_build_agent_context",
                return_value=context,
            ),
            patch.object(
                SessionService,
                "_active_prompt_for_agent",
                return_value=planner_prompt,
            ) as latest_active_lookup,
            patch.object(
                SessionService,
                "_prompt_for_agent_version",
                exact_prompt_lookup,
            ),
            patch.object(
                InterviewPlannerAgent,
                "generate",
                return_value=planner_result,
            ),
            patch.object(
                InterviewPlannerAgent,
                "enforce",
                side_effect=lambda plan_value, *_args: plan_value,
            ),
            patch.object(
                EvidenceTrackerService,
                "apply",
                return_value=[],
            ),
            patch.object(
                InterviewerAgent,
                "render",
                return_value=interviewer_result,
            ) as render_mock,
        ):
            ai_turn, next_action, duration_ms = service._process_progressive_turn(
                session,
                user_turn,
            )

        latest_active_lookup.assert_called_once_with("planner")
        exact_prompt_lookup.assert_called_once_with(
            "interviewer",
            template_code="progressive_interviewer_compact_v2",
            version="progressive_interviewer_compact_v2",
        )
        self.assertEqual(
            render_mock.call_args.kwargs["template_content"],
            "baseline-v3-template",
        )
        interviewer_trace = next(
            trace
            for trace in added
            if isinstance(trace, AgentTrace) and trace.agent_name == "interviewer"
        )
        self.assertEqual(interviewer_trace.prompt_template_id, 71)
        self.assertEqual(
            interviewer_trace.config_snapshot_json["prompt_version"],
            "progressive_interviewer_compact_v2",
        )
        self.assertEqual(ai_turn.source_agent_trace_id, interviewer_trace.id)
        self.assertEqual(next_action, "wait_user_answer")
        self.assertEqual(duration_ms, 9)

    def test_admin_review_and_anonymous_export_include_renderer_audit(
        self,
    ) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        with SessionLocal() as db:
            participant = Participant(
                nickname="审计测试用户",
                info_collect_method="ai_dialogue",
                source="self_assessment",
                status="active",
            )
            scenario = Scenario(
                scenario_code=f"audit-{uuid4().hex[:8]}",
                title="审计测试情境",
                background="用于测试 Renderer 审计字段。",
                target_audience="general",
                scenario_type="test",
                difficulty_level="medium",
                estimated_minutes=5,
                rotation_weight=0,
                is_default=False,
                version="v1",
                status="active",
                source_type="test",
                is_immutable=True,
            )
            db.add_all([participant, scenario])
            db.flush()
            session = AssessmentSession(
                session_uuid=str(uuid4()),
                participant_id=participant.id,
                scenario_id=scenario.id,
                selection_mode="test",
                status="completed",
                assessment_mode="mock",
                flow_version="legacy_v2",
                interviewer_style_version=HUMANISTIC_INTERVIEWER_STYLE,
                state_version=2,
            )
            db.add(session)
            db.flush()
            user_turn = DialogueTurn(
                session_id=session.id,
                turn_index=1,
                speaker="user",
                content="我会先核实证据。",
                content_type="interview_answer",
            )
            db.add(user_turn)
            db.flush()
            planner_trace = AgentTrace(
                session_id=session.id,
                trigger_turn_id=user_turn.id,
                agent_name="consultative_turn",
                generation_mode="mock",
                ai_generation_weight=100,
                config_snapshot_json={
                    "interviewer_style_version": HUMANISTIC_INTERVIEWER_STYLE,
                    "action": "PROBE",
                },
                input_json={"latest_user_turn": user_turn.content},
                output_json={"plan": {"action": "PROBE"}},
                status="success",
                model_name="mock",
                duration_ms=2,
            )
            db.add(planner_trace)
            db.flush()
            renderer_trace = AgentTrace(
                session_id=session.id,
                trigger_turn_id=user_turn.id,
                agent_name="interviewer_renderer",
                generation_mode="mock",
                ai_generation_weight=100,
                config_snapshot_json={
                    "parent_trace_id": planner_trace.id,
                    "interviewer_style_version": HUMANISTIC_INTERVIEWER_STYLE,
                    "validation_codes": ["relational_attachment"],
                    "fallback_reason": "HUMANISTIC_HARD_GATE_FALLBACK",
                    "single_model_attempt": True,
                },
                input_json={"validated_plan": {"action": "PROBE"}},
                output_json={"message": "你会先核实哪一类信息？"},
                status="fallback",
                error_code="HUMANISTIC_HARD_GATE_FALLBACK",
                fallback_type="humanistic_deterministic_renderer",
                model_name="mock",
                duration_ms=3,
            )
            db.add(renderer_trace)
            db.flush()
            ai_turn = DialogueTurn(
                session_id=session.id,
                turn_index=2,
                speaker="ai",
                content="你会先核实哪一类信息？",
                content_type="interview_followup",
                source_agent_trace_id=renderer_trace.id,
            )
            db.add(ai_turn)
            db.commit()

            service = AdminSessionReviewService(db)
            review = service.get_review(
                session.session_uuid,
                current_annotator_id=0,
            )
            renderer_review = next(
                item
                for item in review.traces
                if item.agent_name == "interviewer_renderer"
            )
            self.assertEqual(
                review.session.interviewer_style_version,
                HUMANISTIC_INTERVIEWER_STYLE,
            )
            self.assertEqual(renderer_review.parent_trace_id, planner_trace.id)
            self.assertEqual(
                renderer_review.interviewer_style_version,
                HUMANISTIC_INTERVIEWER_STYLE,
            )
            self.assertEqual(
                renderer_review.validation_codes,
                ["relational_attachment"],
            )
            self.assertEqual(
                renderer_review.fallback_reason,
                "HUMANISTIC_HARD_GATE_FALLBACK",
            )

            exported = service.build_export(
                status_value=None,
                scenario_code=scenario.scenario_code,
                search=None,
                review_status=None,
                low_confidence=False,
                confidence_threshold=0.5,
            )
            exported_session = exported["sessions"][0]
            exported_renderer = next(
                item
                for item in exported["agent_traces"]
                if item["agent_name"] == "interviewer_renderer"
            )
            exported_planner = next(
                item
                for item in exported["agent_traces"]
                if item["agent_name"] == "consultative_turn"
            )
            exported_ai_turn = next(
                item for item in exported["turns"] if item["speaker"] == "ai"
            )
            self.assertEqual(
                exported_session["interviewer_style_version"],
                HUMANISTIC_INTERVIEWER_STYLE,
            )
            self.assertEqual(
                exported_renderer["parent_trace_id"],
                exported_planner["trace_id"],
            )
            self.assertEqual(
                exported_ai_turn["source_agent_trace_id"],
                exported_renderer["trace_id"],
            )
            self.assertEqual(
                exported_renderer["validation_codes"],
                ["relational_attachment"],
            )
            self.assertNotIn(
                session.session_uuid,
                json.dumps(exported, ensure_ascii=False),
            )
            self.assertNotIn(
                participant.nickname,
                json.dumps(exported, ensure_ascii=False),
            )
        engine.dispose()

    def test_v11_pure_authority_routes_are_schema_valid_and_audit_aligned(
        self,
    ) -> None:
        blueprint = _blueprint()
        for text in (
            "你觉得我应该上线还是延期？",
            "如果是你会选哪个？",
        ):
            with self.subTest(text=text):
                base_context = _context()
                latest = base_context.latest_user_turn.model_copy(
                    update={"content": text}
                )
                context = base_context.model_copy(
                    update={
                        "latest_user_turn": latest,
                        "dialogue_history": [latest],
                    }
                )
                state = InterviewState(
                    schema_version="interview_state_v3_3",
                    current_node_code="s1_problem_definition",
                    released_event_codes=["opening_context"],
                    released_unit_codes=[
                        blueprint.event_cards[0].presentation_units[0].unit_code
                    ],
                    task_domain=blueprint.task_domain,
                )
                session = AssessmentSession(
                    id=7,
                    session_uuid=context.session.session_uuid,
                    participant_id=3,
                    scenario_id=10,
                    current_stage_id=11,
                    selection_mode="test",
                    status="in_progress",
                    assessment_mode="mock",
                    flow_version="progressive_v3_3",
                    interviewer_style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                    interview_state_json=deepcopy(state.model_dump(mode="json")),
                    state_version=1,
                )
                user_turn = DialogueTurn(
                    id=91,
                    session_id=session.id,
                    stage_id=session.current_stage_id,
                    turn_index=2,
                    speaker="user",
                    content=text,
                    content_type="interview_answer",
                )
                db = MagicMock()
                added: list[object] = []
                next_id = 400

                def add(item):
                    nonlocal next_id
                    if isinstance(item, (AgentTrace, DialogueTurn)) and item.id is None:
                        item.id = next_id
                        next_id += 1
                    added.append(item)

                db.add.side_effect = add
                service = SessionService(db)
                service.repo = MagicMock()
                service.repo.get_scenario.return_value = SimpleNamespace(id=10)
                service.repo.get_participant.return_value = SimpleNamespace(
                    id=3,
                    nickname="运行测试",
                )
                service.repo.list_turns.return_value = [user_turn]
                service.repo.next_turn_index.return_value = 3
                settings = SimpleNamespace(
                    INTERVIEWER_STYLE_ENABLED=True,
                    INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
                    MODEL_GATEWAY_MODE="mock",
                )
                runtime_settings = SimpleNamespace(
                    RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS=3,
                )

                with (
                    patch(
                        "app.services.session_service.get_settings",
                        return_value=settings,
                    ),
                    patch(
                        "app.agents.runtime_interviewer_agent.get_settings",
                        return_value=settings,
                    ),
                    patch(
                        "app.services.session_service.get_runtime_interview_settings",
                        return_value=runtime_settings,
                    ),
                    patch.object(
                        InterviewStateService,
                        "blueprint",
                        return_value=blueprint,
                    ),
                    patch.object(
                        InterviewStateService,
                        "load",
                        return_value=state,
                    ),
                    patch.object(
                        SessionService,
                        "_build_agent_context",
                        return_value=context,
                    ),
                    patch.object(
                        SessionService,
                        "_prompt_for_agent_version",
                        return_value=None,
                    ),
                ):
                    ai_turn, next_action, _ = service._process_consultative_turn(
                        session,
                        user_turn,
                    )

                renderer_trace = next(
                    item
                    for item in added
                    if isinstance(item, AgentTrace)
                    and item.agent_name == "interviewer_renderer"
                )
                audit = renderer_trace.config_snapshot_json["humanistic_v1_1_audit"]
                plan = next(
                    item
                    for item in added
                    if isinstance(item, AgentTrace)
                    and item.agent_name == "consultative_turn"
                ).output_json["plan"]

                self.assertEqual(next_action, "wait_user_answer")
                self.assertEqual(plan["response_intent"], "redirect")
                self.assertEqual(plan["action"], "CLARIFY")
                self.assertEqual(plan["delivery_mode"], "clarification")
                self.assertEqual(plan["reflection_basis_turn_ids"], [])
                self.assertFalse(user_turn.analysis_json["formal_answer"])
                self.assertEqual(
                    user_turn.analysis_json["evidence_response_origin"],
                    "not_scored",
                )
                self.assertTrue(user_turn.analysis_json["pure_authority_request"])
                self.assertIn("不能替你作出这个决定", ai_turn.content)
                self.assertIn("判断标准", ai_turn.content)
                self.assertEqual(ai_turn.content.count("？"), 1)
                self.assertEqual(
                    audit["candidate_intent_key"], "pure_authority_criteria"
                )
                self.assertEqual(
                    audit["selected_question"],
                    renderer_trace.input_json["selected_question"],
                )
                self.assertIn(audit["selected_question"], ai_turn.content)

    def test_v11_non_measurement_router_preserves_its_factual_response(self) -> None:
        routed_plan = _plan().model_copy(
            update={
                "response_intent": "request_context",
                "action": "CLARIFY",
                "target_dimension": None,
                "target_evidence": None,
                "delivery_mode": "clarification",
                "question_intent": "直接补全用户询问的情境信息",
                "reflection_basis_turn_ids": [],
                "evidence_observations": [],
            }
        )
        routed_message = "眼下是：你们要在五天后完成课程小组作业，" "但完成度和质量还没核实。你会先核实哪一点？"
        routed_result = ConsultativeTurnAgentResult(
            output=ConsultativeTurnOutput(
                plan=routed_plan,
                interviewer=_interviewer_output(routed_message).model_copy(
                    update={"message_type": "clarification"}
                ),
            ),
            raw_output="{}",
            model_name="deterministic-router-v1",
            duration_ms=0,
        )

        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            user_text="眼下是什么情况",
            routed_result=routed_result,
        )

        self.assertEqual(outcome["ai_turn"].content, routed_message)
        renderer_trace = next(
            item
            for item in outcome["added"]
            if isinstance(item, AgentTrace)
            and item.agent_name == "interviewer_renderer"
        )
        audit = renderer_trace.config_snapshot_json["humanistic_v1_1_audit"]
        self.assertFalse(audit["candidate_selection_applied"])
        self.assertEqual(
            audit["renderer_bypass_reason"],
            "deterministic_non_measurement_router",
        )
        self.assertEqual(renderer_trace.input_json["question_candidates"], [])
        self.assertEqual(renderer_trace.output_json["message"], routed_message)

    def test_v11_confusion_router_uses_one_context_aware_renderer_call(self) -> None:
        routed_plan = _plan().model_copy(
            update={
                "response_intent": "clarify_question",
                "action": "CLARIFY",
                "target_dimension": None,
                "target_evidence": None,
                "delivery_mode": "clarification",
                "question_intent": "用更具体的日常语言重述前一个问题",
                "reflection_basis_turn_ids": [],
                "evidence_observations": [],
            }
        )
        routed_output = _interviewer_output("我换个具体问法：你想先确认什么？").model_copy(
            update={"message_type": "clarification"}
        )
        routed_result = ConsultativeTurnAgentResult(
            output=ConsultativeTurnOutput(
                plan=routed_plan,
                interviewer=routed_output,
            ),
            raw_output="{}",
            model_name="deterministic-router-v1",
            duration_ms=0,
        )

        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            user_text="没看懂",
            routed_result=routed_result,
            model_mode="real",
        )

        self.assertEqual(outcome["render_mock"].call_count, 1)
        renderer_trace = next(
            item
            for item in outcome["added"]
            if isinstance(item, AgentTrace)
            and item.agent_name == "interviewer_renderer"
        )
        self.assertTrue(
            renderer_trace.config_snapshot_json["v11_router_model_eligible"]
        )
        self.assertEqual(
            renderer_trace.config_snapshot_json["model_attempt_count"],
            1,
        )

    def test_v11_uncertainty_router_uses_one_context_aware_renderer_call(self) -> None:
        routed_plan = _plan().model_copy(
            update={
                "response_intent": "low_information",
                "action": "CLARIFY",
                "target_dimension": None,
                "target_evidence": None,
                "delivery_mode": "clarification",
                "question_intent": "降低回答负担并提出一个具体小问题",
                "reflection_basis_turn_ids": [],
                "evidence_observations": [],
            }
        )
        routed_output = _interviewer_output("可以先不下结论。你想先弄清哪一点？").model_copy(
            update={"message_type": "clarification"}
        )
        routed_result = ConsultativeTurnAgentResult(
            output=ConsultativeTurnOutput(
                plan=routed_plan,
                interviewer=routed_output,
            ),
            raw_output="{}",
            model_name="deterministic-router-v1",
            duration_ms=0,
        )

        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            user_text="我不知道",
            routed_result=routed_result,
            model_mode="real",
        )

        self.assertEqual(outcome["render_mock"].call_count, 1)
        self.assertFalse(outcome["user_turn"].analysis_json["formal_answer"])
        renderer_trace = next(
            item
            for item in outcome["added"]
            if isinstance(item, AgentTrace)
            and item.agent_name == "interviewer_renderer"
        )
        self.assertTrue(
            renderer_trace.config_snapshot_json["v11_router_model_eligible"]
        )

    def test_v11_router_acknowledges_an_explicit_understanding_correction(
        self,
    ) -> None:
        routed_plan = _plan().model_copy(
            update={
                "response_intent": "conversation_repair",
                "action": "CLARIFY",
                "target_dimension": "problem_definition",
                "target_evidence": ("从未重复的观察角度补充一项可判断信息"),
                "delivery_mode": "clarification",
                "question_intent": ("承接纠错，改从problem_definition角度提出未重复问题"),
                "reflection_basis_turn_ids": [],
                "evidence_observations": [],
            }
        )
        routed_result = ConsultativeTurnAgentResult(
            output=ConsultativeTurnOutput(
                plan=routed_plan,
                interviewer=_interviewer_output(
                    "抱歉，刚才的问题没有承接好。" "你认为眼下最需要判断的核心问题是什么？"
                ).model_copy(update={"message_type": "clarification"}),
            ),
            raw_output="{}",
            model_name="deterministic-router-v1",
            duration_ms=0,
        )
        user_text = "你理解错了，我不是要比较进度和返工；" "我的重点是先确认质量记录是否可信，请换个角度。"

        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            user_text=user_text,
            routed_result=routed_result,
        )

        self.assertIn(
            "刚才我没有接住“先确认质量记录是否可信”",
            outcome["ai_turn"].content,
        )
        self.assertNotIn("比较进度和返工", outcome["ai_turn"].content)
        self.assertEqual(outcome["ai_turn"].content.count("？"), 1)
        renderer_trace = next(
            item
            for item in outcome["added"]
            if isinstance(item, AgentTrace)
            and item.agent_name == "interviewer_renderer"
        )
        self.assertEqual(
            renderer_trace.output_json["reflection_source_quotes"],
            [{"turn_id": 91, "quote": "先确认质量记录是否可信"}],
        )
        self.assertFalse(outcome["user_turn"].analysis_json["formal_answer"])

    def test_v11_mixed_authority_planner_uses_all_sanitized_fragments_and_audits_them(
        self,
    ) -> None:
        original = "我倾向延期，因为故障风险高；" "你替我决定吧；" "我会先核实日志，再设置回滚阈值。"
        expected_fragments = [
            "我倾向延期，因为故障风险高",
            "我会先核实日志，再设置回滚阈值",
        ]
        expected_measurement_text = "；".join(expected_fragments)
        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            user_text=original,
        )

        self.assertEqual(len(outcome["measurement_contexts"]), 1)
        measurement_context = outcome["measurement_contexts"][0]
        self.assertEqual(
            measurement_context.latest_user_turn.content,
            expected_measurement_text,
        )
        self.assertNotIn("替我决定", measurement_context.latest_user_turn.content)
        self.assertEqual(outcome["user_turn"].content, original)
        self.assertTrue(outcome["user_turn"].analysis_json["formal_answer"])
        self.assertTrue(outcome["user_turn"].analysis_json["mixed_authority_request"])
        self.assertEqual(
            outcome["user_turn"].analysis_json["authority_substantive_text"],
            expected_measurement_text,
        )
        self.assertEqual(
            outcome["user_turn"].analysis_json["authority_substantive_fragments"],
            expected_fragments,
        )
        self.assertEqual(
            outcome["user_turn"].analysis_json["authority_removed_spans"],
            ["你替我决定吧"],
        )
        planner_trace = next(
            item
            for item in outcome["added"]
            if isinstance(item, AgentTrace) and item.agent_name == "consultative_turn"
        )
        self.assertEqual(
            planner_trace.input_json["authority_substantive_fragments"],
            expected_fragments,
        )
        self.assertEqual(
            planner_trace.config_snapshot_json["authority_substantive_fragments"],
            expected_fragments,
        )
        self.assertEqual(
            planner_trace.output_json["plan"]["memory_update"]["user_position"],
            expected_measurement_text,
        )

    def test_v11_role_boundary_is_not_mislabeled_as_pure_decision_request(
        self,
    ) -> None:
        outcome = self._run_consultative_turn(
            HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            user_text="你能当我的心理咨询师吗？",
        )

        analysis = outcome["user_turn"].analysis_json
        self.assertFalse(analysis["pure_authority_request"])
        self.assertFalse(analysis["mixed_authority_request"])
        self.assertIsNone(analysis["authority_request_kind"])
        self.assertFalse(analysis["formal_answer"])
        self.assertEqual(analysis["evidence_response_origin"], "not_scored")

    def _run_consultative_turn(
        self,
        style_version: str,
        *,
        force_unsafe_renderer: bool = False,
        prompt_present: bool = True,
        model_mode: str = "mock",
        configured_timeout: int = 3,
        planner_validation_errors: list[str] | None = None,
        user_text: str | None = None,
        routed_result: ConsultativeTurnAgentResult | None = None,
        plan_override: InterviewPlanOutput | None = None,
    ) -> dict[str, object]:
        blueprint = _blueprint()
        plan = plan_override or _plan()
        context = _context()
        if user_text is not None:
            latest = context.latest_user_turn.model_copy(update={"content": user_text})
            context = context.model_copy(
                update={
                    "latest_user_turn": latest,
                    "dialogue_history": [latest],
                }
            )
        state = InterviewState(
            schema_version="interview_state_v3_3",
            current_node_code="s1_problem_definition",
            formal_user_turn_count=0,
            released_event_codes=["opening_context"],
            released_unit_codes=[
                blueprint.event_cards[0].presentation_units[0].unit_code
            ],
            task_domain=blueprint.task_domain,
        )
        session = AssessmentSession(
            id=7,
            session_uuid=context.session.session_uuid,
            participant_id=3,
            scenario_id=10,
            current_stage_id=11,
            selection_mode="test",
            status="in_progress",
            assessment_mode="mock",
            flow_version="progressive_v3_3",
            interviewer_style_version=style_version,
            interview_state_json=deepcopy(state.model_dump(mode="json")),
            state_version=1,
        )
        user_turn = DialogueTurn(
            id=91,
            session_id=session.id,
            stage_id=session.current_stage_id,
            turn_index=2,
            speaker="user",
            content=context.latest_user_turn.content,
            content_type="interview_answer",
        )
        consultative_output = _interviewer_output("为了判断得更稳妥，你会先核实哪一类信息？")
        result = ConsultativeTurnAgentResult(
            output=ConsultativeTurnOutput(
                plan=plan.model_copy(deep=True),
                interviewer=consultative_output,
            ),
            raw_output="{}",
            model_name="fixed-planner",
            duration_ms=17,
        )
        renderer_output = (
            _interviewer_output("我感到此刻和你很亲近。你愿意继续说吗？")
            if force_unsafe_renderer
            else _interviewer_output("为了判断得更稳妥，你愿意先说说会核实哪类信息吗？")
        )
        renderer_result = InterviewerAgentResult(
            output=renderer_output,
            raw_output=renderer_output.model_dump_json(),
            model_name="fixed-renderer",
            duration_ms=5,
            model_attempt_count=1 if model_mode == "real" else 0,
        )

        db = MagicMock()
        added: list[object] = []
        next_trace_id = 100

        def add(item):
            nonlocal next_trace_id
            if isinstance(item, AgentTrace) and item.id is None:
                item.id = next_trace_id
                next_trace_id += 1
            added.append(item)

        db.add.side_effect = add
        service = SessionService(db)
        service.repo = MagicMock()
        service.repo.get_scenario.return_value = SimpleNamespace(id=10)
        service.repo.get_participant.return_value = SimpleNamespace(
            id=3,
            nickname="运行测试",
        )
        service.repo.list_turns.return_value = []
        service.repo.next_turn_index.return_value = 3
        settings = SimpleNamespace(
            INTERVIEWER_STYLE_ENABLED=True,
            INTERVIEWER_STYLE_DEFAULT=HUMANISTIC_INTERVIEWER_STYLE,
            MODEL_GATEWAY_MODE=model_mode,
        )
        runtime_settings = SimpleNamespace(
            RUNTIME_INTERVIEWER_RENDER_TIMEOUT_SECONDS=configured_timeout,
        )
        measurement_contexts: list[AgentRuntimeContext] = []
        original_build_deterministic_plan = (
            InterviewPlannerAgent.build_deterministic_plan
        )

        def capture_build_deterministic_plan(
            planner,
            build_context,
            build_state,
            build_blueprint,
        ):
            measurement_contexts.append(build_context)
            return original_build_deterministic_plan(
                planner,
                build_context,
                build_state,
                build_blueprint,
            )

        validation_call_count = 0

        def validate_turn(_agent, output, **_kwargs):
            nonlocal validation_call_count
            validation_call_count += 1
            if planner_validation_errors and validation_call_count == 1:
                return planner_validation_errors
            if "亲近" in output.message:
                return ["relational_attachment"]
            return []

        prompt = (
            SimpleNamespace(
                id=77,
                content="humanistic_interviewer_v1",
            )
            if prompt_present
            else None
        )
        with (
            patch(
                "app.services.session_service.get_settings",
                return_value=settings,
            ),
            patch(
                "app.services.session_service.get_runtime_interview_settings",
                return_value=runtime_settings,
            ),
            patch.object(
                InterviewStateService,
                "blueprint",
                return_value=blueprint,
            ),
            patch.object(
                InterviewStateService,
                "load",
                return_value=state,
            ),
            patch.object(
                SessionService,
                "_build_agent_context",
                return_value=context,
            ),
            patch.object(
                SessionService,
                "_prompt_for_agent_version",
                return_value=prompt,
            ),
            patch.object(
                ConsultativeTurnAgent,
                "route_repair",
                return_value=routed_result,
            ),
            patch.object(
                ConsultativeTurnAgent,
                "generate",
                return_value=result,
            ),
            patch.object(
                ConsultativeTurnAgent,
                "validate_turn",
                autospec=True,
                side_effect=validate_turn,
            ),
            patch.object(
                InterviewPlannerAgent,
                "enforce",
                side_effect=lambda plan_value, *_args: plan_value,
            ),
            patch.object(
                InterviewPlannerAgent,
                "avoid_duplicate",
                side_effect=lambda plan_value, *_args: plan_value,
            ),
            patch.object(
                InterviewPlannerAgent,
                "build_deterministic_plan",
                autospec=True,
                side_effect=capture_build_deterministic_plan,
            ),
            patch.object(
                EvidenceTrackerService,
                "apply",
                return_value=[],
            ),
            patch.object(
                InterviewerAgent,
                "render",
                return_value=renderer_result,
            ) as render_mock,
        ):
            ai_turn, next_action, duration_ms = service._process_consultative_turn(
                session,
                user_turn,
            )

        routed_model_eligible = bool(
            style_version == HUMANISTIC_INTERVIEWER_STYLE_V1_1
            and routed_result is not None
            and routed_result.output.plan is not None
            and routed_result.output.plan.response_intent
            in {"clarify_question", "low_information"}
        )
        self.assertEqual(
            render_mock.call_count,
            1
            if prompt_present and (routed_result is None or routed_model_eligible)
            else 0,
        )
        self.assertEqual(next_action, "wait_user_answer")
        expected_renderer_duration = (
            renderer_result.duration_ms
            if prompt_present and (routed_result is None or routed_model_eligible)
            else 0
        )
        core_result = routed_result or result
        self.assertEqual(
            duration_ms,
            core_result.duration_ms + expected_renderer_duration,
        )
        return {
            "session": session,
            "user_turn": user_turn,
            "ai_turn": ai_turn,
            "added": added,
            "render_mock": render_mock,
            "measurement_contexts": measurement_contexts,
        }


if __name__ == "__main__":
    unittest.main()
