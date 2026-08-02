from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402
from app.core.database import get_sessionmaker  # noqa: E402
from app.models.agent import AgentTrace  # noqa: E402
from app.models.assessment import AssessmentSession, DialogueTurn  # noqa: E402
from app.models.participant import Participant  # noqa: E402
from app.models.report import AssessmentReport  # noqa: E402
from app.models.scoring import ScoreEvidence, ScoreResult, ScoreSnapshot  # noqa: E402
from app.schemas.session import SubmitTurnRequest  # noqa: E402
from app.services.session_service import SessionService  # noqa: E402
from session_flow_helpers import create_ready_session  # noqa: E402


STAGE_PLANS: dict[str, dict[str, Any]] = {
    "s1_problem_definition": {
        "main": (
            "我认为当前最核心的决策问题不是简单地上线或延期，而是在 48 小时窗口内，"
            "产品核心链路（任务同步）的风险是否可控，以及如果延期，对市场宣传节奏、"
            "用户预期和团队承诺会产生什么影响。需要界定的边界包括：可接受的核心错误率阈值、"
            "可投入的工程与运营资源、以及各相关方能够承担的最大代价。"
        ),
        "followups": [
            "更准确地说，我需要先回答三个子问题：同步失败的复现率和影响范围有多大？"
            "48 小时内能否完成有效修复或兜底？延期和强行上线分别会损失什么、带来什么风险？",
            "相关方至少包括市场、研发、运营和种子用户。市场关心窗口期，研发关心质量声誉，"
            "运营关心客服承载，种子用户关心功能可用性。决策必须在这几方之间找到可接受的权衡点。",
        ],
    },
    "s2_evidence_verification": {
        "main": (
            "我会优先把 86 条内测反馈按严重程度、复现率、用户类型和业务影响分层；"
            "特别把那 19 条同步失败反馈与日志、客服记录、灰度数据做交叉验证。"
            "同时我会检查样本偏差：内测用户是否覆盖低端设备、弱网环境，以及反馈是否集中在核心链路。"
        ),
        "followups": [
            "我认为现有证据还不够充分。需要补充的数据包括：失败机型分布、弱网 vs 稳定网络复现率、"
            "客服反馈中与同步失败相关的投诉量，以及核心链路在灰度环境中的表现。",
            "单一指标（比如反馈数量或失败率）不足以支撑决策。如果只看数量，可能低估样本偏差；"
            "如果只看失败率，可能忽略业务影响范围。必须多维交叉验证。",
        ],
    },
    "s3_stakeholder_perspectives": {
        "main": (
            "我会把市场、研发、运营和种子用户的诉求都纳入决策。市场希望抓住窗口，"
            "研发希望保护质量声誉，运营担心客服承载，种子用户希望尽快使用协作功能。"
            "这些视角之间存在冲突：按时上线可能牺牲质量和客服体验，全面延期可能损失市场机会和用户信任。"
        ),
        "followups": [
            "从用户角度看，少量同步延迟可能可以接受，但核心功能不可用会严重损害信任。"
            "从运营角度看，如果咨询量激增而人手不足，会放大用户负面体验。因此不能只看单一方的诉求。",
            "我的取舍原则是：优先保证核心链路可用，再通过灰度控制影响范围，"
            "同时让市场保留小范围发布的抓手，运营提前准备 FAQ 和人工响应。",
        ],
    },
    "s4_reasoning_decision": {
        "main": (
            "我的初步方案是：如果核心链路故障率低于可接受阈值且支持团队能在线响应，"
            "就按灰度方式上线；否则延期。这个判断建立在两个前提上："
            "一是 48 小时内能拿到可信的核心链路验证结论，二是灰度入口和回滚开关已经就绪。"
        ),
        "followups": [
            "关键假设是核心故障率在可控范围内。如果这个假设被推翻，方案就要从灰度上线调整为限制范围上线或延期。"
            "另一个假设是支持团队能在上线当天提供有效响应。",
            "我会用‘依据—推理—结论’来组织判断：依据是内测反馈、日志和灰度数据；"
            "推理是比较不同方案的风险与收益；结论是在满足前提时选择灰度上线。",
        ],
    },
    "s5_dynamic_adjustment": {
        "main": (
            "如果新增信息显示核心协作链路在弱网环境下复现率明显升高，我会把原方案从全面上线改为灰度上线，"
            "并设定明确的监控指标：核心错误率、用户投诉率、客服咨询量。"
            "如果任一指标超过阈值，立即暂停并回滚。"
        ),
        "followups": [
            "调整的边界是：核心链路风险没有扩大到无法接受的程度。"
            "如果灰度用户中也出现大量同步失败，就暂停发布并转入修复。",
            "后续监控包括实时错误率、用户反馈、客服压力和灰度覆盖率。"
            "触发条件是核心错误率超过阈值或投诉率明显上升。",
        ],
    },
    "s6_integrated_plan": {
        "main": (
            "最终方案：分阶段灰度上线。第一阶段只向低风险用户群开放，保留回滚开关；"
            "研发在 24 小时内验证弱网和低端设备问题，运营准备 FAQ 和人工响应，市场改为小范围发布。"
            "判断依据是同步失败集中在核心链路，直接全量上线风险太高，但完全延期也会损失窗口。"
        ),
        "followups": [
            "责任分工：研发负责核心链路验证和回滚开关，运营负责 FAQ 和值班，市场负责小范围发布口径，"
            "我负责监控指标和决策推进。",
            "兜底安排：如果核心错误率超过阈值、投诉率激增或修复无法在 24 小时内完成，"
            "立即停止灰度并启动回滚，同时向用户和市场同步说明。",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a complete assessment flow against the real LLM and print the report."
    )
    parser.add_argument("--nickname", default="real-flow-test", help="Participant nickname.")
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep the session and report in the database after the run.",
    )
    parser.add_argument(
        "--max-followups",
        type=int,
        default=2,
        help="Maximum followup answers to submit per stage before forcing advancement.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path(__file__).with_name("last_real_flow_log.json"),
        help="Path to write the full JSON log.",
    )
    return parser.parse_args()


def _latest_ai_turn(turns: list[Any]) -> Any | None:
    return next((t for t in reversed(turns) if t.speaker == "ai"), None)


def _latest_user_turn(turns: list[Any]) -> Any | None:
    return next((t for t in reversed(turns) if t.speaker == "user"), None)


def _force_advance(service: SessionService, session_uuid: str) -> bool:
    """Force stage advancement; return True if a new stage was entered."""
    session_row = service._get_session_or_404(session_uuid)
    turns = service.repo.list_turns(session_row.id)
    user_turn = _latest_user_turn(turns)
    if user_turn is None:
        return False
    next_stage = service._advance_to_next_stage(session_row, user_turn)
    service.db.commit()
    return next_stage is not None


def _clean_session(db: Any, session_row: AssessmentSession, participant_id: int) -> None:
    """Delete all session-related rows in an order that respects foreign keys."""
    # Scoring and report data reference agent_trace / dialogue_turn, so delete them first.
    db.query(ScoreEvidence).filter(
        ScoreEvidence.dialogue_turn_id.in_(
            db.query(DialogueTurn.id).filter(DialogueTurn.session_id == session_row.id)
        )
    ).delete(synchronize_session=False)
    db.query(ScoreResult).filter(
        ScoreResult.snapshot_id.in_(
            db.query(ScoreSnapshot.id).filter(ScoreSnapshot.session_id == session_row.id)
        )
    ).delete(synchronize_session=False)
    db.query(ScoreSnapshot).filter(ScoreSnapshot.session_id == session_row.id).delete(
        synchronize_session=False
    )
    db.query(AssessmentReport).filter(AssessmentReport.session_id == session_row.id).delete(
        synchronize_session=False
    )

    db.query(DialogueTurn).filter(DialogueTurn.session_id == session_row.id).update(
        {DialogueTurn.source_agent_trace_id: None},
        synchronize_session=False,
    )
    db.query(AgentTrace).filter(AgentTrace.session_id == session_row.id).delete(
        synchronize_session=False
    )
    db.query(DialogueTurn).filter(DialogueTurn.session_id == session_row.id).delete(
        synchronize_session=False
    )
    db.query(AssessmentSession).filter(AssessmentSession.id == session_row.id).delete(
        synchronize_session=False
    )
    db.query(Participant).filter(Participant.id == participant_id).delete(
        synchronize_session=False
    )
    db.commit()


def run_flow(
    nickname: str, max_followups: int, keep_data: bool, log_file: Path
) -> dict[str, Any]:
    os.environ.setdefault("MODEL_GATEWAY_MODE", "real")
    get_settings.cache_clear()
    settings = get_settings()
    if settings.MODEL_GATEWAY_MODE.lower() != "real":
        raise RuntimeError("MODEL_GATEWAY_MODE must be set to 'real' for this script.")
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is required for the real-model flow.")

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    created_session_uuid: str | None = None
    participant_id: int | None = None
    flow_log: dict[str, Any] = {"stages": []}

    try:
        service = SessionService(db)
        session = create_ready_session(
            service,
            nickname=nickname,
            occupation_category="商业/金融/管理",
            occupation="项目管理人员",
            info_collect_method="ai_dialogue",
            assessment_mode="real",
            preserve_preparation_records=True,
            use_seeded_scenario=False,
        )
        created_session_uuid = session.session_uuid
        session_row = service._get_session_or_404(created_session_uuid)
        participant_id = session_row.participant_id

        print(f"[session] uuid={created_session_uuid}")
        print(f"[session] participant={session.participant_nickname}")
        print(f"[session] scenario={session.scenario.title}")
        print(f"[session] first_ai_message={session.turns[0].content if session.turns else ''}")
        print("-" * 72)

        flow_log["session_uuid"] = created_session_uuid
        flow_log["participant"] = session.participant_nickname
        flow_log["scenario"] = session.scenario.title

        visited_stages: set[str] = set()
        total_iterations = 0
        max_iterations = 30
        finished = False

        while total_iterations < max_iterations and not finished:
            state = service.get_session(created_session_uuid)
            current_stage = state.current_stage
            if current_stage is None:
                print("[flow] no current stage, finishing")
                break

            stage_code = current_stage.stage_code
            if stage_code in visited_stages:
                print(f"[flow] stage {stage_code} already visited; forcing advancement")
                if not _force_advance(service, created_session_uuid):
                    print("[flow] cannot advance further, finishing")
                    break
                continue

            plan = STAGE_PLANS.get(stage_code)
            if plan is None:
                print(f"[flow] unknown stage {stage_code}; forcing advancement")
                if not _force_advance(service, created_session_uuid):
                    break
                continue

            visited_stages.add(stage_code)
            stage_entry: dict[str, Any] = {
                "stage_code": stage_code,
                "title": current_stage.title,
                "main_question": current_stage.main_question,
                "turns": [],
            }
            print(f"\n[stage {stage_code}] {current_stage.title}")
            print(f"[main question] {current_stage.main_question}")

            resp = service.submit_turn(
                created_session_uuid,
                SubmitTurnRequest(content=plan["main"], content_type="scenario_answer"),
            )
            stage_entry["turns"].append({"speaker": "user", "type": "main", "content": plan["main"]})
            print(f"[answer] (submitted main answer, next_action={resp.next_action})")

            followups_used = 0
            while True:
                state = service.get_session(created_session_uuid)
                ai_turn = _latest_ai_turn(state.turns)
                if ai_turn:
                    print(f"[ai] {ai_turn.content_type}: {ai_turn.content[:280]}")
                    stage_entry["turns"].append(
                        {"speaker": "ai", "type": ai_turn.content_type, "content": ai_turn.content}
                    )

                new_stage = state.current_stage.stage_code if state.current_stage else None
                if new_stage != stage_code:
                    print(f"[flow] advanced to {new_stage}")
                    break

                if resp.next_action in {"finish_ready", "generate_report"}:
                    print(f"[flow] agent signaled {resp.next_action}; finishing")
                    finished = True
                    break

                if resp.next_action != "ask_followup" or followups_used >= max_followups:
                    print("[flow] no advancement and no followups left; forcing advancement")
                    if not _force_advance(service, created_session_uuid):
                        print("[flow] cannot force advance, finishing")
                        finished = True
                    break

                answer = plan["followups"][followups_used]
                followups_used += 1
                resp = service.submit_turn(
                    created_session_uuid,
                    SubmitTurnRequest(content=answer, content_type="scenario_answer"),
                )
                stage_entry["turns"].append(
                    {"speaker": "user", "type": "followup", "content": answer}
                )
                print(f"[followup answer {followups_used}] (next_action={resp.next_action})")

            flow_log["stages"].append(stage_entry)
            total_iterations += 1

        print("\n" + "=" * 72)
        print("[finish] completing session")
        finish_resp = service.finish_session(created_session_uuid)
        print(f"[finish] status={finish_resp.status} completed_at={finish_resp.completed_at}")

        report = service.get_report(created_session_uuid)
        report_json = report.report
        flow_log["report"] = report_json

        result = {
            "session_uuid": created_session_uuid,
            "participant_id": participant_id,
            "status": finish_resp.status,
            "report": report_json,
            "cleanup": "skipped" if keep_data else "pending",
        }

        # Print concise report summary.
        print("\n[report summary]")
        print(f"overall_level={report_json.get('overall_level')}")
        print(f"fallback_used={report_json.get('fallback_used')}")
        print(f"warnings={report_json.get('warnings')}")
        for dr in report_json.get("dimension_reports", []):
            print(
                f"  {dr.get('dimension_key')}: score={dr.get('score')} "
                f"level={dr.get('level_label')} suggestion={dr.get('suggestion', '')[:60]}..."
            )

        # Print agent trace summary.
        traces = (
            db.query(AgentTrace)
            .filter(AgentTrace.session_id == session_row.id)
            .order_by(AgentTrace.id)
            .all()
        )
        print("\n[agent traces]")
        trace_summary: list[dict[str, Any]] = []
        for trace in traces:
            output_json = trace.output_json or {}
            summary = {
                "agent_name": trace.agent_name,
                "status": trace.status,
                "model": trace.model_name,
                "fallback_used": output_json.get("fallback_used", False),
                "warning_count": len(output_json.get("warnings", [])),
            }
            trace_summary.append(summary)
            print(
                f"  {summary['agent_name']}: status={summary['status']} "
                f"model={summary['model'] or 'n/a'} "
                f"fallback={summary['fallback_used']} "
                f"warnings={summary['warning_count']}"
            )
        flow_log["agent_traces"] = trace_summary

        if not keep_data:
            _clean_session(db, session_row, participant_id)
            result["cleanup"] = "done"
            print("\n[cleanup] removed session, participant, turns, traces, scoring and report data")

        log_file.write_text(json.dumps(flow_log, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[log] full flow written to {log_file}")

        return result

    except SQLAlchemyError as exc:
        print(f"[error] database issue: {exc}")
        raise
    finally:
        db.close()


def main() -> int:
    args = parse_args()
    run_flow(args.nickname, args.max_followups, args.keep_data, args.log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
