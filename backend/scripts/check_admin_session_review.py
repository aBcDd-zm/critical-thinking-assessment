from __future__ import annotations

import io
import csv
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.feedback import SessionFeedback  # noqa: E402
from app.models.participant import Participant  # noqa: E402
from app.models.report import AssessmentReport  # noqa: E402
from app.models.review import ExpertScoreAnnotation, HumanReview  # noqa: E402
from app.models.rubric import RubricDimension  # noqa: E402
from app.models.scenario import Scenario, ScenarioStage  # noqa: E402
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot  # noqa: E402


def create_fixture(db):
    scenario = db.execute(
        select(Scenario).where(Scenario.status == "active").order_by(Scenario.id)
    ).scalars().first()
    if scenario is None:
        raise AssertionError("Seeded active scenario is required")
    stage = db.execute(
        select(ScenarioStage)
        .where(ScenarioStage.scenario_id == scenario.id)
        .order_by(ScenarioStage.stage_order)
    ).scalars().first()
    dimension = db.execute(
        select(RubricDimension).where(RubricDimension.status == "active")
    ).scalars().first()
    if stage is None or dimension is None:
        raise AssertionError("Seeded stage and rubric dimension are required")

    nickname = f"隐私测试用户{uuid4().hex[:6]}"
    session_uuid = str(uuid4())
    now = datetime.utcnow()
    participant = Participant(
        nickname=nickname,
        info_collect_method="ai_dialogue",
        source="self_assessment",
        status="active",
    )
    db.add(participant)
    db.flush()
    session = AssessmentSession(
        session_uuid=session_uuid,
        participant_id=participant.id,
        scenario_id=scenario.id,
        current_stage_id=stage.id,
        selection_mode="manual",
        selection_reason="admin review validation fixture",
        status="completed",
        assessment_mode="mock",
        started_at=now,
        completed_at=now,
        total_duration_seconds=180,
    )
    db.add(session)
    db.flush()
    user_turn = DialogueTurn(
        session_id=session.id,
        stage_id=stage.id,
        turn_index=1,
        speaker="user",
        content=f"我是{nickname}，测试会话是{session_uuid}，我会先核实证据。",
        content_type="scenario_answer",
    )
    db.add(user_turn)
    db.flush()
    trace = AgentTrace(
        session_id=session.id,
        stage_id=stage.id,
        trigger_turn_id=user_turn.id,
        agent_name="followup",
        generation_mode="mock",
        ai_generation_weight=0,
        config_snapshot_json={"fixture": True},
        input_json={
            "session": {"session_id": session.id, "session_uuid": session_uuid},
            "participant": {"participant_id": participant.id, "nickname": nickname},
        },
        output_json={"question": f"{nickname}，你会如何核实？"},
        raw_output=f"trace for {session_uuid}",
        status="ok",
        error_code=None,
        model_name="mock",
        duration_ms=12,
    )
    db.add(trace)
    db.flush()
    user_turn.source_agent_trace_id = trace.id
    snapshot = ScoreSnapshot(
        session_id=session.id,
        stage_id=stage.id,
        dialogue_turn_id=user_turn.id,
        snapshot_type="final",
        summary=f"{nickname} 展示了证据意识",
        trend_analysis="fixture",
        agent_trace_id=trace.id,
    )
    db.add(snapshot)
    db.flush()
    result = ScoreResult(
        snapshot_id=snapshot.id,
        dimension_id=dimension.id,
        score=3,
        reason="能够提出核实证据",
        confidence=0.8,
        scoring_source="mock",
    )
    db.add(result)
    db.flush()
    evidence = ScoreEvidence(
        score_result_id=result.id,
        dialogue_turn_id=user_turn.id,
        evidence_text=user_turn.content,
        evidence_type="supporting_evidence",
        explanation="fixture evidence",
    )
    report = AssessmentReport(
        session_id=session.id,
        agent_trace_id=trace.id,
        report_json={"summary": f"{nickname} 的报告", "session_uuid": session_uuid},
        summary=f"{nickname} 的报告",
        status="generated",
    )
    feedback = SessionFeedback(
        session_id=session.id,
        realism_score=4,
        difficulty_score=3,
        naturalness_score=4,
        fatigue_score=2,
        report_trust_score=4,
        overall_satisfaction_score=4,
        open_feedback=f"{nickname} 的开放反馈",
        status="active",
    )
    db.add_all([evidence, report, feedback])
    db.commit()
    return {
        "participant_id": participant.id,
        "session_id": session.id,
        "session_uuid": session_uuid,
        "nickname": nickname,
        "scenario_code": scenario.scenario_code,
    }


