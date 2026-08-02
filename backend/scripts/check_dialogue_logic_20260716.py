from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.participant import Participant  # noqa: E402
from app.schemas.session import SubmitTurnRequest  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402
from session_flow_helpers import create_ready_session  # noqa: E402


def submit(service: SessionService, session_uuid: str, content: str):
    service.submit_turn(
        session_uuid,
        SubmitTurnRequest(content=content, content_type="scenario_answer"),
    )
    return service.get_session(session_uuid)


def main() -> int:
    os.environ["MODEL_GATEWAY_MODE"] = "mock"
    get_settings.cache_clear()
    db = get_sessionmaker()()
    created_session_ids: list[int] = []
    participant_ids: list[int] = []
    try:
        service = SessionService(db)

        created = create_ready_session(service, nickname="逻辑回归A")
        session_row = service._get_session_or_404(created.session_uuid)
        created_session_ids.append(session_row.id)
        participant_ids.append(session_row.participant_id)

        state = submit(service, created.session_uuid, "决策是什么东西")
        assert state.current_stage.stage_order == 1
        assert state.turns[-1].content_type == "term_explanation"
        assert state.progress.stages[0].used_followups == 0
        assert state.language_mode == "plain"

        state = submit(service, created.session_uuid, "延期")
        assert state.current_stage.stage_order == 1
        assert state.progress.stages[0].used_followups == 1
        assert state.turns[-2].analysis["intent"] == "substantive_answer"

        state = submit(service, created.session_uuid, "因为48小时太紧，质量风险还没有排除")
        assert state.current_stage.stage_order == 2

        state = submit(service, created.session_uuid, "延期")
        assert state.current_stage.stage_order == 2
        assert state.turns[-1].content_type == "redirect_response"
        assert state.progress.stages[1].used_followups == 0

        state = submit(service, created.session_uuid, "86条和19条是什么意思")
        assert state.turns[-1].content_type == "term_explanation"
        assert state.progress.stages[1].used_followups == 0

        created_b = create_ready_session(service, nickname="逻辑回归B")
        session_b = service._get_session_or_404(created_b.session_uuid)
        created_session_ids.append(session_b.id)
        participant_ids.append(session_b.participant_id)
        submit(service, created_b.session_uuid, "需要决定是否上线")
        submit(service, created_b.session_uuid, "我还是决定上线")
        state_b = submit(service, created_b.session_uuid, "我的选择仍然是上线")
        progress_b = state_b.progress.stages[0]
        assert state_b.current_stage.stage_order == 2
        assert progress_b.used_followups == 2
        assert not progress_b.waiting_for_stage_choice
        assert not progress_b.can_skip
        transition_turn = next(
            turn
            for turn in state_b.turns
            if turn.content == "我的选择仍然是上线"
        )
        assert (
            transition_turn.analysis["stage_transition"]["reason"]
            == "followup_limit_reached"
        )

        created_c = create_ready_session(service, nickname="逻辑回归C")
        session_c = service._get_session_or_404(created_c.session_uuid)
        created_session_ids.append(session_c.id)
        participant_ids.append(session_c.participant_id)
        submit(service, created_c.session_uuid, "要提高完成效率")
        submit(service, created_c.session_uuid, "继续提高效率")
        state_c = submit(service, created_c.session_uuid, "下一题")
        assert state_c.current_stage.stage_order == 2
        navigation_turn = next(
            turn for turn in state_c.turns if turn.content == "下一题"
        )
        assert (
            navigation_turn.analysis["stage_transition"]["reason"]
            == "user_navigation"
        )
        assert state_c.progress.stages[0].skipped

        print("2026-07-16 dialogue logic regression passed.")
        print(
            "term explanation, plain mode, relevance, bounded stage progression, "
            "typed navigation: passed"
        )
        return 0
    finally:
        for session_id in created_session_ids:
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).update(
                {DialogueTurn.source_agent_trace_id: None}, synchronize_session=False
            )
            db.query(AgentTrace).filter(AgentTrace.session_id == session_id).delete()
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).delete()
            db.query(AssessmentSession).filter(AssessmentSession.id == session_id).delete()
        for participant_id in participant_ids:
            db.query(Participant).filter(Participant.id == participant_id).delete()
        db.commit()
        db.close()


if __name__ == "__main__":
    sys.exit(main())
