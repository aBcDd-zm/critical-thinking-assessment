from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1.endpoints.sessions import (
    request_report_generation as request_report_generation_endpoint,
)
from app.models.assessment import AssessmentSession
from app.models.base import Base
from app.schemas.session import ReportGenerationResponse
from app.services.session_service import SessionService


class ReportGenerationReliabilityTests(unittest.TestCase):
    def _service(self, *, report_exists: bool):
        db = MagicMock()
        session = SimpleNamespace(
            id=7,
            session_uuid="session-1",
            status="completed",
            interview_state_json={},
            state_version=0,
        )
        db.execute.return_value.scalar_one_or_none.return_value = session
        db.execute.return_value.scalar_one.return_value = session
        db.execute.return_value.rowcount = 1
        service = SessionService(db)
        service.repo = MagicMock()
        service.repo.get_report.return_value = (
            SimpleNamespace(id=8) if report_exists else None
        )
        service.repo.get_session_by_uuid.return_value = session
        return service, db, session

    def test_completed_session_generates_once_after_atomic_claim(self) -> None:
        service, db, session = self._service(report_exists=False)
        service.repo.get_report.side_effect = [None, SimpleNamespace(id=8)]
        with patch.object(service, "_generate_scoring_and_report") as generate:
            self.assertTrue(service.generate_report_if_completed("session-1"))
        generate.assert_called_once_with(session)
        self.assertEqual(db.commit.call_count, 3)
        self.assertEqual(
            session.interview_state_json["report_generation"]["status"],
            "ready",
        )
        self.assertEqual(
            session.interview_state_json["report_generation"]["attempts"],
            1,
        )

    def test_existing_report_is_idempotent(self) -> None:
        service, db, _session = self._service(report_exists=True)
        with patch.object(service, "_generate_scoring_and_report") as generate:
            self.assertFalse(service.generate_report_if_completed("session-1"))
        generate.assert_not_called()
        db.commit.assert_not_called()
        db.rollback.assert_called_once()

    def test_completed_missing_report_can_be_scheduled_for_recovery(self) -> None:
        service, _db, _session = self._service(report_exists=False)

        response = service.request_report_generation("session-1")

        self.assertEqual(response.session_uuid, "session-1")
        self.assertEqual(response.status, "scheduled")
        self.assertEqual(
            _session.interview_state_json["report_generation"]["status"],
            "scheduled",
        )

    def test_repeated_recovery_request_does_not_schedule_again(self) -> None:
        service, db, session = self._service(report_exists=False)

        first = service.request_report_generation("session-1")
        second = service.request_report_generation("session-1")

        self.assertEqual(first.status, "scheduled")
        self.assertEqual(second.status, "running")
        self.assertEqual(
            session.interview_state_json["report_generation"]["attempts"],
            0,
        )
        self.assertEqual(db.commit.call_count, 1)
        db.rollback.assert_called_once()

    def test_expired_recovery_lease_can_be_scheduled_again(self) -> None:
        service, _db, session = self._service(report_exists=False)
        session.interview_state_json = {
            "report_generation": {
                "status": "scheduled",
                "attempts": 1,
                "updated_at": "2000-01-01T00:00:00Z",
            }
        }

        response = service.request_report_generation("session-1")

        self.assertEqual(response.status, "scheduled")
        self.assertEqual(
            session.interview_state_json["report_generation"]["attempts"],
            1,
        )

    def test_failed_generation_is_not_replayed_by_queued_duplicate(self) -> None:
        service, _db, session = self._service(report_exists=False)
        session.interview_state_json = {
            "report_generation": {
                "status": "scheduled",
                "attempts": 0,
            }
        }
        with patch.object(service, "_generate_scoring_and_report") as generate:
            self.assertFalse(service.generate_report_if_completed("session-1"))
            self.assertFalse(service.generate_report_if_completed("session-1"))

        generate.assert_called_once_with(session)
        self.assertEqual(
            session.interview_state_json["report_generation"]["status"],
            "failed",
        )
        self.assertEqual(
            session.interview_state_json["report_generation"]["attempts"],
            1,
        )

    def test_unexpected_failure_can_be_explicitly_scheduled_for_retry(self) -> None:
        service, _db, session = self._service(report_exists=False)
        with patch.object(
            service,
            "_generate_scoring_and_report",
            side_effect=RuntimeError("unexpected failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
                service.generate_report_if_completed("session-1")

        self.assertEqual(
            session.interview_state_json["report_generation"]["status"],
            "failed",
        )
        response = service.request_report_generation("session-1")
        self.assertEqual(response.status, "scheduled")

    def test_sqlite_request_stays_responsive_while_generator_is_running(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "report-lease.db"
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(engine)
            session_factory = sessionmaker(bind=engine)
            with session_factory() as seed_db:
                seed_db.add(
                    AssessmentSession(
                        session_uuid="sqlite-session",
                        participant_id=1,
                        scenario_id=1,
                        selection_mode="fixed",
                        status="completed",
                        assessment_mode="mock",
                        language_mode="standard",
                        flow_version="progressive_v3_3",
                        interview_state_json={},
                        state_version=0,
                    )
                )
                seed_db.commit()

            entered_generation = Event()
            release_generation = Event()
            worker_results: list[bool] = []

            def run_generator() -> None:
                with session_factory() as worker_db:
                    service = SessionService(worker_db)

                    def wait_without_writing(_session: AssessmentSession) -> None:
                        entered_generation.set()
                        release_generation.wait(timeout=3)

                    with patch.object(
                        service,
                        "_generate_scoring_and_report",
                        side_effect=wait_without_writing,
                    ):
                        worker_results.append(
                            service.generate_report_if_completed("sqlite-session")
                        )

            worker = Thread(target=run_generator)
            worker.start()
            self.assertTrue(entered_generation.wait(timeout=2))

            with session_factory() as request_db:
                started = perf_counter()
                response = SessionService(request_db).request_report_generation(
                    "sqlite-session"
                )
                elapsed = perf_counter() - started

            self.assertEqual(response.status, "running")
            self.assertLess(elapsed, 1.0)
            release_generation.set()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(worker_results, [False])
            engine.dispose()

    def test_report_generation_stops_after_three_attempts(self) -> None:
        service, _db, session = self._service(report_exists=False)
        session.interview_state_json = {
            "report_generation": {
                "status": "failed",
                "attempts": 3,
            }
        }

        response = service.request_report_generation("session-1")

        self.assertEqual(response.status, "failed")

    def test_existing_report_is_ready_without_rescheduling(self) -> None:
        service, _db, _session = self._service(report_exists=True)

        response = service.request_report_generation("session-1")

        self.assertEqual(response.status, "ready")

    def test_incomplete_session_rejects_report_generation_request(self) -> None:
        service, _db, session = self._service(report_exists=False)
        session.status = "in_progress"

        with self.assertRaises(HTTPException) as raised:
            service.request_report_generation("session-1")

        self.assertEqual(raised.exception.status_code, 409)

    def test_endpoint_enqueues_only_newly_scheduled_request(self) -> None:
        background_tasks = MagicMock()
        db = MagicMock()
        with patch(
            "app.api.v1.endpoints.sessions.SessionService.request_report_generation",
            return_value=ReportGenerationResponse(
                session_uuid="session-1",
                status="scheduled",
            ),
        ):
            request_report_generation_endpoint("session-1", background_tasks, db)

        background_tasks.add_task.assert_called_once()

        background_tasks.reset_mock()
        with patch(
            "app.api.v1.endpoints.sessions.SessionService.request_report_generation",
            return_value=ReportGenerationResponse(
                session_uuid="session-1",
                status="running",
            ),
        ):
            request_report_generation_endpoint("session-1", background_tasks, db)

        background_tasks.add_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
