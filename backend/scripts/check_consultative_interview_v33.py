from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4


def _events(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines if line.strip()]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="consultative-v33-check-") as temp_dir:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp_dir) / 'check.db'}"
        os.environ["MODEL_GATEWAY_MODE"] = "mock"
        os.environ["INTERVIEW_FLOW_VERSION"] = "progressive_v3_3"
        root = Path(__file__).resolve().parents[1]
        sys.path.extend([str(root), str(Path(__file__).resolve().parent)])

        from sqlalchemy import select

        from app.agents.consultative_turn_agent import ConsultativeTurnAgent
        from app.agents.interview_planner_agent import InterviewPlannerAgent
        from app.agents.progressive_schemas import (
            DimensionSlotState,
            InterviewPlanOutput,
            InterviewState,
            PlannerBudget,
        )
        from app.agents.schemas import (
            DimensionScore,
            EvidenceItem,
            ScoringOutput,
        )
        from app.agents.user_turn_intent import is_scoring_analysis
        from app.core.config import get_settings
        from app.core.runtime_interview_config import (
            get_runtime_interview_settings,
        )
        from app.core.database import get_engine, get_sessionmaker
        from app.agents.interview_question_validator import InterviewQuestionValidator
        from app.agents.runtime_interviewer_agent import InterviewerAgent
        from app.models import Base
        from app.models.assessment import AssessmentSession
        from app.models.agent import AgentTrace
        from app.schemas.session import (
            CreateSessionRequest,
            ProfileTurnRequest,
            SubmitTurnRequest,
        )
        from app.services.evidence_sufficiency_service import (
            EvidenceSufficiencyService,
        )
        from app.services.interview_state_service import InterviewStateService
        from app.services.session_service import SessionService
        from seed_db import seed_database

        get_settings.cache_clear()
        get_runtime_interview_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        configured_turn_timeout = (
            get_settings().CONSULTATIVE_TURN_TIMEOUT_SECONDS
        )
        assert 1 <= configured_turn_timeout <= 30
        configured_runtime_timeout = (
            get_runtime_interview_settings()
            .RUNTIME_CONSULTATIVE_TURN_TIMEOUT_SECONDS
        )
        assert 5 <= configured_runtime_timeout <= 15
        deterministic_observations = InterviewPlannerAgent._mock_observations(  # noqa: SLF001
            "这个判断依赖样本可比和口径一致两个前提；"
            "若盲核发现高风险漏检就是反例，应推翻结论。"
            "只有交叉验证证据同时达标，才支持继续扩大。"
        )
        reasoning_behaviors = {
            item.behavior_key
            for item in deterministic_observations
            if item.dimension_key == "reasoning_argumentation"
        }
        assert reasoning_behaviors == {
            "explain_premise_evidence_inference",
            "identify_assumption_risk_counterexample",
            "connect_evidence_and_conclusion",
        }
        Base.metadata.create_all(get_engine())
        seed_database(root / "seeds")

        with get_sessionmaker()() as db:
            service = SessionService(db)
            created = service.create_session(
                CreateSessionRequest(
                    nickname="大学生验收",
                    occupation_category="学生",
                    occupation="大学生",
                    consent_accepted=True,
                    consent_version="critical_thinking_assessment_consent_v1",
                )
            )
            assert created.flow_version == "progressive_v3_3"
            for answer in ("课程学习和小组作业", "老师和同学"):
                list(
                    service.stream_profile_turn(
                        created.session_uuid, ProfileTurnRequest(content=answer)
                    )
                )
            session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == created.session_uuid
                )
            ).scalar_one()
            scenario = service.repo.get_scenario(session.scenario_id)
            blueprint = InterviewStateService.blueprint(scenario)
            assert blueprint.schema_version == "occupation_interview_skeleton_v3_3"
            assert blueprint.current_arrangement and blueprint.new_arrangement
            assert blueprint.pilot_arrangement and blueprint.stakeholder_conflict
            visible_facts = "".join(
                unit.text
                for event in blueprint.event_cards
                for unit in event.presentation_units
            )
            assert "减少交接和检查" in visible_facts
            assert "逐项交接检查" in visible_facts
            assert "增加返工和质量风险" in visible_facts
            assert InterviewQuestionValidator.fact_is_supported(
                "一部分人想少做交接检查赶进度，另一部分担心返工和质量风险。",
                "一部分参与者想减少交接和检查以赶进度，另一部分担心这样会增加返工和质量风险。",
            )
            assert not InterviewQuestionValidator.fact_is_supported(
                "一部分人想赶进度，另一部分没有意见。",
                "一部分参与者想减少交接和检查以赶进度，另一部分担心这样会增加返工和质量风险。",
            )
            repair_agent = ConsultativeTurnAgent()
            reported_quote = (
                "我会先列出每个人负责的部分，核实完成到哪一步，"
                "并确认有没有还没通过检查的内容"
            )
            assert repair_agent._repair_display_quote(reported_quote) == reported_quote  # noqa: SLF001
            assert repair_agent._repair_display_quote(  # noqa: SLF001
                "我会先核实完成情况和质量记录，然后确认哪些模块还没有通过检查，"
                "最后和同学一起安排补救任务并每天更新进度"
            ) == "我会先核实完成情况和质量记录，然后确认哪些模块还没有通过检查"
            for repair_variant in (
                "这个问题你已经问过了，请换个角度",
                "我前面已经回答过这个问题",
                "为什么又问同一个问题",
                "不要再重复提问",
                (
                    "我刚才其实已经说过了：我会先看返工是因为交接不清、"
                    "数据出错，还是标准没有统一。你现在这个问题和上一题有些重复。"
                    "如果还需要我补充，可以换一个更具体的角度问。"
                ),
            ):
                assert (
                    repair_agent._repair_intent(repair_variant)  # noqa: SLF001
                    == "conversation_repair"
                )
                assert not is_scoring_analysis(
                    {
                        "analysis_source": "consultative_turn_v3_3",
                        "formal_answer": True,
                        "response_intent": "assess_answer",
                        "excluded_from_scoring": False,
                    },
                    text=repair_variant,
                )
            assert repair_agent._repair_intent("我会安排重复测试两轮") is None  # noqa: SLF001

            first_fallback_probe = InterviewerAgent._probe_message(  # noqa: SLF001
                "integrative_decision",
                [],
            )
            second_fallback_probe = InterviewerAgent._probe_message(  # noqa: SLF001
                "integrative_decision",
                [first_fallback_probe],
            )
            assert second_fallback_probe != first_fallback_probe
            assert "概括准确吗" not in first_fallback_probe
            assert "概括准确吗" not in second_fallback_probe
            assert "先做哪一步" not in first_fallback_probe
            assert "先做哪一步" not in second_fallback_probe

            first_clarify = InterviewerAgent._clarify_message(  # noqa: SLF001
                "clarify",
                [],
            )
            second_clarify = InterviewerAgent._clarify_message(  # noqa: SLF001
                "clarify",
                [first_clarify],
            )
            assert second_clarify != first_clarify
            assert "准备先做哪一步" not in first_clarify
            assert "准备先做哪一步" not in second_clarify

            uat_dimension_slots = {
                dimension_key: DimensionSlotState(
                    dimension_key=dimension_key,
                    status=(
                        "sufficient"
                        if dimension_key == "problem_definition"
                        else "blocked"
                    ),
                    insufficient_reason=(
                        None
                        if dimension_key == "problem_definition"
                        else "probe_budget_exhausted"
                    ),
                )
                for dimension_key in (
                    "problem_definition",
                    "evidence_evaluation",
                    "reasoning_argumentation",
                    "multiple_perspectives",
                    "integrative_decision",
                    "dynamic_adjustment",
                )
            }
            uat_state = InterviewState(
                schema_version="interview_state_v3_3",
                current_node_code="s6_integrated_plan",
                formal_user_turn_count=10,
                released_event_codes=[
                    "opening_context",
                    "evidence_uncertainty",
                    "stakeholder_conflict",
                    "decision_pressure",
                    "counter_evidence",
                    "integration",
                ],
                dimension_slots=uat_dimension_slots,
                dimension_opportunity_counts={
                    "problem_definition": 1,
                    "evidence_evaluation": 1,
                    "reasoning_argumentation": 1,
                    "multiple_perspectives": 0,
                    "integrative_decision": 1,
                    "dynamic_adjustment": 1,
                },
            )

            # Regression: only a target from a question already shown to the
            # user may be credited to the answer currently being processed.
            assert (
                SessionService._opportunity_target_for_answer(uat_state)
                is None
            )
            uat_state.last_plan = {
                "target_dimension": "problem_definition",
            }
            assert (
                SessionService._opportunity_target_for_answer(uat_state)
                == "problem_definition"
            )
            uat_state.last_plan = {
                "target_dimension": "not_a_dimension",
            }
            assert (
                SessionService._opportunity_target_for_answer(uat_state)
                is None
            )
            uat_state.last_plan = None

            first_repair = repair_agent._repair_followup(  # noqa: SLF001
                uat_state
            )
            first_target, first_intent, first_question = first_repair
            assert first_target in uat_state.dimension_slots
            assert "用什么结果判断" not in first_question

            uat_state.asked_intent_keys.append(
                "|".join(
                    (
                        "CLARIFY",
                        first_target,
                        first_intent,
                        "-",
                    )
                )
            )
            uat_state.last_plan = {
                "target_dimension": first_target,
            }

            second_repair = repair_agent._repair_followup(  # noqa: SLF001
                uat_state
            )
            second_target, _, second_question = second_repair
            assert second_target != first_target
            assert second_question != first_question
            assert "用什么结果判断" not in second_question

            uat_state.asked_intent_keys.clear()
            uat_state.last_plan = None

            premature_conclusion = InterviewPlanOutput(
                response_intent="assess_answer",
                action="CONCLUDE",
                active_topic="信息核实",
                target_dimension="problem_definition",
                target_evidence="补充当前判断的一项关键依据",
                delivery_mode="closing",
                question_intent="顺着当前话题补充一项尚未充分的证据",
                reason="UAT_V33_01 原始结束计划",
                budget=PlannerBudget(
                    used_turns=10,
                    remaining_turns=2,
                    reserved_update_turns=2,
                    reserved_closure_turns=1,
                ),
            )
            deferred_conclusion = InterviewPlannerAgent().enforce(
                premature_conclusion,
                uat_state,
                blueprint,
            )
            assert uat_state.turn_latency_budget_ms == 15_000
            assert deferred_conclusion.action == "PROBE"
            assert deferred_conclusion.target_dimension == "multiple_perspectives"
            assert deferred_conclusion.budget.remaining_turns == 2
            assert (
                "conclusion deferred for incomplete dimension coverage"
                in deferred_conclusion.warnings
            )
            rerendered = repair_agent.rerender_after_plan_enforcement(
                service._build_agent_context(session, None),  # noqa: SLF001
                uat_state,
                blueprint,
                deferred_conclusion,
                fallback_used=False,
            )
            assert rerendered.fallback_used is False
            assert rerendered.question_count == 1
            assert (
                "planner action enforced; deterministic rerender applied"
                in rerendered.warnings
            )

            opening = _events(list(service.stream_start_interview(created.session_uuid)))
            opening_text = next(
                item["ai_turn"]["content"]
                for item in opening
                if item["event"] == "agent_completed"
            )
            assert "完成度和质量还没核实" in opening_text
            assert opening_text.count("？") == 1 and len(opening_text) <= 90

            def submit(content: str) -> tuple[str, object]:
                request_id = str(uuid4())
                result = _events(
                    list(
                        service.stream_submit_turn(
                            created.session_uuid,
                            SubmitTurnRequest(
                                client_turn_id=request_id,
                                content=content,
                            ),
                        )
                    )
                )
                completed = next(
                    item for item in result if item["event"] == "agent_completed"
                )
                user_turn = service.repo.get_user_turn_by_client_id(
                    session.id, request_id
                )
                return completed["ai_turn"]["content"], user_turn

            context_text, context_turn = submit("眼下是什么情况")
            assert "五天后" in context_text and "完成度和质量" in context_text
            assert context_turn.analysis_json["formal_answer"] is False
            assert context_turn.analysis_json["response_intent"] == "request_context"

            arrangements_text, arrangements_turn = submit("新安排和原安排分别是什么")
            assert "新安排是减少交接和检查" in arrangements_text
            assert "原安排是继续逐项交接检查" in arrangements_text
            assert arrangements_turn.analysis_json["formal_answer"] is False

            answer_text, answer_turn = submit(
                "我会先核实当前完成度和质量记录，再决定是否减少检查。"
            )
            assert answer_turn.analysis_json["formal_answer"] is True
            timeline_length_before_repair = len(
                session.interview_state_json["evidence_timeline"]
            )
            repair_text, repair_turn = submit(
                "这个问题你已经问过了，请换个角度"
            )
            repair_trace = db.execute(
                select(AgentTrace)
                .where(
                    AgentTrace.trigger_turn_id == repair_turn.id,
                    AgentTrace.agent_name == "consultative_turn",
                )
                .order_by(AgentTrace.id.desc())
            ).scalars().first()
            assert "不会再重复" in repair_text, (
                repair_text,
                repair_trace.config_snapshot_json.get("validation_errors"),
            )
            assert repair_turn.analysis_json["formal_answer"] is False
            assert repair_turn.analysis_json["excluded_from_scoring"] is True
            assert answer_turn.content[:12] in repair_text
            assert "用什么结果判断" not in repair_text
            assert (
                repair_trace.config_snapshot_json["hidden_target_dimension"]
                is not None
            )
            if session.interviewer_style_version == "humanistic_v1":
                renderer_trace = db.execute(
                    select(AgentTrace)
                    .where(
                        AgentTrace.trigger_turn_id == repair_turn.id,
                        AgentTrace.agent_name == "interviewer_renderer",
                    )
                    .order_by(AgentTrace.id.desc())
                ).scalars().first()
                assert renderer_trace is not None
                assert (
                    renderer_trace.config_snapshot_json["parent_trace_id"]
                    == repair_trace.id
                )
                assert (
                    repair_turn.analysis_json["renderer_trace_id"]
                    == renderer_trace.id
                )

            state = session.interview_state_json
            assert state["formal_user_turn_count"] == 1
            assert state["context_repair_count"] == 3
            assert len(state["evidence_timeline"]) == timeline_length_before_repair
            assert all(
                repair_turn.id not in (slot.get("evidence_turn_ids") or [])
                for slot in state["dimension_slots"].values()
            )
            isolated_analysis = dict(repair_turn.analysis_json)
            repair_turn.analysis_json = {
                **isolated_analysis,
                "formal_answer": True,
                "response_intent": "assess_answer",
                "excluded_from_scoring": False,
            }
            db.flush()
            contaminated_quality = EvidenceSufficiencyService(
                db
            ).measurement_quality(session)
            assert contaminated_quality.status == "invalid"
            assert repair_turn.id in (
                contaminated_quality.scoring_contamination_turn_ids
            )

            raw_invalid_report = {
                "summary": "不应继续展示的总体解释",
                "overall_level": "high",
                "dimension_reports": [
                    {
                        "dimension_key": "problem_definition",
                        "score": 5,
                    }
                ],
                "dimension_scores": [{"score": 5}],
                "advantages": ["不应继续展示的优势"],
                "strengths": ["不应继续展示的旧版优势"],
                "improvement_suggestions": ["不应继续展示的建议"],
                "development_plan": ["不应继续展示的发展计划"],
            }
            safe_invalid_report = service._with_measurement_quality(  # noqa: SLF001
                session,
                raw_invalid_report,
            )
            assert safe_invalid_report["overall_level"] == "结果无效"
            assert safe_invalid_report["dimension_reports"] == []
            assert safe_invalid_report["dimension_scores"] == []
            assert safe_invalid_report["advantages"] == []
            assert safe_invalid_report["strengths"] == []
            assert safe_invalid_report["improvement_suggestions"] == []
            assert safe_invalid_report["development_plan"] == []
            assert (
                safe_invalid_report["measurement_quality"]["status"]
                == "invalid"
            )
            # API 净化不得反向改写数据库中保存的原始报告内容。
            assert raw_invalid_report["dimension_reports"][0]["score"] == 5
            assert raw_invalid_report["advantages"] == [
                "不应继续展示的优势"
            ]

            repair_turn.analysis_json = isolated_analysis
            db.flush()
            assert len(state["asked_intent_keys"]) == len(
                set(state["asked_intent_keys"])
            )
            assert all(value <= 2 for value in state["topic_probe_counters"].values())

            original_interview_state = session.interview_state_json
            recovered_state = json.loads(json.dumps(original_interview_state))
            recovered_state["released_event_codes"] = [
                "opening_context",
                "evidence_uncertainty",
                "stakeholder_conflict",
                "decision_pressure",
                "counter_evidence",
                "integration",
            ]
            session.interview_state_json = recovered_state
            existing_trace_count = sum(
                1
                for item in db.execute(
                    select(AgentTrace).where(
                        AgentTrace.session_id == session.id,
                        AgentTrace.agent_name == "consultative_turn",
                    )
                ).scalars()
                if (item.config_snapshot_json or {}).get(
                    "measurement_scope"
                )
                == "formal_answer"
            )
            synthetic_traces = []
            while existing_trace_count < 4:
                trace = AgentTrace(
                    session_id=session.id,
                    stage_id=session.current_stage_id,
                    agent_name="consultative_turn",
                    generation_mode="deterministic",
                    ai_generation_weight=0,
                    config_snapshot_json={
                        "measurement_scope": "formal_answer",
                        "measurement_core_status": "success",
                        "model_call_status": "not_called",
                    },
                    input_json={},
                    output_json={},
                    status="success",
                    duration_ms=0,
                )
                db.add(trace)
                synthetic_traces.append(trace)
                existing_trace_count += 1
                db.flush()
            synthetic_failure_count = 0
            while synthetic_failure_count / (
                existing_trace_count + synthetic_failure_count
            ) < 0.30:
                trace = AgentTrace(
                    session_id=session.id,
                    stage_id=session.current_stage_id,
                    agent_name="consultative_turn",
                    generation_mode="real",
                    ai_generation_weight=100,
                    config_snapshot_json={
                        "measurement_scope": "formal_answer",
                        "measurement_core_status": "failed",
                        "model_call_status": "not_called",
                    },
                    input_json={},
                    output_json={},
                    status="fallback",
                    error_code="CONSULTATIVE_TURN_FALLBACK",
                    fallback_type="deterministic_consultative_turn",
                    duration_ms=15000,
                )
                db.add(trace)
                synthetic_traces.append(trace)
                synthetic_failure_count += 1
                db.flush()
            assert synthetic_failure_count / (
                existing_trace_count + synthetic_failure_count
            ) < 0.50
            supported_scores = [
                DimensionScore(
                    dimension_key=dimension_key,
                    score=3,
                    assessment_status="scored",
                    reason="回归测试",
                    score_kind="supported",
                )
                for dimension_key in recovered_state["dimension_slots"]
            ]
            recovered_quality = EvidenceSufficiencyService(
                db
            ).measurement_quality(session, supported_scores)
            assert recovered_quality.status == "caution"
            assert any(
                "确定性降级已保留完整事件和六维证据" in reason
                for reason in recovered_quality.reasons
            )
            for trace in synthetic_traces:
                db.delete(trace)
            session.interview_state_json = original_interview_state
            db.flush()

            completed_progress = SessionService._build_interview_progress(
                AssessmentSession(
                    status="completed",
                    started_at=datetime.utcnow() - timedelta(seconds=75),
                    total_duration_seconds=75,
                    interview_state_json={"formal_user_turn_count": 10},
                )
            )
            assert completed_progress.percent == 100
            assert completed_progress.elapsed_seconds == 75

            sufficient_state = {
                "dimension_slots": {
                    "problem_definition": {
                        "status": "sufficient",
                        "conflicting_evidence_turn_ids": [],
                    }
                },
                "dimension_opportunity_counts": {"problem_definition": 1},
                "dimension_opportunity_quality": {"problem_definition": 25},
                "weak_evidence_turn_ids": {"problem_definition": []},
                "technical_fallback_count": 0,
                "evidence_timeline": [
                    {
                        "observations": [
                            {
                                "dimension_key": "problem_definition",
                                "behavior_key": "a",
                                "validity": "valid",
                            },
                            {
                                "dimension_key": "problem_definition",
                                "behavior_key": "b",
                                "validity": "valid",
                            },
                        ]
                    }
                ],
            }
            esi = EvidenceSufficiencyService.dimension_result(
                sufficient_state, "problem_definition"
            )
            assert esi.index == 100 and esi.level == "high"
            unobserved = EvidenceSufficiencyService.dimension_result(
                sufficient_state, "dynamic_adjustment"
            )
            assert unobserved.index is None and unobserved.score_kind == "unobserved"

            partial_state = json.loads(json.dumps(sufficient_state))
            partial_state["dimension_slots"]["problem_definition"]["status"] = "partial"
            partial_state["evidence_timeline"][0]["observations"] = [
                partial_state["evidence_timeline"][0]["observations"][0]
            ]
            partial = EvidenceSufficiencyService.dimension_result(
                partial_state, "problem_definition"
            )
            assert partial.index == 80 and partial.score_kind == "provisional"
            partial_output, _ = EvidenceSufficiencyService(db).apply_scoring(
                AssessmentSession(
                    id=999999,
                    session_uuid=str(uuid4()),
                    participant_id=999999,
                    scenario_id=999999,
                    selection_mode="test",
                    status="completed",
                    assessment_mode="mock",
                    flow_version="progressive_v3_3",
                    interview_state_json=partial_state,
                ),
                ScoringOutput(
                    snapshot_type="final",
                    summary="partial",
                    scores=[
                        DimensionScore(
                            dimension_key="problem_definition",
                            score=4,
                            assessment_status="scored",
                            confidence=0.8,
                            reason="模型暂定分",
                            evidence=[
                                EvidenceItem(
                                    text="测试证据",
                                    evidence_type="supporting_evidence",
                                )
                            ],
                        )
                    ],
                ),
            )
            assert partial_output.scores[0].score is None
            assert partial_output.scores[0].assessment_status == "insufficient_evidence"
            assert partial_output.scores[0].evidence == []

            weak_state = json.loads(json.dumps(partial_state))
            weak_state["evidence_timeline"][0]["observations"][0]["validity"] = "weak"
            weak_state["weak_evidence_turn_ids"] = {"problem_definition": [7]}
            weak = EvidenceSufficiencyService.dimension_result(
                weak_state, "problem_definition"
            )
            assert weak.index == 60

            conflict_state = json.loads(json.dumps(sufficient_state))
            conflict_state["dimension_slots"]["problem_definition"][
                "conflicting_evidence_turn_ids"
            ] = [9]
            contradictory = EvidenceSufficiencyService.dimension_result(
                conflict_state, "problem_definition"
            )
            assert contradictory.index == 74

            blocked_state = json.loads(json.dumps(partial_state))
            blocked_state["dimension_slots"]["problem_definition"]["status"] = "blocked"
            blocked_state["dimension_slots"]["problem_definition"][
                "insufficient_reason"
            ] = "technical_failure"
            blocked_state["technical_fallback_count"] = 2
            blocked = EvidenceSufficiencyService.dimension_result(
                blocked_state, "problem_definition"
            )
            assert blocked.index == 49
            nontechnical_blocked_state = json.loads(json.dumps(blocked_state))
            nontechnical_blocked_state["dimension_slots"]["problem_definition"][
                "insufficient_reason"
            ] = "probe_budget_exhausted"
            nontechnical_blocked = EvidenceSufficiencyService.dimension_result(
                nontechnical_blocked_state, "problem_definition"
            )
            assert nontechnical_blocked.index == 75

    print("consultative interview v3.3 check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
