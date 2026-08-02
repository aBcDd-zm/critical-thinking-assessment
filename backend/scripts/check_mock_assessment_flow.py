from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault("INTERVIEW_FLOW_VERSION", "legacy_v2")

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from app.agents.schemas import FollowupOutput, HostOutput  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.participant import Participant  # noqa: E402
from app.schemas.session import SubmitTurnRequest  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402
from session_flow_helpers import create_ready_session  # noqa: E402
from check_agent_fixture_cases import (  # noqa: E402
    build_context_from_dialogue_case,
    load_fixtures,
    load_seed_data,
    validate_dialogue_cases,
    validate_scoring_cases,
    validate_users,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a backend mock assessment flow without frontend.")
    parser.add_argument(
        "--case-id",
        default="student_medium_s2",
        help="dialogue_cases.json 中的 case_id",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="保留本次写入数据库的模拟 session，默认会清理。",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="只做 schema / Agent 模块检查，不连接数据库。",
    )
    return parser.parse_args()


def try_import_dialogue_agents() -> tuple[Any | None, Any | None]:
    try:
        from app.agents.followup_agent import FollowupAgent
        from app.agents.host_agent import HostAgent
    except ImportError:
        return None, None
    return HostAgent, FollowupAgent


def run_agent_or_schema_flow(context: Any) -> tuple[HostOutput, FollowupOutput, str]:
    HostAgent, FollowupAgent = try_import_dialogue_agents()
    if HostAgent is None or FollowupAgent is None:
        host_output = HostOutput(
            stage_code=context.stage.stage_code,
            message=context.stage.main_question,
            reason="Host/Followup 模块尚未合入 master，使用 schema-only baseline。",
            fallback_used=True,
            warnings=["dialogue agent modules not available"],
        )
        fallback_rule = context.candidate_intervention_rules[0] if context.candidate_intervention_rules else None
        followup_output = FollowupOutput(
            question=(
                fallback_rule.fallback_question
                if fallback_rule and fallback_rule.fallback_question
                else "你能进一步说明你的判断依据吗？"
            ),
            question_type=fallback_rule.rule_type if fallback_rule else "clarify",
            selected_rule_code=fallback_rule.rule_code if fallback_rule else None,
            generation_mode="schema_only",
            ai_generation_weight=0,
            reason="Host/Followup 模块尚未合入 master，使用固定兜底追问。",
            fallback_used=True,
            warnings=["dialogue agent modules not available"],
        )
        return host_output, followup_output, "schema-only"

    host_output = HostAgent().generate(context)
    followup_output = FollowupAgent().generate(context)
    HostOutput.model_validate(host_output.model_dump())
    FollowupOutput.model_validate(followup_output.model_dump())
    return host_output, followup_output, "agent"


def run_db_flow(nickname: str, user_answers: list[str], keep_data: bool) -> dict[str, Any]:
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    created_session_uuid: str | None = None
    participant_id: int | None = None
    try:
        service = SessionService(db)
        created = create_ready_session(
            service,
            nickname=nickname,
            info_collect_method="fixture",
            assessment_mode="mock",
            use_seeded_scenario=False,
        )
        created_session_uuid = created.session_uuid
        session_row = service._get_session_or_404(created_session_uuid)
        participant_id = session_row.participant_id
        for answer in user_answers:
            service.submit_turn(
                created_session_uuid,
                SubmitTurnRequest(content=answer, content_type="scenario_answer"),
            )
        refreshed = service.get_session(created_session_uuid)
        trace_count = db.query(AgentTrace).filter(
            AgentTrace.session_id == session_row.id
        ).count()
        if len(refreshed.turns) < 3:
            raise AssertionError(
                f"Expected at least 3 turns after submit, got {len(refreshed.turns)}"
            )
        if trace_count < 1:
            raise AssertionError("Expected at least one AgentTrace after submit.")
        result = {
            "session_uuid": created_session_uuid,
            "turn_count": len(refreshed.turns),
            "trace_count": trace_count,
            "status": refreshed.status,
        }
        if not keep_data:
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_row.id).update(
                {DialogueTurn.source_agent_trace_id: None},
                synchronize_session=False,
            )
            db.query(AgentTrace).filter(AgentTrace.session_id == session_row.id).delete()
            db.query(DialogueTurn).filter(DialogueTurn.session_id == session_row.id).delete()
            db.query(AssessmentSession).filter(AssessmentSession.id == session_row.id).delete()
            db.query(Participant).filter(Participant.id == participant_id).delete()
            db.commit()
            result["cleanup"] = "done"
        else:
            result["cleanup"] = "skipped"
        return result
    finally:
        db.close()


def main() -> int:
    args = parse_args()
    os.environ.setdefault("MODEL_GATEWAY_MODE", "mock")
    get_settings.cache_clear()

    users, dialogue_cases, scoring_cases = load_fixtures()
    scenario_seed, rubric_seed = load_seed_data()
    users_by_id = validate_users(users)
    dialogue_cases_by_id = validate_dialogue_cases(
        dialogue_cases,
        users_by_id,
        scenario_seed,
        rubric_seed,
    )
    validate_scoring_cases(scoring_cases, users_by_id, dialogue_cases_by_id, rubric_seed)
    if args.case_id not in dialogue_cases_by_id:
        raise AssertionError(f"Unknown case_id: {args.case_id}")

    dialogue_case = dialogue_cases_by_id[args.case_id]
    user = users_by_id[dialogue_case["user_id"]]
    context = build_context_from_dialogue_case(
        dialogue_case,
        users_by_id,
        scenario_seed,
        rubric_seed,
    )

    print("=" * 72)
    print("Backend mock assessment flow")
    print("=" * 72)
    print(f"case_id={args.case_id}")
    print(f"user={user['nickname']} / {user['expected_level']}")
    print(f"stage={context.stage.stage_code}")

    if not args.skip_db:
        try:
            db_result = run_db_flow(
                nickname=user["nickname"],
                user_answers=[dialogue_case["latest_user_answer"]],
                keep_data=args.keep_data,
            )
            print("[DB] session flow passed:")
            print(f"  session_uuid={db_result['session_uuid']}")
            print(f"  turn_count={db_result['turn_count']}")
            print(f"  trace_count={db_result['trace_count']}")
            print(f"  cleanup={db_result['cleanup']}")
        except (SQLAlchemyError, Exception) as exc:
            print("[DB] skipped:")
            print(f"  reason={type(exc).__name__}: {exc}")
            print("  hint=请确认 MySQL 已启动、已执行迁移和 seed；schema/Agent 检查会继续运行。")

    host_output, followup_output, mode = run_agent_or_schema_flow(context)
    print("[Agent] flow passed:")
    print(f"  mode={mode}")
    print(f"  host_next_action={host_output.next_action}")
    print(f"  host_fallback_used={host_output.fallback_used}")
    print(f"  followup_next_action={followup_output.next_action}")
    print(f"  followup_rule={followup_output.selected_rule_code}")
    print(f"  followup_fallback_used={followup_output.fallback_used}")
    print(f"  followup_question={followup_output.question}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
