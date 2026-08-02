from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


PERSONAS: dict[str, dict[str, object]] = {
    "elementary": {
        "nickname": "小学生回归",
        "occupation": "小学生",
        "profile": ("学校小组展示", "同学和老师"),
        "answers": (
            "我会先把没做完的部分做完，别的以后再说。",
            "大家都说来不及，我觉得跟着大家的说法做就行。",
            "我会照原来的安排做，不用管其他人怎么想。",
            "我觉得继续做就行，等做完了再看。",
            "我会先做最容易的，剩下的到时候再说。",
            "这个办法看起来更快，所以我就选它。",
            "我还是照刚才说的做，不想改。",
            "有问题就再看看，现在先继续。",
            "我会让组长决定，大家听他的。",
            "我准备先做，后面怎么安排还没有想好。",
            "我觉得应该没事，就按这个办法继续。",
            "最后大家一起把能做的做完。",
        ),
    },
    "middle": {
        "nickname": "初中生回归",
        "occupation": "初中生",
        "profile": ("学校小组展示", "同学、组长和老师"),
        "answers": (
            "我觉得先要看五天够不够把最重要的部分做完，还有是不是有人分到的任务太多。",
            "我会先看每个人完成了多少，再问没做完的原因，不能只听一句来不及就决定。",
            "我觉得有可能会迟到，但也有7次没有迟到，所以不能马上说这次一定做不完。",
            "如果交接少了确实会更快，但也可能有人不知道前面改了什么，所以要先试一小部分。",
            "我会让想赶进度的同学和担心出错的同学一起说理由，再请老师帮忙确认重要标准。",
            "先把最重要的内容列出来，两个人各负责一部分，每天放学前互相检查一次。",
            "如果检查发现错误变多，就停止这个办法，改回原来的检查方式。",
            "18%比5%多很多，说明这个办法出了问题，我会先停下来，把重要的地方重新检查。",
            "我会找出错误是交接不清还是内容本身有问题，查清后再决定哪些部分还能继续。",
            "同学关心能不能按时完成，老师关心内容是否正确，看展示的人还要能听懂。",
            "我会先完成重点内容，再补次要部分；组长记录进度，老师抽查，出错就暂停。",
            "最后每天看完成情况和错误数，如果问题减少再继续，否则恢复原安排。",
        ),
    },
    "high": {
        "nickname": "高中生回归",
        "occupation": "高中生",
        "profile": ("研究性学习小组项目", "组员、指导老师和展示对象"),
        "answers": (
            "表面问题是五天后要展示，真正要决定的是在时间和质量约束下，哪些关键内容必须保留、哪些可以缩减。",
            "我会核对任务清单、原始修改记录和每个人的完成口径，再抽查关键内容，避免把主观进度当成事实。",
            "过去10次有3次延期只是初步数据；还要确认任务规模和人员配置是否可比，否则样本不能直接支持这次一定延期。",
            "如果减少交接能节省时间，前提是修改记录完整；一旦盲查发现信息断点，这个因果判断就不成立。",
            "赶进度的组员关注按期完成，复核者承担质量风险，老师和展示对象关心准确性；我会优先保护关键内容的可靠性。",
            "第一天界定必做范围并分工，第二至四天制作和交叉复核，第五天只处理关键缺陷；组长每天记录进度和错误。",
            "先在非关键部分小范围试用；若错误率超过8%就暂停并回到逐项检查，低于5%且连续两天稳定才扩大。",
            "返工率从5%升到18%削弱了原先的效率判断，所以我会停止扩大，但保留已验证无误的部分并恢复关键交接。",
            "我会按错误来源分组复查，并让未参与制作的同学盲核；若两轮错误率都回到5%以下，再重新评估。",
            "短期缩减检查有利于进度，但长期可能把返工成本转给复核者和展示对象，因此先保证关键内容并公开取舍。",
            "最终方案是关键内容保持逐项复核、非关键部分条件试用；每项都有负责人、截止时间、检查人和回退路径。",
            "每天记录完成率、错误率和交接遗漏；连续两天达标才继续，任一关键错误出现就回滚并复盘原因。",
        ),
    },
}


def _events(lines: list[str]) -> list[dict]:
    return [json.loads(line) for line in lines if line.strip()]


