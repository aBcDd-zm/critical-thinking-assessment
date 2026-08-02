from __future__ import annotations

"""Isolated regression check for occupation-aware scenario preparation.

The script uses a temporary SQLite database, so it never changes local MySQL
data. It covers request validation, profile onboarding, scenario adaptation,
occupation cache reuse, and profile/formal-context isolation.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _stream_events(body: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="adaptive-scenario-check-") as temp_dir:
        database_path = Path(temp_dir) / "check.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
        os.environ["MODEL_GATEWAY_MODE"] = "mock"
        os.environ["INTERVIEW_FLOW_VERSION"] = "legacy_v2"

        sys.path.append(str(Path(__file__).resolve().parents[1]))
        sys.path.append(str(Path(__file__).resolve().parent))

        from fastapi.testclient import TestClient
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.core.database import get_engine, get_sessionmaker
        from app.agents.scenario_design_agent import (
            GeneratedScenario,
            ScenarioAgentResult,
            STAGE_TASK_CONTRACTS,
            normalize_scenario_payload,
            scenario_structure_fingerprint,
        )
        from app.agents.question_contract import (
            DEFAULT_STAGE_CONTRACTS,
            GENERIC_FALLBACK_QUESTION,
            enforce_constraints,
            probe_coverage_real,
            resolve_probe,
        )
        from app.agents.schemas import ResolvedEvidenceItem
        from app.main import app
        from app.models import Base
        from app.models.agent import AgentTrace
        from app.models.assessment import AssessmentSession
        from app.models.scenario import Scenario, ScenarioGenerationJob, ScenarioStage
        from app.services.admin_session_review_service import AdminSessionReviewService
        from seed_db import seed_database

        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        Base.metadata.create_all(get_engine())
        seed_database(Path(__file__).resolve().parents[1] / "seeds")

        client = TestClient(app)
        invalid = client.post(
            "/api/v1/sessions",
            json={"nickname": "缺少职业字段"},
        )
        assert invalid.status_code == 422, invalid.text

        payload = {
            "nickname": "职业情景回归",
            "occupation_category": "教育培训",
            "occupation": "高中生物教师",
            "consent_accepted": True,
            "consent_version": "critical_thinking_assessment_consent_v1",
        }

        def create_and_finish_profile(nickname: str) -> dict[str, object]:
            response = client.post(
                "/api/v1/sessions", json={**payload, "nickname": nickname}
            )
            assert response.status_code == 200, response.text
            created = response.json()
            assert created["phase"] == "onboarding"
            assert created["status"] == "onboarding"
            session_uuid = str(created["session_uuid"])

            first = client.post(
                f"/api/v1/sessions/{session_uuid}/profile/turns/stream",
                json={"content": "我经常组织课堂活动，并根据学生反馈调整讲解。"},
            )
            assert first.status_code == 200, first.text
            first_events = _stream_events(first.text)
            assert any(item["event"] == "profile_answer_saved" for item in first_events)

            current = client.get(f"/api/v1/sessions/{session_uuid}").json()
            if current["phase"] == "onboarding":
                second = client.post(
                    f"/api/v1/sessions/{session_uuid}/profile/turns/stream",
                    json={"content": "我通常和学生及同科教师协作，熟悉在信息不全时安排教学。"},
                )
                assert second.status_code == 200, second.text
                second_events = _stream_events(second.text)
                assert any(item["event"] == "profile_completed" for item in second_events)

            ready = client.get(f"/api/v1/sessions/{session_uuid}")
            assert ready.status_code == 200, ready.text
            state = ready.json()
            assert state["phase"] == "assessment", state
            assert state["status"] == "in_progress", state
            assert state["scenario"]["source_type"] in {"ai_adapted", "ai_base"}
            if state["current_stage"] is None:
                opening = client.post(
                    f"/api/v1/sessions/{session_uuid}/interview/start/stream"
                )
                assert opening.status_code == 200, opening.text
                state = client.get(
                    f"/api/v1/sessions/{session_uuid}"
                ).json()
            assert state["current_stage"]["stage_code"] == "s1_problem_definition"
            assert state["current_stage"]["main_question"] == (
                "看完这些信息，你觉得现在最需要先弄清楚的一件事是什么？"
            )
            return state

        first_state = create_and_finish_profile("职业情景回归A")
        second_state = create_and_finish_profile("职业情景回归B")

        SessionLocal = get_sessionmaker()
        with SessionLocal() as db:
            first_session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == first_state["session_uuid"]
                )
            ).scalar_one()
            first_job_for_structure = db.execute(
                select(ScenarioGenerationJob).where(
                    ScenarioGenerationJob.session_id == first_session.id
                )
            ).scalar_one()
            base_payload = first_job_for_structure.reviewed_json
            assert base_payload is not None
            valid_scenario = GeneratedScenario.model_validate(base_payload)
            base_scenario = db.get(Scenario, first_job_for_structure.base_scenario_id)
            assert base_scenario is not None
            first_stage = db.execute(
                select(ScenarioStage).where(
                    ScenarioStage.scenario_id == base_scenario.id,
                    ScenarioStage.stage_code == "s1_problem_definition",
                )
            ).scalar_one()
            central_decision = str(
                (base_scenario.generation_metadata_json or {}).get("central_decision")
                or ""
            ).rstrip("。！？?!")
            assert central_decision
            assert first_stage.main_question == (
                "看完这些信息，你觉得现在最需要先弄清楚的一件事是什么？"
            )
            assert central_decision not in first_stage.main_question
            assert first_stage.context_generation_constraints_json[
                "cctst_task_contract"
            ] == STAGE_TASK_CONTRACTS["s1_problem_definition"]
            assert "两项限制条件" in first_stage.context_generation_constraints_json[
                "cctst_task_contract"
            ]
            assert first_stage.exit_criteria_json["expected_evidence"] == [
                "核心判断",
                "限制条件",
            ]
            assert "判断边界" not in first_stage.exit_criteria_json[
                "expected_evidence"
            ]
            stage_contract = first_stage.exit_criteria_json.get(
                "question_contract"
            ) or {}
            assert len(stage_contract.get("probes") or []) == 2
            assert all(
                probe.get("mode") == "strategy_guided"
                for probe in stage_contract.get("probes") or []
            )
            assert "no_reask_core" in (stage_contract.get("constraints") or [])
            assert "no_cross_stage_duplicate" in (
                stage_contract.get("constraints") or []
            )
            wrapped_payload = {
                "schema_version": "occupation_scenario_v2",
                "professional_knowledge_required": False,
                "contains_real_personal_data": False,
                "scenario": {
                    key: value
                    for key, value in valid_scenario.model_dump().items()
                    if key
                    not in {
                        "schema_version",
                        "professional_knowledge_required",
                        "contains_real_personal_data",
                    }
                },
            }
            normalized_payload = normalize_scenario_payload(wrapped_payload)
            assert GeneratedScenario.model_validate(normalized_payload) == valid_scenario
            changed_payload = valid_scenario.model_dump()
            changed_payload["stages"][0]["context"] = changed_payload["stages"][0][
                "context"
            ].replace("60", "61", 1)
            changed_scenario = GeneratedScenario.model_validate(changed_payload)
            assert scenario_structure_fingerprint(valid_scenario) != (
                scenario_structure_fingerprint(changed_scenario)
            )

            invalid_function_payload = valid_scenario.model_dump()
            invalid_function_payload["stages"][1]["dynamic_infos"][0][
                "measurement_function"
            ] = "wrong_function"
            try:
                GeneratedScenario.model_validate(invalid_function_payload)
            except ValueError:
                pass
            else:
                raise AssertionError("Invalid dynamic measurement function was accepted.")

            invalid_knowledge_payload = valid_scenario.model_dump()
            invalid_knowledge_payload["professional_knowledge_required"] = True
            try:
                GeneratedScenario.model_validate(invalid_knowledge_payload)
            except ValueError:
                pass
            else:
                raise AssertionError("Professional-knowledge dependency was accepted.")

        s1_contract = DEFAULT_STAGE_CONTRACTS["s1_problem_definition"]
        v2_expected = ["核心判断", "限制条件"]

        def s1_probe(evidence: list) -> dict[str, object]:
            return resolve_probe(
                s1_contract,
                probe_coverage_real(evidence),
                expected_evidence=v2_expected,
                followups_used=0,
                max_followups=2,
            )

        repaired = s1_probe(
            [
                ResolvedEvidenceItem(
                    evidence_key="核心判断",
                    coverage="partial",
                    supporting_turn_indexes=[9],
                    reason="用户已经提出原因诊断问题。",
                ),
                ResolvedEvidenceItem(
                    evidence_key="限制条件",
                    coverage="missing",
                    reason="尚未提及限制条件。",
                ),
            ]
        )
        assert repaired.get("evidence_gap") == "限制条件（第一项）"
        assert "核心问题是什么" not in str(repaired.get("question"))
        assert "哪一项" in str(repaired.get("question"))
        assert "两项" not in str(repaired.get("question"))

        second_constraint = s1_probe(
            [
                ResolvedEvidenceItem(
                    evidence_key="核心判断",
                    coverage="covered",
                    supporting_turn_indexes=[9],
                    reason="用户已提出明确的原因诊断问题。",
                ),
                ResolvedEvidenceItem(
                    evidence_key="限制条件",
                    coverage="partial",
                    supporting_turn_indexes=[11],
                    reason="用户已指出一项限制条件。",
                ),
            ]
        )
        assert second_constraint.get("evidence_gap") == "限制条件（第二项）"
        assert "还有哪一项" in str(second_constraint.get("question"))
        assert "两项" not in str(second_constraint.get("question"))

        complete_constraints = s1_probe(
            [
                ResolvedEvidenceItem(
                    evidence_key="核心判断",
                    coverage="covered",
                    supporting_turn_indexes=[9],
                    reason="核心判断清楚。",
                ),
                ResolvedEvidenceItem(
                    evidence_key="限制条件",
                    coverage="covered",
                    supporting_turn_indexes=[9],
                    reason="已提出两项不同限制。",
                ),
            ]
        )
        assert complete_constraints == {}

        missing_core = s1_probe(
            [
                ResolvedEvidenceItem(
                    evidence_key="核心判断",
                    coverage="missing",
                    reason="尚未提出核心问题。",
                ),
                ResolvedEvidenceItem(
                    evidence_key="限制条件",
                    coverage="missing",
                    reason="尚未提出限制条件。",
                ),
            ]
        )
        assert missing_core == {}

        # Constraint gate on the freshly materialized S1 contract: compliant
        # wording passes untouched, the V2.2 re-ask drift and the observed
        # 2026-07-17 cross-stage repetition both fall back with warnings, and
        # a contract-less stage stays fully untouched.
        gate_context = SimpleNamespace(
            candidate_intervention_rules=[],
            dialogue_history=[
                SimpleNamespace(
                    speaker="ai",
                    stage_code="s5_dynamic_adjustment",
                    content_type="followup_question",
                    content=(
                        "你提到用实时错误率和投诉率作为触发条件，如果灰度期间错误率没超阈值"
                        "但用户反馈中频繁出现“数据同步延迟”，你会怎么看待这个信号？"
                    ),
                )
            ],
        )
        armed_coverage = {"核心判断": "partial", "限制条件": "missing"}

        clean_question = "你刚提到进度压力。题面里哪一项现实条件最限制你作出这个判断？"
        passed_question, gate_warnings = enforce_constraints(
            stage_contract, clean_question, gate_context, coverage=armed_coverage
        )
        assert passed_question == clean_question and gate_warnings == []

        reask_question, reask_warnings = enforce_constraints(
            stage_contract,
            "那眼下最需要先判断清楚的核心问题是什么？",
            gate_context,
            coverage=armed_coverage,
        )
        assert reask_question == GENERIC_FALLBACK_QUESTION
        assert "question_contract_violation:no_reask_core" in reask_warnings

        duplicate_question, duplicate_warnings = enforce_constraints(
            stage_contract,
            (
                "你提到分阶段灰度上线，我想了解：如果灰度期间核心错误率没超阈值，"
                "但用户反馈中频繁出现“数据同步延迟”，你会怎么看待这个信号？"
            ),
            gate_context,
            coverage=armed_coverage,
        )
        assert duplicate_question == GENERIC_FALLBACK_QUESTION
        assert (
            "question_contract_violation:no_cross_stage_duplicate"
            in duplicate_warnings
        )

        untouched_question, untouched_warnings = enforce_constraints(
            {},
            "那眼下最需要先判断清楚的核心问题是什么？",
            gate_context,
            coverage=armed_coverage,
        )
        assert untouched_question == "那眼下最需要先判断清楚的核心问题是什么？"
        assert untouched_warnings == []

        with SessionLocal() as db:
            first_session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == first_state["session_uuid"]
                )
            ).scalar_one()
            second_session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == second_state["session_uuid"]
                )
            ).scalar_one()
            first_job = db.execute(
                select(ScenarioGenerationJob).where(
                    ScenarioGenerationJob.session_id == first_session.id
                )
            ).scalar_one()
            second_job = db.execute(
                select(ScenarioGenerationJob).where(
                    ScenarioGenerationJob.session_id == second_session.id
                )
            ).scalar_one()
            assert first_job.base_scenario_id == second_job.base_scenario_id
            assert second_job.cache_hit is True
            assert second_job.design_call_count == 0
            assert first_job.adaptation_call_count <= 1
            assert second_job.adaptation_call_count <= 1

        failed_payload = {
            "nickname": "降级回归",
            "occupation_category": "学生",
            "occupation": "大学生",
            "consent_accepted": True,
            "consent_version": "critical_thinking_assessment_consent_v1",
        }
        failed_result = ScenarioAgentResult(
            success=False,
            scenario=None,
            raw_output="",
            model_name=None,
            error_code="TEST_GENERATION_FAILURE",
            error_reason="synthetic failure",
        )
        with patch(
            "app.services.scenario_generation_service.ScenarioDesignAgent.generate_base",
            return_value=failed_result,
        ):
            failed_create = client.post("/api/v1/sessions", json=failed_payload)
        assert failed_create.status_code == 200, failed_create.text
        failed_uuid = failed_create.json()["session_uuid"]
        for answer in ("我经常完成课程任务。", "我会与同学协作并安排时间。"):
            response = client.post(
                f"/api/v1/sessions/{failed_uuid}/profile/turns/stream",
                json={"content": answer},
            )
            assert response.status_code == 200, response.text
        failed_state = client.get(f"/api/v1/sessions/{failed_uuid}").json()
        assert failed_state["phase"] == "assessment"
        assert failed_state["scenario"]["source_type"] == "seeded_fallback"
        assert failed_state["scenario_preparation"]["fallback_used"] is True

        adaptation_create = client.post(
            "/api/v1/sessions",
            json={
                "nickname": "适配降级回归",
                "occupation_category": "教育培训",
                "occupation": "大学辅导员",
                "consent_accepted": True,
                "consent_version": "critical_thinking_assessment_consent_v1",
            },
        )
        assert adaptation_create.status_code == 200, adaptation_create.text
        adaptation_uuid = adaptation_create.json()["session_uuid"]
        first_profile = client.post(
            f"/api/v1/sessions/{adaptation_uuid}/profile/turns/stream",
            json={"content": "我经常安排学生事务。"},
        )
        assert first_profile.status_code == 200, first_profile.text
        adaptation_failure = ScenarioAgentResult(
            success=False,
            scenario=None,
            raw_output="",
            model_name=None,
            error_code="TEST_ADAPTATION_FAILURE",
            error_reason="synthetic adaptation failure",
        )
        with patch(
            "app.services.scenario_generation_service.ScenarioDesignAgent.adapt_for_profile",
            return_value=adaptation_failure,
        ):
            second_profile = client.post(
                f"/api/v1/sessions/{adaptation_uuid}/profile/turns/stream",
                json={"content": "我与学生和教师协作，判断任务优先级。"},
            )
        assert second_profile.status_code == 200, second_profile.text
        adaptation_state = client.get(
            f"/api/v1/sessions/{adaptation_uuid}"
        ).json()
        assert adaptation_state["phase"] == "assessment"
        assert adaptation_state["scenario"]["source_type"] == "ai_base"
        assert adaptation_state["scenario_preparation"]["fallback_used"] is False

        formal_answer = "核心判断是是否调整当前安排；约束包括时间有限和协作资源有限。"
        turn_response = client.post(
            f"/api/v1/sessions/{first_state['session_uuid']}/turns/stream",
            json={"content": formal_answer, "content_type": "scenario_answer"},
        )
        assert turn_response.status_code == 200, turn_response.text
        assert not any(
            event["event"] == "error" for event in _stream_events(turn_response.text)
        )

        with SessionLocal() as db:
            first_session = db.execute(
                select(AssessmentSession).where(
                    AssessmentSession.session_uuid == first_state["session_uuid"]
                )
            ).scalar_one()
            formal_trace = db.execute(
                select(AgentTrace)
                .where(
                    AgentTrace.session_id == first_session.id,
                    AgentTrace.agent_name == "followup",
                )
                .order_by(AgentTrace.id.desc())
            ).scalars().first()
            assert formal_trace is not None
            serialized_context = json.dumps(
                formal_trace.input_json, ensure_ascii=False, sort_keys=True
            )
            assert "profile_question" not in serialized_context
            assert "profile_answer" not in serialized_context
            assert "我经常组织课堂活动" not in serialized_context

            export_payload = AdminSessionReviewService(db).build_export(
                status_value=None,
                scenario_code=None,
                search=None,
                review_status=None,
                low_confidence=False,
                confidence_threshold=0.6,
            )
            serialized_export = json.dumps(export_payload, ensure_ascii=False)
            assert "高中生物教师" not in serialized_export
            assert "我经常组织课堂活动" not in serialized_export
            assert "profile_question" not in serialized_export
            assert "profile_answer" not in serialized_export
            assert "教育培训" in serialized_export

        print("Adaptive scenario flow regression passed.")
        print("validation, onboarding, cache, adaptation, isolation: passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