def cleanup_fixture(db, fixture) -> None:
    session_id = fixture["session_id"]
    db.query(ExpertScoreAnnotation).filter(
        ExpertScoreAnnotation.session_id == session_id
    ).delete()
    db.query(HumanReview).filter(HumanReview.session_id == session_id).delete()
    snapshot_ids = list(
        db.execute(
            select(ScoreSnapshot.id).where(ScoreSnapshot.session_id == session_id)
        ).scalars()
    )
    result_ids = (
        list(
            db.execute(
                select(ScoreResult.id).where(ScoreResult.snapshot_id.in_(snapshot_ids))
            ).scalars()
        )
        if snapshot_ids
        else []
    )
    if result_ids:
        db.query(ScoreEvidence).filter(ScoreEvidence.score_result_id.in_(result_ids)).delete(
            synchronize_session=False
        )
    if snapshot_ids:
        db.query(ScoreResult).filter(ScoreResult.snapshot_id.in_(snapshot_ids)).delete(
            synchronize_session=False
        )
        db.query(ScoreSnapshot).filter(ScoreSnapshot.id.in_(snapshot_ids)).delete(
            synchronize_session=False
        )
    db.query(AssessmentReport).filter(AssessmentReport.session_id == session_id).delete()
    db.query(SessionFeedback).filter(SessionFeedback.session_id == session_id).delete()
    db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).update(
        {DialogueTurn.source_agent_trace_id: None}
    )
    db.query(AgentTrace).filter(AgentTrace.session_id == session_id).delete()
    db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).delete()
    db.query(AssessmentSession).filter(AssessmentSession.id == session_id).delete()
    db.query(Participant).filter(Participant.id == fixture["participant_id"]).delete()
    db.commit()


