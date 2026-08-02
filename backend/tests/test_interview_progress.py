from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from app.models.assessment import AssessmentSession
from app.services.session_service import SessionService


class InterviewProgressTests(unittest.TestCase):
    @staticmethod
    def _build_progress(
        count: int,
        *,
        status: str = "in_progress",
        started_at: datetime | None = None,
        total_duration_seconds: int | None = None,
    ):
        return SessionService._build_interview_progress(
            AssessmentSession(
                status=status,
                started_at=started_at,
                total_duration_seconds=total_duration_seconds,
                interview_state_json={"formal_user_turn_count": count},
            )
        )

    def test_remaining_minutes_before_minimum_answer_count(self) -> None:
        cases = {
            0: 18,
            8: 2,
        }
        for count, expected_minutes in cases.items():
            with self.subTest(count=count):
                progress = self._build_progress(count)
                self.assertEqual(
                    progress.estimated_remaining_minutes,
                    expected_minutes,
                )

    def test_remaining_minutes_during_concluding_turns(self) -> None:
        cases = {
            9: 2,
            11: 2,
        }
        for count, expected_minutes in cases.items():
            with self.subTest(count=count):
                progress = self._build_progress(count)
                self.assertEqual(
                    progress.estimated_remaining_minutes,
                    expected_minutes,
                )

    def test_remaining_minutes_are_zero_at_maximum_answer_count(self) -> None:
        progress = self._build_progress(12)

        self.assertEqual(progress.estimated_remaining_minutes, 0)
        self.assertEqual(progress.percent, 99)

    def test_completed_progress_has_no_remaining_time_and_keeps_duration(self) -> None:
        progress = self._build_progress(
            9,
            status="completed",
            started_at=datetime.utcnow() - timedelta(seconds=999),
            total_duration_seconds=75,
        )

        self.assertEqual(progress.estimated_remaining_minutes, 0)
        self.assertEqual(progress.percent, 100)
        self.assertEqual(progress.elapsed_seconds, 75)

    def test_active_progress_keeps_live_elapsed_time(self) -> None:
        progress = self._build_progress(
            0,
            started_at=datetime.utcnow() - timedelta(seconds=5),
            total_duration_seconds=99,
        )

        self.assertGreaterEqual(progress.elapsed_seconds, 4)
        self.assertLess(progress.elapsed_seconds, 10)


if __name__ == "__main__":
    unittest.main()
