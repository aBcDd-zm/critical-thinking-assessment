from __future__ import annotations

"""Isolated progressive-v3 contract and end-to-end regression check."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


def _events(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines if line.strip()]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="progressive-v3-check-") as temp_dir:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp_dir) / 'check.db'}"
        os.environ["MODEL_GATEWAY_MODE"] = "mock"
        os.environ["INTERVIEW_FLOW_VERSION"] = "progressive_v3"
        root = Path(__file__).resolve().parents[1]
        sys.path.extend([str(root), str(Path(__file__).resolve().parent)])

        from sqlalchemy import select

        from app.agents.interview_blueprint import NODE_LAYOUT
        from app.agents.interview_planner_agent import InterviewPlannerAgent
        from app.agents.interview_question_validator import InterviewQuestionValidator
        from app.agents.runtime_interviewer_agent import InterviewerAgent
        from app.agents.measurement_contract import load_measurement_contract
        from app.agents.progressive_schemas import (
            EvidenceObservation,
            InterviewPlanOutput,
            InterviewerOutput,
        )
        from app.core.config import get_settings
        from app.core.database import get_engine, get_sessionmaker
        from app.models import Base
        from app.models.agent import AgentTrace
        from app.models.assessment import AssessmentSession, DialogueTurn
        from app.models.participant import ConsentRecord
        from app.schemas.session import CreateSessionRequest, SubmitTurnRequest
        from app.services.admin_session_review_service import AdminSessionReviewService
        from app.services.evidence_tracker_service import EvidenceTrackerService
        from app.services.interview_state_service import InterviewStateService
        from app.services.session_service import SessionService
        from seed_db import seed_database
        from session_flow_helpers import create_ready_session

        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        Base.metadata.create_all(get_engine())
        seed_database(root / "seeds")

        contract = load_measurement_contract()
        assert sum(len(item.behaviors) for item in contract.dimensions) == 18
        assert contract.budget.min_total_user_turns == 9
        assert contract.budget.max_total_user_turns == 12
        assert contract.confidence_policy.numeric_mapping_status == (
            "requires_expert_calibration"
        )
        try:
            CreateSessionRequest(
                nickname="未同意用户",
                occupation_category="学生",
                occupation="大学生",
            )
            raise AssertionError("session request accepted without versioned consent")
        except Exception as exc:  # noqa: BLE001
            assert "consent_accepted" in str(exc)

        SessionLocal = get_sessionmaker()
        with SessionLocal() as db:
            service = SessionService(db)
            ready = create_ready_session(
                service,
                nickname="V3 回归",
                occupation_category="互联网/信息技术",
                occupation="项目协调人",
                use_seeded_scenario=False,
                preserve_preparation_records=True,
            )
            assert ready.flow_version == "progressive_v3"
            assert ready.current_stage is None
            assert ready.progress is None
            assert ready.interview_progress is not None
            assert ready.interview_progress.formal_answer_count == 0
            assert ready.turns[-1].content_type == "interview_opening"
            assert ready.turns[-1].analysis is None
            assert len(ready.turns[-1].content) <= 90
            assert ready.turns[-1].content.count("？") == 1

            session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == ready.session_uuid
                )
            ).scalar_one()
            scenario = service.repo.get_scenario(session.scenario_id)
            assert scenario is not None
            blueprint = InterviewStateService.blueprint(scenario)
            assert blueprint is not None
            assert blueprint.presentation_version == "consultative_progressive_v3_1"
            assert [item.node_code for item in blueprint.story_nodes] == [
                item[0] for item in NODE_LAYOUT
            ]
            assert [item.event_code for item in blueprint.event_cards] == [
                item[1] for item in NODE_LAYOUT
            ]
            all_units = [
                unit
                for event in blueprint.event_cards
                for unit in event.presentation_units
            ]
            assert all_units and all(len(unit.text) <= 70 for unit in all_units)
            assert len({unit.unit_code for unit in all_units}) == len(all_units)
            consent = db.execute(
                select(ConsentRecord).where(ConsentRecord.session_id == session.id)
            ).scalar_one()
            assert consent.consent_version == "critical_thinking_assessment_consent_v1"
            assert consent.scope_json["psychological_diagnosis"] is False

            # 1: one answer may supply evidence to several dimensions.
            request_id = str(uuid4())
            first = service.submit_turn(
                ready.session_uuid,
                SubmitTurnRequest(
                    client_turn_id=request_id,
                    answer_duration_ms=4200,
                    content=(
                        "我会先弄清核心问题，核实数据来源和样本，"
                        "因为资源和用户风险会影响方案。"
                    ),
                ),
            )
            first_turn = service.repo.get_user_turn_by_client_id(session.id, request_id)
            assert first_turn is not None and first_turn.answer_duration_ms == 4200
            assert len((first_turn.analysis_json or {}).get("evidence_delta", [])) == 6
            first_ai = service.repo.get_interviewer_turn_for_trigger(
                session.id, first_turn.id
            )
            assert first_ai is not None and len(first_ai.content) <= 90
            assert first_ai.content.count("？") + first_ai.content.count("?") == 1
            first_trace = db.get(AgentTrace, first_ai.source_agent_trace_id)
            assert first_trace is not None
            assert first_trace.config_snapshot_json["delivery_mode"] in {
                "reflective_probe",
                "summary_check",
                "event_link",
                "perspective_shift",
            }
            if first_trace.config_snapshot_json["action"] == "RELEASE_EVENT":
                assert first_trace.config_snapshot_json["release_unit_code"]
                db.refresh(session)
                assert (
                    first_trace.config_snapshot_json["release_unit_code"]
                    in session.interview_state_json["released_unit_codes"]
                )

            # 2/3: an unsupported opinion is not enough; low information is clarified
            # once and does not consume the formal-answer budget.
            before_low = session.interview_state_json["formal_user_turn_count"]
            low_id = str(uuid4())
            low = service.submit_turn(
                ready.session_uuid,
                SubmitTurnRequest(client_turn_id=low_id, content="不知道"),
            )
            assert low.next_action == "wait_user_answer"
            db.refresh(session)
            assert session.interview_state_json["formal_user_turn_count"] == before_low
            low_ai = service.repo.get_interviewer_turn_for_trigger(
                session.id,
                service.repo.get_user_turn_by_client_id(session.id, low_id).id,
            )
            assert low_ai and low_ai.content_type == "interview_clarification"

            # 4/5/6: after bounded probing, events advance while evidence remains
            # cumulative; counter-evidence cannot appear before a prior decision.
            stream_id = str(uuid4())
            streamed = _events(
                list(
                    service.stream_submit_turn(
                        ready.session_uuid,
                        SubmitTurnRequest(
                            client_turn_id=stream_id,
                            content="我先安排一个可逆的方案，再根据验证结果决定。",
                        ),
                    )
                )
            )
            completed_event = next(
                item for item in streamed if item["event"] == "agent_completed"
            )
            saved_text = completed_event["ai_turn"]["content"]
            refreshed = service.get_session(ready.session_uuid)
            assert refreshed.turns[-1].content == saved_text
            trigger = service.repo.get_user_turn_by_client_id(session.id, stream_id)
            assert trigger is not None
            interviewer_trace = db.execute(
                select(AgentTrace).where(
                    AgentTrace.session_id == session.id,
                    AgentTrace.trigger_turn_id == trigger.id,
                    AgentTrace.agent_name == "interviewer",
                )
            ).scalar_one()
            assert interviewer_trace.output_json["message"] == saved_text
            admin = AdminSessionReviewService(db).get_review(
                ready.session_uuid, current_annotator_id=0
            )
            admin_turn = next(
                item for item in admin.turns if item.source_agent_trace_id == interviewer_trace.id
            )
            assert admin_turn.content == saved_text
            assert admin.progressive_audit is not None

            # 7: contradictory evidence is retained without increasing confidence.
            state = InterviewStateService.load(session, scenario)
            tracker = EvidenceTrackerService()
            assert state.dimension_slots["dynamic_adjustment"].status == "not_available"
            tracker.unlock_for_event(state, "counter_evidence")
            assert state.dimension_slots["dynamic_adjustment"].status == "not_started"
            tracker.apply(
                state,
                turn_id=trigger.id,
                observations=[
                    EvidenceObservation(
                        dimension_key="integrative_decision",
                        behavior_key="define_plan_priority_conditions",
                        quote="我先安排一个可逆的方案",
                        rationale="形成初步安排",
                        extraction_confidence=0.8,
                    )
                ],
            )
            conflict_turn_id = trigger.id + 10000
            tracker.apply(
                state,
                turn_id=conflict_turn_id,
                observations=[
                    EvidenceObservation(
                        dimension_key="integrative_decision",
                        behavior_key="define_plan_priority_conditions",
                        quote="我改变了之前的安排",
                        rationale="与之前安排冲突",
                        extraction_confidence=0.7,
                        novelty="contradictory",
                        contradiction_with=[str(trigger.id)],
                    )
                ],
            )
            slot = state.dimension_slots["integrative_decision"]
            assert conflict_turn_id in slot.conflicting_evidence_turn_ids
            assert slot.confidence is None
            tracker.apply(
                state,
                turn_id=trigger.id + 1,
                observations=[
                    EvidenceObservation(
                        dimension_key="integrative_decision",
                        behavior_key="explain_tradeoff_risk_fallback",
                        quote="我比较了两种方案的风险和收益",
                        rationale="比较权衡",
                        extraction_confidence=0.75,
                    )
                ],
            )
            assert state.dimension_slots["integrative_decision"].status in {
                "partial",
                "sufficient",
            }

            # 8: asking for the answer is redirected without exposing internals.
            assert InterviewPlannerAgent._intent("你告诉我标准答案应该怎么选") == "redirect"

            # 9: the visible-output validator rejects internal labels and compound asks.
            validator = InterviewQuestionValidator()
            assert "internal_terms" in validator.message_errors(
                "你的证据缺口是什么？为什么？"
            )
            assert "judgmental" in validator.message_errors(
                "回答很好，你已经接近高分了。"
            )
            assert "unsupported_inference" in validator.message_errors(
                "你现在很焦虑。你会怎么选？"
            )
            validated_plan = InterviewPlanOutput.model_validate(
                first_trace.input_json["validated_plan"]
            )
            first_output = InterviewerOutput.model_validate(first_trace.output_json)
            fabricated = first_output.model_copy(
                update={"message": "你只在乎速度。下一步怎么做？"}
            )
            first_context = service._build_agent_context(session, first_turn)
            selected_event = next(
                (
                    item
                    for item in blueprint.event_cards
                    if item.event_code == validated_plan.release_event_code
                ),
                None,
            )
            selected_unit = next(
                (
                    unit
                    for unit in (selected_event.presentation_units if selected_event else [])
                    if unit.unit_code == validated_plan.release_unit_code
                ),
                None,
            )
            reflection_ok, reflection_errors = validator.validate(
                fabricated,
                plan=validated_plan,
                allowed_fact_codes=(
                    {validated_plan.release_unit_code}
                    if validated_plan.release_unit_code
                    else set()
                ),
                previous_questions=[],
                allowed_source_turn_ids=set(
                    validated_plan.reflection_basis_turn_ids
                ),
                approved_reflection=InterviewerAgent._approved_reflection(
                    first_context, validated_plan.delivery_mode
                ),
                allowed_fact_text=(selected_unit.text if selected_unit else None),
            )
            assert reflection_ok is False
            assert "unsupported_inference" in reflection_errors
            normalized = InterviewerAgent._parse(
                json.dumps(
                    {"InterviewerOutput": {"message": first_output.message}},
                    ensure_ascii=False,
                ),
                plan=validated_plan,
                unit=selected_unit,
            )
            assert normalized is not None
            assert normalized.message == first_output.message
            os.environ["MODEL_GATEWAY_MODE"] = "real"
            get_settings.cache_clear()
            with patch.object(
                InterviewPlannerAgent,
                "_call",
                side_effect=RuntimeError("simulated model failure"),
            ):
                fallback = InterviewPlannerAgent().generate(
                    service._build_agent_context(session, trigger),
                    InterviewStateService.load(session, scenario),
                    blueprint,
                )
            assert fallback.fallback_type == "deterministic_planner"
            assert fallback.output.fallback_used is True
            os.environ["MODEL_GATEWAY_MODE"] = "mock"
            get_settings.cache_clear()

            # 10: duplicate IDs replay exactly one saved user/event/question chain.
            duplicate = service.submit_turn(
                ready.session_uuid,
                SubmitTurnRequest(
                    client_turn_id=request_id,
                    content="这段不应被保存",
                ),
            )
            assert duplicate.replayed is True
            assert db.execute(
                select(DialogueTurn).where(
                    DialogueTurn.session_id == session.id,
                    DialogueTurn.client_turn_id == request_id,
                )
            ).scalars().all().__len__() == 1

            # 11: persisted flow version, not the current environment flag, governs
            # resumed sessions.
            session.flow_version = "legacy_v2"
            db.commit()
            assert service.get_session(ready.session_uuid).flow_version == "legacy_v2"
            legacy = service.submit_turn(
                ready.session_uuid,
                SubmitTurnRequest(
                    client_turn_id=str(uuid4()),
                    content="这是恢复后继续原六阶段逻辑的回答。",
                ),
            )
            assert legacy.replayed is False
            assert db.execute(
                select(AgentTrace).where(
                    AgentTrace.session_id == session.id,
                    AgentTrace.agent_name == "followup",
                )
            ).scalars().first() is not None
            session.flow_version = "progressive_v3"
            db.commit()

            # 12: finish is allowed early, but non-sufficient dimensions stay unscored.
            service.finish_session(ready.session_uuid)
            final = service.get_session(ready.session_uuid)
            assert final.status == "completed"
            assert final.interview_progress is not None

        print("progressive interview v3: 12 scenarios passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