def _normalize_question(text: str) -> str:
    return re.sub(r"[\s，。！？?、：；“”‘’]", "", text).lower()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="score-differentiation-") as temp_dir:
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(temp_dir) / 'check.db'}"
        os.environ["MODEL_GATEWAY_MODE"] = "mock"
        os.environ["INTERVIEW_FLOW_VERSION"] = "progressive_v3_3"
        root = Path(__file__).resolve().parents[1]
        sys.path.extend([str(root), str(Path(__file__).resolve().parent)])

        from sqlalchemy import select

        from app.core.config import get_settings
        from app.core.database import get_engine, get_sessionmaker
        from app.models import Base
        from app.models.agent import AgentTrace
        from app.models.assessment import AssessmentSession, DialogueTurn
        from app.schemas.session import (
            CreateSessionRequest,
            ProfileTurnRequest,
            SubmitTurnRequest,
        )
        from app.services.session_service import SessionService
        from seed_db import seed_database

        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
        Base.metadata.create_all(get_engine())
        seed_database(root / "seeds")

        results: dict[str, dict[str, object]] = {}
        with get_sessionmaker()() as db:
            for tier, persona in PERSONAS.items():
                service = SessionService(db)
                created = service.create_session(
                    CreateSessionRequest(
                        nickname=str(persona["nickname"]),
                        occupation_category="学生",
                        occupation=str(persona["occupation"]),
                        consent_accepted=True,
                        consent_version="critical_thinking_assessment_consent_v1",
                    )
                )
                for profile_answer in persona["profile"]:
                    list(
                        service.stream_profile_turn(
                            created.session_uuid,
                            ProfileTurnRequest(content=str(profile_answer)),
                        )
                    )
                _events(list(service.stream_start_interview(created.session_uuid)))

                for answer in persona["answers"]:
                    session = db.execute(
                        select(AssessmentSession).where(
                            AssessmentSession.session_uuid == created.session_uuid
                        )
                    ).scalar_one()
                    if session.status == "completed":
                        break
                    _events(
                        list(
                            service.stream_submit_turn(
                                created.session_uuid,
                                SubmitTurnRequest(
                                    client_turn_id=str(uuid4()),
                                    content=str(answer),
                                ),
                            )
                        )
                    )

                session = db.execute(
                    select(AssessmentSession).where(
                        AssessmentSession.session_uuid == created.session_uuid
                    )
                ).scalar_one()
                if session.status != "completed":
                    raise AssertionError(
                        f"{tier} session did not complete: {session.status}"
                    )
                report = service.get_report(created.session_uuid).report
                dimension_reports = report["dimension_reports"]
                scores = {
                    item["dimension_key"]: item["score"]
                    for item in dimension_reports
                }
                if len(scores) != 6 or any(score is None for score in scores.values()):
                    raise AssertionError(
                        f"{tier} incomplete scores: {scores}; "
                        f"slots={session.interview_state_json.get('dimension_slots')}; "
                        f"opportunities={session.interview_state_json.get('dimension_opportunity_counts')}"
                    )
                average = sum(scores.values()) / len(scores)
                quality = report["measurement_quality"]
                if quality["status"] != "valid":
                    raise AssertionError(
                        f"{tier} measurement not valid: {quality['reasons']}"
                    )
                if quality.get("technical_failure_rate") != 0:
                    raise AssertionError(f"{tier} has technical fallback")
                if quality.get("total_fallback_rate") != 0:
                    raise AssertionError(f"{tier} has quality fallback")
                if quality.get("unobserved_dimensions"):
                    raise AssertionError(
                        f"{tier} unobserved: {quality['unobserved_dimensions']}"
                    )
                if quality.get("provisional_dimensions"):
                    raise AssertionError(
                        f"{tier} provisional: {quality['provisional_dimensions']}"
                    )

                turns = list(
                    db.execute(
                        select(DialogueTurn)
                        .where(DialogueTurn.session_id == session.id)
                        .order_by(DialogueTurn.turn_index)
                    ).scalars()
                )
                user_turns = {
                    item.id: item.content for item in turns if item.speaker == "user"
                }
                for item in dimension_reports:
                    for quote in item.get("evidence_quotes") or []:
                        if quote not in user_turns.values():
                            raise AssertionError(
                                f"{tier} ungrounded report quote: {quote}"
                            )

                questions = [
                    item.content
                    for item in turns
                    if item.speaker == "ai"
                    and "?" in item.content.replace("？", "?")
                ]
                normalized = [_normalize_question(item) for item in questions]
                if len(normalized) != len(set(normalized)):
                    raise AssertionError(f"{tier} repeated exact questions: {questions}")

                report_text = json.dumps(report, ensure_ascii=False)
                if tier != "high" and any(
                    term in report_text
                    for term in ("灰度上线", "研发、运营、市场", "上线范围")
                ):
                    raise AssertionError(f"{tier} report contains adult product wording")

                fallback_traces = [
                    {
                        "status": trace.status,
                        "errors": (trace.config_snapshot_json or {}).get(
                            "validation_errors"
                        ),
                        "action": (trace.config_snapshot_json or {}).get("action"),
                        "target": (trace.config_snapshot_json or {}).get(
                            "hidden_target_dimension"
                        ),
                    }
                    for trace in db.execute(
                        select(AgentTrace)
                        .where(
                            AgentTrace.session_id == session.id,
                            AgentTrace.agent_name == "consultative_turn",
                            AgentTrace.status == "fallback",
                        )
                        .order_by(AgentTrace.id)
                    ).scalars()
                ]
                if fallback_traces:
                    raise AssertionError(f"{tier} fallback traces: {fallback_traces}")
                results[tier] = {
                    "session_uuid": created.session_uuid,
                    "scores": scores,
                    "average": average,
                    "quality": quality["status"],
                    "quality_reasons": quality.get("reasons"),
                    "technical_failure_rate": quality.get("technical_failure_rate"),
                    "total_fallback_rate": quality.get("total_fallback_rate"),
                    "esi": quality.get("overall_evidence_sufficiency_index"),
                }

        elementary = float(results["elementary"]["average"])
        middle = float(results["middle"]["average"])
        high = float(results["high"]["average"])
        if not elementary + 0.5 <= middle:
            raise AssertionError(f"elementary/middle separation too small: {results}")
        if not middle + 0.5 <= high:
            raise AssertionError(f"middle/high separation too small: {results}")
        if max(results["elementary"]["scores"].values()) > 2:
            raise AssertionError(f"elementary low anchors not preserved: {results}")
        if not 3.0 <= middle <= 4.2:
            raise AssertionError(f"middle tier not centered: {results}")
        if high < 4.2 or min(results["high"]["scores"].values()) < 4:
            raise AssertionError(f"high tier not consistently strong: {results}")

        print(json.dumps(results, ensure_ascii=False, indent=2))
        print("score differentiation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