def main() -> int:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    fixtures = []
    try:
        fixture = create_fixture(db)
        second_fixture = create_fixture(db)
        fixtures.extend([fixture, second_fixture])
        client = TestClient(app)
        unauthorized_paths = [
            "/api/v1/admin/sessions",
            "/api/v1/admin/sessions/export",
            f"/api/v1/admin/sessions/{fixture['session_uuid']}/review",
        ]
        for path in unauthorized_paths:
            unauthorized = client.get(path)
            if unauthorized.status_code != 401:
                raise AssertionError(f"Expected 401 for {path}, got {unauthorized.status_code}")

        settings = get_settings()
        login = client.post(
            "/api/v1/admin/auth/login",
            json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD},
        )
        if login.status_code != 200:
            raise AssertionError(f"Admin login failed: {login.status_code} {login.text}")
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        listing = client.get(
            "/api/v1/admin/sessions",
            params={
                "search": fixture["nickname"],
                "status": "completed",
                "scenario_code": fixture["scenario_code"],
                "page": 1,
                "page_size": 1,
            },
            headers=headers,
        )
        if listing.status_code != 200 or listing.json()["total"] != 1:
            raise AssertionError(f"Unexpected session list: {listing.text}")
        empty_listing = client.get(
            "/api/v1/admin/sessions",
            params={"search": f"missing-{uuid4()}"},
            headers=headers,
        )
        if empty_listing.status_code != 200 or empty_listing.json()["total"] != 0:
            raise AssertionError(f"Unexpected empty list response: {empty_listing.text}")

        review = client.get(
            f"/api/v1/admin/sessions/{fixture['session_uuid']}/review", headers=headers
        )
        review_payload = review.json()
        if review.status_code != 200:
            raise AssertionError(f"Review failed: {review.status_code} {review.text}")
        if not review_payload["turns"] or not review_payload["traces"]:
            raise AssertionError("Review is missing turns or traces")
        if not review_payload["score_snapshots"] or review_payload["report"] is None:
            raise AssertionError("Review is missing scoring or report data")

        missing = client.get(
            f"/api/v1/admin/sessions/{uuid4()}/review", headers=headers
        )
        if missing.status_code != 404:
            raise AssertionError(f"Expected review 404, got {missing.status_code}")

        exports = []
        for _ in range(2):
            response = client.get(
                "/api/v1/admin/sessions/export",
                params={"format": "json", "search": fixture["nickname"]},
                headers=headers,
            )
            if response.status_code != 200:
                raise AssertionError(f"JSON export failed: {response.text}")
            exports.append(response.json())
        first_session = exports[0]["sessions"][0]
        if first_session["anonymous_session_id"] != exports[1]["sessions"][0][
            "anonymous_session_id"
        ]:
            raise AssertionError("Anonymous session id is not stable")
        second_export = client.get(
            "/api/v1/admin/sessions/export",
            params={"format": "json", "search": second_fixture["nickname"]},
            headers=headers,
        ).json()
        if first_session["anonymous_session_id"] == second_export["sessions"][0][
            "anonymous_session_id"
        ]:
            raise AssertionError("Different sessions received the same anonymous id")
        serialized = json.dumps(exports[0], ensure_ascii=False)
        if fixture["nickname"] in serialized or fixture["session_uuid"] in serialized:
            raise AssertionError("Direct identity leaked into JSON export")
        if '"participant_id"' in serialized:
            raise AssertionError("Participant internal id leaked into JSON export")

        zip_response = client.get(
            "/api/v1/admin/sessions/export",
            params={"format": "csv_zip", "search": fixture["nickname"]},
            headers=headers,
        )
        if zip_response.status_code != 200:
            raise AssertionError(f"CSV ZIP export failed: {zip_response.text}")
        with zipfile.ZipFile(io.BytesIO(zip_response.content)) as archive:
            expected = {
                "manifest.json",
                "sessions.csv",
                "turns.csv",
                "agent_traces.csv",
                "score_snapshots.csv",
                "score_results.csv",
                "score_evidence.csv",
                "reports.csv",
                "feedback.csv",
                "human_reviews.csv",
                "expert_scores.csv",
            }
            if set(archive.namelist()) != expected:
                raise AssertionError(f"Unexpected ZIP files: {archive.namelist()}")
            if not archive.read("sessions.csv").startswith(b"\xef\xbb\xbf"):
                raise AssertionError("CSV does not use UTF-8 BOM")
            manifest = json.loads(archive.read("manifest.json"))
            for table_name, expected_count in manifest["record_counts"].items():
                data = archive.read(f"{table_name}.csv").decode("utf-8-sig")
                actual_count = len(list(csv.DictReader(io.StringIO(data)))) if data else 0
                if actual_count != expected_count:
                    raise AssertionError(
                        f"CSV count mismatch for {table_name}: {actual_count} != {expected_count}"
                    )
            zip_text = b"".join(archive.read(name) for name in archive.namelist())
            if fixture["nickname"].encode() in zip_text or fixture["session_uuid"].encode() in zip_text:
                raise AssertionError("Direct identity leaked into CSV ZIP export")

        print("Admin session review check passed:")
        print(f"  session_uuid={fixture['session_uuid']}")
        print(f"  anonymous_session_id={first_session['anonymous_session_id']}")
        print(f"  turns={len(review_payload['turns'])}")
        print(f"  traces={len(review_payload['traces'])}")
        print(f"  zip_files={len(expected)}")
        return 0
    finally:
        for fixture in reversed(fixtures):
            cleanup_fixture(db, fixture)
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
