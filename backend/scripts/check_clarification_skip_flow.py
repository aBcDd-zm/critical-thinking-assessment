from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.main import app  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.participant import Participant  # noqa: E402
from app.schemas.session import SubmitTurnRequest  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402
from session_flow_helpers import create_ready_session  # noqa: E402


def main() -> int:
    os.environ["MODEL_GATEWAY_MODE"] = "mock"
    get_settings.cache_clear()
    db = get_sessionmaker()()
    session_id: int | None = None
    participant_id: int | None = None
    try:
        service = SessionService(db)
        created = create_ready_session(service, nickname="澄清流程验收")
        session = service._get_session_or_404(created.session_uuid)
        session_id = session.id
        participant_id = session.participant_id

        service.submit_turn(
            created.session_uuid,
            SubmitTurnRequest(content="你好", content_type="scenario_answer"),
        )
        after_greeting = service.get_session(created.session_uuid)
        stage_one = after_greeting.progress.stages[0]
        assert stage_one.used_followups == 0
        assert stage_one.used_clarifications == 0

        service.submit_turn(
            created.session_uuid,
            SubmitTurnRequest(content="什么问题啊", content_type="scenario_answer"),
        )
        after_first_clarification = service.get_session(created.session_uuid)
        stage_one = after_first_clarification.progress.stages[0]
        assert after_first_clarification.current_stage.stage_order == 1
        assert stage_one.used_followups == 0
        assert stage_one.used_clarifications == 1
        assert not stage_one.can_skip

        service.submit_turn(
            created.session_uuid,
            SubmitTurnRequest(
                content="现在到底有什么信息什么问题啊",
                content_type="scenario_answer",
            ),
        )
        before_skip = service.get_session(created.session_uuid)
        stage_one = before_skip.progress.stages[0]
        assert before_skip.current_stage.stage_order == 1
        assert stage_one.used_followups == 0
        assert stage_one.used_clarifications == 2
        assert stage_one.released_dynamic_info_count == 0
        assert stage_one.can_skip

        skip_response = TestClient(app).post(
            f"/api/v1/sessions/{created.session_uuid}/stages/current/skip"
        )
        assert skip_response.status_code == 200, skip_response.text
        assert skip_response.json()["next_action"] == "wait_user_answer"
        db.rollback()
        db.expire_all()
        after_skip = service.get_session(created.session_uuid)
        assert after_skip.current_stage.stage_order == 2
        assert after_skip.progress.stages[0].skipped
        assert after_skip.progress.stages[0].status == "skipped"
        assert any(turn.content_type == "stage_skipped" for turn in after_skip.turns)
        stage_two_opening = after_skip.turns[-1]
        assert stage_two_opening.content_type == "stage_question"
        assert "【当前已知信息】" in stage_two_opening.content
        assert "【当前问题】" in stage_two_opening.content

        print("Clarification and skip flow passed.")
        print(f"session_uuid={created.session_uuid}")
        print("formal_followups=0, clarifications=2, dynamic_infos=0, next_stage=2")
        return 0
    finally:
        if session_id is not None:
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).update(
                {DialogueTurn.source_agent_trace_id: None},
                synchronize_session=False,
            )
            db.query(AgentTrace).filter(AgentTrace.session_id == session_id).delete()
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).delete()
            db.query(AssessmentSession).filter(AssessmentSession.id == session_id).delete()
        if participant_id is not None:
            db.query(Participant).filter(Participant.id == participant_id).delete()
        db.commit()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
