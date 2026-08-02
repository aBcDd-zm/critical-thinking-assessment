from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents import ReportOutput, ScoringOutput  # noqa: E402
from app.agents.report_agent import ReportAgent  # noqa: E402
from app.agents.scoring_agent import ScoringAgent  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.participant import Participant  # noqa: E402
from app.models.report import AssessmentReport  # noqa: E402
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot  # noqa: E402
from app.models.scenario import ScenarioStage  # noqa: E402
from app.repositories.session_repository import SessionRepository  # noqa: E402
from app.services.report_service import ReportService  # noqa: E402
from app.services.scoring_service import ScoringService  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate C-line scoring/report DB flow.")
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep the synthetic session data for manual inspection.",
    )
    return parser.parse_args()


def create_synthetic_session(db):
    repo = SessionRepository(db)
    scenario = repo.get_default_scenario()
    if scenario is None:
        raise AssertionError("Default scenario not found. Please run scripts/seed_db.py first.")
    stage = db.execute(
        select(ScenarioStage)
        .where(
            ScenarioStage.scenario_id == scenario.id,
            ScenarioStage.stage_code == "s6_integrated_plan",
            ScenarioStage.status == "active",
        )
        .limit(1)
    ).scalar_one_or_none()
    if stage is None:
        stage = repo.get_first_active_stage(scenario.id)
    if stage is None:
        raise AssertionError("Default scenario has no active stage.")

    participant = Participant(
        nickname=f"DEV-C-{uuid4().hex[:8]}",
        info_collect_method="synthetic_fixture",
        source="self_assessment",
        status="active",
    )
    db.add(participant)
    db.flush()

    now = datetime.now(UTC).replace(tzinfo=None)
    session = AssessmentSession(
        session_uuid=str(uuid4()),
        participant_id=participant.id,
        scenario_id=scenario.id,
        current_stage_id=stage.id,
        selection_mode="manual",
        selection_reason="dev-c synthetic report generation flow",
        status="completed",
        assessment_mode="mock",
        started_at=now,
        completed_at=now,
        total_duration_seconds=60,
    )
    db.add(session)
    db.flush()

    opening = DialogueTurn(
        session_id=session.id,
        stage_id=stage.id,
        turn_index=1,
        speaker="ai",
        content=stage.main_question,
        content_type="stage_question",
    )
    answer = DialogueTurn(
        session_id=session.id,
        stage_id=stage.id,
        turn_index=2,
        speaker="user",
        content=(
            "我会采用分阶段灰度上线。先限制入口和用户范围，保留回滚开关；"
            "研发在 24 小时内验证弱网和低端设备问题，运营准备 FAQ 和人工响应，"
            "市场改成小范围发布。判断依据是同步失败集中在核心链路，"
            "直接全量上线风险太高，但完全延期也会损失窗口。"
        ),
        content_type="scenario_answer",
    )
    db.add_all([opening, answer])
    db.commit()
    db.refresh(session)
    db.refresh(answer)
    return session, participant, answer


def cleanup_synthetic_data(db, session_id: int, participant_id: int) -> None:
    db.query(AssessmentReport).filter(AssessmentReport.session_id == session_id).delete()
    snapshot_ids = [
        row[0]
        for row in db.query(ScoreSnapshot.id)
        .filter(ScoreSnapshot.session_id == session_id)
        .all()
    ]
    if snapshot_ids:
        result_ids = [
            row[0]
            for row in db.query(ScoreResult.id)
            .filter(ScoreResult.snapshot_id.in_(snapshot_ids))
            .all()
        ]
        if result_ids:
            db.query(ScoreEvidence).filter(
                ScoreEvidence.score_result_id.in_(result_ids)
            ).delete(synchronize_session=False)
        db.query(ScoreResult).filter(
            ScoreResult.snapshot_id.in_(snapshot_ids)
        ).delete(synchronize_session=False)
        db.query(ScoreSnapshot).filter(
            ScoreSnapshot.id.in_(snapshot_ids)
        ).delete(synchronize_session=False)
    db.query(AgentTrace).filter(AgentTrace.session_id == session_id).delete()
    db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).delete()
    db.query(AssessmentSession).filter(AssessmentSession.id == session_id).delete()
    db.query(Participant).filter(Participant.id == participant_id).delete()
    db.commit()


def main() -> int:
    args = parse_args()
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    session_id: int | None = None
    participant_id: int | None = None
    try:
        session, participant, latest_user_turn = create_synthetic_session(db)
        session_id = session.id
        participant_id = participant.id

        context = SessionService(db)._build_agent_context(session, latest_user_turn)
        scoring_output = ScoringOutput.model_validate(
            ScoringAgent().generate(context, snapshot_type="final").model_dump()
        )
        scoring_service = ScoringService(db)
        snapshot = scoring_service.persist_scoring_output(context, scoring_output)
        scoring_service.persist_scoring_failure(
            context,
            error_code="DEV_C_SYNTHETIC_SCORING_FAILURE",
            reason="synthetic failed trace validation",
        )
        report_output = ReportOutput.model_validate(
            ReportAgent().generate(context, scoring_output).model_dump()
        )
        report_service = ReportService(db)
        persisted_report = report_service.persist_report_output(context, report_output)
        updated_report = report_service.persist_report_output(context, report_output)
        report_count = db.query(AssessmentReport).filter(
            AssessmentReport.session_id == session.id
        ).count()
        if report_count != 1:
            raise AssertionError(f"Expected one report after update-if-exists, got {report_count}")
        if updated_report.id != persisted_report.id:
            raise AssertionError("Report update-if-exists created a second report row.")
        report_service.persist_report_failure(
            context,
            error_code="DEV_C_SYNTHETIC_REPORT_FAILURE",
            reason="synthetic failed trace validation",
        )
        db.commit()

        response = SessionService(db).get_report(session.session_uuid)
        if response.report.get("summary") != report_output.summary:
            raise AssertionError("Readable report summary mismatch.")
        if not response.report.get("disclaimer"):
            raise AssertionError("Readable report missing disclaimer.")
        required_report_keys = {
            "summary",
            "overall_level",
            "dimension_reports",
            "advantages",
            "improvement_suggestions",
            "development_plan",
            "disclaimer",
        }
        missing = required_report_keys - set(response.report)
        if missing:
            raise AssertionError(f"Report API missing keys: {sorted(missing)}")
        if len(response.report["dimension_reports"]) != 6:
            raise AssertionError("Report API should return six dimension reports")
        trace_statuses = {
            row[0]
            for row in db.query(AgentTrace.status)
            .filter(AgentTrace.session_id == session.id)
            .all()
        }
        if trace_statuses != {"ok", "failed"}:
            raise AssertionError(f"Unexpected AgentTrace statuses: {trace_statuses}")

        print("Report generation DB flow passed:")
        print(f"  session_uuid={session.session_uuid}")
        print(f"  snapshot_id={snapshot.id}")
        print(f"  report_id={persisted_report.id}")
        print(f"  score_count={len(scoring_output.scores)}")
        print(f"  report_status={response.status}")
        print(f"  cleanup={'skipped' if args.keep_data else 'done'}")
        if not args.keep_data:
            cleanup_synthetic_data(db, session_id, participant_id)
        return 0
    except Exception:
        db.rollback()
        if session_id is not None and participant_id is not None and not args.keep_data:
            cleanup_synthetic_data(db, session_id, participant_id)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
