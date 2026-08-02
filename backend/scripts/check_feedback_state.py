from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models.feedback import SessionFeedback  # noqa: E402
from app.schemas.session import SubmitFeedbackRequest  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402


class FakeRepository:
    def __init__(self) -> None:
        self.session = SimpleNamespace(id=1, session_uuid="feedback-check")
        self.feedback: SessionFeedback | None = None

    def get_session_by_uuid(self, session_uuid: str):
        return self.session if session_uuid == self.session.session_uuid else None

    def get_feedback(self, session_id: int):
        assert session_id == self.session.id
        return self.feedback


class FakeDatabase:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository

    def add(self, value) -> None:
        if isinstance(value, SessionFeedback):
            self.repository.feedback = value

    def commit(self) -> None:
        return None

    def refresh(self, value) -> None:
        value.updated_at = datetime(2026, 7, 17, 12, 0, 0)


def main() -> int:
    repository = FakeRepository()
    database = FakeDatabase(repository)
    service = SessionService.__new__(SessionService)
    service.db = database
    service.repo = repository

    empty_state = service.get_feedback("feedback-check")
    assert empty_state.submitted is False
    assert empty_state.feedback is None

    created = service.submit_feedback("feedback-check", _payload(open_feedback="首次反馈"))
    assert created.open_feedback == "首次反馈"
    saved_state = service.get_feedback("feedback-check")
    assert saved_state.submitted is True
    assert saved_state.feedback is not None
    assert saved_state.feedback.open_feedback == "首次反馈"

    updated = service.submit_feedback("feedback-check", _payload(open_feedback="更新反馈"))
    assert updated.open_feedback == "更新反馈"
    assert repository.feedback is not None
    assert repository.feedback.metadata_json["updated_from_frontend"] is True

    try:
        service.get_feedback("missing-session")
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("Missing session should return 404")

    print("Feedback state checks passed.")
    print("empty=200-null, create=passed, update=passed, missing-session=404")
    return 0


def _payload(*, open_feedback: str) -> SubmitFeedbackRequest:
    return SubmitFeedbackRequest(
        realism_score=4,
        difficulty_score=3,
        naturalness_score=4,
        fatigue_score=3,
        report_trust_score=4,
        overall_satisfaction_score=4,
        open_feedback=open_feedback,
    )


if __name__ == "__main__":
    raise SystemExit(main())
