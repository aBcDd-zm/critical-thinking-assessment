from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents import FollowupOutput, ResolvedEvidenceItem  # noqa: E402
from app.agents.dialogue_policy import DialoguePolicy  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.participant import Participant  # noqa: E402
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
        created = create_ready_session(service, nickname="语义证据回归")
        session = service._get_session_or_404(created.session_uuid)
        stage = service.repo.get_stage(session.current_stage_id)
        assert stage is not None
        session_id = session.id
        participant_id = session.participant_id

        user_one = _add_turn(
            service,
            session,
            speaker="user",
            content="太赶了，做不完。",
            content_type="scenario_answer",
            analysis={
                "intent": "substantive_answer",
                "relevance": "off_topic",
                "response_category": "redirect",
                "evidence_keys": [],
            },
        )
        _add_turn(
            service,
            session,
            speaker="ai",
            content="追问一",
            content_type="followup_question",
        )
        user_two = _add_turn(
            service,
            session,
            speaker="user",
            content="看看怎么压缩，可以在规定时间完成。",
            content_type="scenario_answer",
            analysis={
                "intent": "substantive_answer",
                "relevance": "off_topic",
                "response_category": "redirect",
                "evidence_keys": [],
            },
        )
        _add_turn(
            service,
            session,
            speaker="ai",
            content="追问二",
            content_type="followup_question",
        )
        latest = _add_turn(
            service,
            session,
            speaker="user",
            content="提高完成的效率。",
            content_type="scenario_answer",
            analysis={
                "intent": "substantive_answer",
                "relevance": "off_topic",
                "response_category": "redirect",
                "evidence_keys": [],
            },
        )

        output = FollowupOutput(
            question="这一部分先到这里，我们继续看下一项。",
            content_type="advance_prompt",
            question_type="advance",
            resolved_response_category="assess_answer",
            category_correction_reason="这些表达都在回答时间约束和工作范围。",
            resolved_evidence=[
                ResolvedEvidenceItem(
                    evidence_key="核心问题",
                    coverage="partial",
                    supporting_turn_indexes=[user_two.turn_index, latest.turn_index],
                    reason="提到了压缩与效率，但决策对象仍不够明确。",
                    confidence=0.82,
                ),
                ResolvedEvidenceItem(
                    evidence_key="约束条件",
                    coverage="covered",
                    supporting_turn_indexes=[user_one.turn_index, user_two.turn_index],
                    reason="明确表达时间紧迫和规定时间限制。",
                    confidence=0.94,
                ),
                ResolvedEvidenceItem(
                    evidence_key="决策边界",
                    coverage="missing",
                    supporting_turn_indexes=[],
                    reason="尚未说明上线时间或范围边界。",
                    confidence=0.9,
                ),
            ],
            reason="semantic regression fixture",
            next_action="advance_stage",
            transition_reason="followup_limit_reached",
            generation_mode="ai_open",
            ai_generation_weight=100,
            confidence=0.9,
        )
        service._apply_model_resolution(session, latest, output)
        decision_context = service._build_agent_context(session, latest)
        decision = DialoguePolicy().decide(decision_context)
        assert decision.next_action == "advance_stage"
        assert decision.transition_reason == "followup_limit_reached"
        service._persist_followup_result(session, latest, output, None)  # type: ignore[arg-type]
        db.commit()

        state = service.get_session(created.session_uuid)
        progress = state.progress.stages[0]
        assert latest.analysis_json["resolved_response_category"] == "assess_answer"
        assert progress.evidence_coverage == {
            "核心问题": "partial",
            "约束条件": "complete",
            "决策边界": "missing",
        }
        assert progress.used_followups == 2
        assert not progress.waiting_for_stage_choice
        assert not progress.can_skip
        assert state.current_stage.stage_order == 2
        assert not state.progress.stages[0].skipped
        assert (
            latest.analysis_json["stage_transition"]["reason"]
            == "followup_limit_reached"
        )

        print("Semantic evidence and stage-limit flow passed.")
        print("semantic_merge=passed, formal_followups=2/2, auto_advance=passed")
        print("transition_reason=followup_limit_reached, transition_audit=passed")
        return 0
    finally:
        if session_id is not None:
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).update(
                {DialogueTurn.source_agent_trace_id: None},
                synchronize_session=False,
            )
            db.query(AgentTrace).filter(AgentTrace.session_id == session_id).delete()
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_id).delete()
            db.query(AssessmentSession).filter(
                AssessmentSession.id == session_id
            ).delete()
        if participant_id is not None:
            db.query(Participant).filter(Participant.id == participant_id).delete()
        db.commit()
        db.close()


def _add_turn(
    service: SessionService,
    session: AssessmentSession,
    *,
    speaker: str,
    content: str,
    content_type: str,
    analysis: dict | None = None,
) -> DialogueTurn:
    turn = DialogueTurn(
        session_id=session.id,
        stage_id=session.current_stage_id,
        turn_index=service.repo.next_turn_index(session.id),
        speaker=speaker,
        content=content,
        content_type=content_type,
        analysis_json=analysis,
    )
    service.db.add(turn)
    service.db.flush()
    return turn


if __name__ == "__main__":
    raise SystemExit(main())
