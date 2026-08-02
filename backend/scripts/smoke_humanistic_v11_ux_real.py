from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.agents.interview_blueprint import build_blueprint_from_generated
from app.agents.progressive_schemas import InterviewPlanOutput, PlannerBudget
from app.agents.runtime_interviewer_agent import (
    HUMANISTIC_INTERVIEWER_STYLE,
    HUMANISTIC_INTERVIEWER_STYLE_V1_1,
    InterviewerAgent,
)
from app.agents.schemas import (
    AgentRuntimeContext,
    DialogueTurnContext,
    ParticipantContext,
    ScenarioContext,
    SessionContext,
    StageContext,
)
from app.core.config import get_settings
from app.schemas.model_gateway import ChatMessage, ModelChatRequest
from app.services.model_gateway_service import ModelGatewayService
from app.services.occupation_skeleton_service import OccupationSkeletonService


CONFIRMATION = "RUN_SYNTHETIC_HUMANISTIC_V11_UX_SMOKE"
MAX_CALLS = 24


@dataclass(frozen=True)
class SyntheticCase:
    case_id: str
    answer: str
    delivery_mode: str
    target_dimension: str
    target_evidence: str
    active_topic: str
    question_intent: str


CASES = (
    SyntheticCase(
        "UX-S01",
        "我会先核实当前完成度和质量记录，再决定是否调整分工。",
        "reflective_probe",
        "evidence_evaluation",
        "说明一项需要核实的信息",
        "信息核实",
        "询问用户会先核实哪一类信息",
    ),
    SyntheticCase(
        "UX-S02",
        "一方面我想按时完成，另一方面也担心仓促调整会增加返工。",
        "reflective_probe",
        "multiple_perspectives",
        "说明两个参与方的不同关注",
        "不同关注",
        "询问用户会怎样兼顾进度和返工风险",
    ),
    SyntheticCase(
        "UX-S03",
        "我会先小范围试用，同时保留原安排作为回退方案。",
        "summary_check",
        "dynamic_adjustment",
        "说明调整和回退条件",
        "调整条件",
        "核对用户准备在什么条件下回退",
    ),
    SyntheticCase(
        "UX-S04",
        "项目负责人关心进度，复核同学更担心质量，我需要先了解双方底线。",
        "perspective_shift",
        "multiple_perspectives",
        "说明相关方的关注和协调依据",
        "相关方关注",
        "询问用户会依据什么协调双方诉求",
    ),
    SyntheticCase(
        "UX-S05",
        "我还不确定记录是否完整，所以现在不想立刻下结论。",
        "reflective_probe",
        "reasoning_argumentation",
        "说明暂缓结论的依据",
        "暂缓判断",
        "询问用户需要看到什么信息才会形成判断",
    ),
    SyntheticCase(
        "UX-S06",
        "我希望先听建议，但最后会根据现场限制自己决定。",
        "reflective_probe",
        "integrative_decision",
        "说明自主决定的下一步安排",
        "自主决定",
        "询问用户准备先采取哪一个行动",
    ),
)


def _blueprint():
    generated = OccupationSkeletonService._build_generated(  # noqa: SLF001
        "合成协作任务",
        "参与者",
        concrete_v33=True,
    )
    return build_blueprint_from_generated(
        generated,
        occupation_category="学生",
        occupation="大学生",
        user_role="参与者",
        task_domain="合成协作任务",
        skeleton_v3_3=True,
        **OccupationSkeletonService._arrangements("合成协作任务"),  # noqa: SLF001
    )


def _context(case: SyntheticCase) -> AgentRuntimeContext:
    latest = DialogueTurnContext(
        turn_id=91,
        turn_index=2,
        stage_id=11,
        stage_code="s1_problem_definition",
        speaker="user",
        content=case.answer,
        content_type="interview_answer",
    )
    return AgentRuntimeContext(
        session=SessionContext(
            session_id=7,
            session_uuid=str(uuid4()),
            assessment_mode="synthetic",
            status="in_progress",
        ),
        participant=ParticipantContext(
            participant_id=3,
            nickname="合成测试参与者",
        ),
        scenario=ScenarioContext(
            scenario_id=10,
            scenario_code=case.case_id,
            title="合成协作任务",
            background="五天后需要完成一项合成协作任务。",
        ),
        stage=StageContext(
            stage_id=11,
            stage_code="s1_problem_definition",
            stage_order=1,
            title="界定问题",
            stage_goal="观察问题界定",
            context="当前完成度和质量尚未核实。",
            main_question="你会先确认什么？",
            max_followups=2,
        ),
        dialogue_history=[latest],
        latest_user_turn=latest,
    )


def _plan(case: SyntheticCase) -> InterviewPlanOutput:
    return InterviewPlanOutput(
        response_intent="assess_answer",
        action="PROBE",
        active_topic=case.active_topic,
        target_dimension=case.target_dimension,
        target_evidence=case.target_evidence,
        delivery_mode=case.delivery_mode,
        question_intent=case.question_intent,
        reflection_basis_turn_ids=[91],
        reason="固定合成计划用于 v1/v1.1 体验诊断",
        budget=PlannerBudget(
            used_turns=1,
            remaining_turns=9,
            reserved_update_turns=2,
            reserved_closure_turns=1,
        ),
    )


def _opaque_order(case_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(f"humanistic-v11-ux:{case_id}".encode()).digest()
    return (
        (HUMANISTIC_INTERVIEWER_STYLE, HUMANISTIC_INTERVIEWER_STYLE_V1_1)
        if digest[0] % 2 == 0
        else (HUMANISTIC_INTERVIEWER_STYLE_V1_1, HUMANISTIC_INTERVIEWER_STYLE)
    )


async def _judge(
    gateway: ModelGatewayService,
    *,
    case: SyntheticCase,
    candidate_a: str,
    candidate_b: str,
) -> tuple[dict, str]:
    prompt = {
        "task": "匿名比较两条审辩式思维测评访谈追问；不能推测其来源。",
        "synthetic_user_answer": case.answer,
        "candidate_A": candidate_a,
        "candidate_B": candidate_b,
        "criteria": {
            "grounding": "承接必须能在用户原话中找到依据，不推断隐藏心理。",
            "interaction": "语气自然、有人际连续感，但不得冒充心理咨询师。",
            "focus": "只提出一个清楚且与计划目标一致的问题。",
            "autonomy": "不评判、不诱导、不替用户决定。",
            "fluency": "中文简洁顺畅，不机械堆砌套话。",
        },
        "output": {
            "winner": "A、B或TIE",
            "scores": {
                "A": "1到5整数",
                "B": "1到5整数",
            },
            "reason_codes": "最多3个短标签",
        },
    }
    response = await gateway.chat(
        ModelChatRequest(
            messages=[
                ChatMessage(
                    role="system",
                    content=("你是独立的中文访谈体验审阅者。只按给定标准比较，" "忽略版本、模型或系统来源，只输出严格 JSON。"),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(prompt, ensure_ascii=False),
                ),
            ],
            temperature=0,
            max_tokens=220,
            json_mode=True,
            thinking_enabled=False,
            reasoning_effort="low",
            timeout_seconds=15,
        )
    )
    return json.loads(response.content), response.model


def _secure_output_dir(path: Path) -> None:
    if not path.is_absolute():
        raise ValueError("output-dir must be absolute")
    repo_root = Path(__file__).resolve().parents[2]
    if path == repo_root or repo_root in path.parents:
        raise ValueError("output-dir must be outside the repository")
    path.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(path, 0o700)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--v11-only", action="store_true")
    args = parser.parse_args()
    if args.confirmation != CONFIRMATION:
        raise SystemExit("confirmation mismatch; no model call made")

    settings = get_settings()
    if settings.MODEL_GATEWAY_MODE.lower() != "real":
        raise SystemExit("MODEL_GATEWAY_MODE must be real")
    if not settings.DEEPSEEK_API_KEY:
        raise SystemExit("DeepSeek API key is not configured")
    _secure_output_dir(args.output_dir)

    blueprint = _blueprint()
    renderer = InterviewerAgent()
    gateway = ModelGatewayService(settings)
    rows: list[dict] = []
    model_calls = 0

    for case in CASES:
        outputs: dict[str, dict] = {}
        context = _context(case)
        plan = _plan(case)
        styles = (
            (HUMANISTIC_INTERVIEWER_STYLE_V1_1,)
            if args.v11_only
            else (
                HUMANISTIC_INTERVIEWER_STYLE,
                HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            )
        )
        for style in styles:
            renderer_input = renderer.runtime_renderer_input_payload(
                context,
                blueprint,
                plan,
                style_version=style,
            )
            polish_required = (
                style == HUMANISTIC_INTERVIEWER_STYLE_V1_1
                and renderer.v11_requires_model_polish(
                    renderer_input,
                    mode="adaptive",
                )
            )
            started = time.perf_counter()
            result = renderer.render(
                context,
                blueprint,
                plan,
                previous_questions=[],
                style_version=style,
                timeout_seconds=8,
                primary_timeout_seconds=5,
                allow_model_call=(
                    style == HUMANISTIC_INTERVIEWER_STYLE or polish_required
                ),
                deterministic_primary=(
                    style == HUMANISTIC_INTERVIEWER_STYLE_V1_1 and not polish_required
                ),
                renderer_input=renderer_input,
            )
            model_calls += result.model_attempt_count
            if model_calls >= MAX_CALLS:
                raise RuntimeError("synthetic smoke model-call cap reached")
            outputs[style] = {
                "message": result.output.message,
                "status": result.status,
                "fallback_type": result.fallback_type,
                "duration_ms": round((time.perf_counter() - started) * 1000),
                "model_attempt_count": result.model_attempt_count,
                "model": result.model_name,
                "validation_errors": result.validation_errors,
                "polish_required": polish_required,
            }

        if args.v11_only:
            rows.append(
                {
                    "case_id": case.case_id,
                    "synthetic_answer": case.answer,
                    "outputs": outputs,
                }
            )
            print(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "v1_1_ms": outputs[HUMANISTIC_INTERVIEWER_STYLE_V1_1][
                            "duration_ms"
                        ],
                        "calls_so_far": model_calls,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            continue

        order = _opaque_order(case.case_id)
        judged, judge_model = asyncio.run(
            _judge(
                gateway,
                case=case,
                candidate_a=outputs[order[0]]["message"],
                candidate_b=outputs[order[1]]["message"],
            )
        )
        model_calls += 1
        if judge_model != settings.DEEPSEEK_MODEL:
            raise RuntimeError(
                f"judge model mismatch: {judge_model!r} != {settings.DEEPSEEK_MODEL!r}"
            )
        winner = judged.get("winner")
        resolved_winner = (
            "tie"
            if winner == "TIE"
            else order[0]
            if winner == "A"
            else order[1]
            if winner == "B"
            else "invalid"
        )
        rows.append(
            {
                "case_id": case.case_id,
                "synthetic_answer": case.answer,
                "opaque_order": {"A": order[0], "B": order[1]},
                "outputs": outputs,
                "blind_judgment": judged,
                "resolved_winner": resolved_winner,
                "judge_model": judge_model,
            }
        )
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "resolved_winner": resolved_winner,
                    "v1_ms": outputs[HUMANISTIC_INTERVIEWER_STYLE]["duration_ms"],
                    "v1_1_ms": outputs[HUMANISTIC_INTERVIEWER_STYLE_V1_1][
                        "duration_ms"
                    ],
                    "calls_so_far": model_calls,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    wins = {
        HUMANISTIC_INTERVIEWER_STYLE: 0,
        HUMANISTIC_INTERVIEWER_STYLE_V1_1: 0,
        "tie": 0,
        "invalid": 0,
    }
    for row in rows:
        if "resolved_winner" in row:
            wins[row["resolved_winner"]] += 1
    summary = {
        "protocol": (
            "synthetic_v11_only_engineering_diagnostic_v1"
            if args.v11_only
            else "synthetic_blinded_engineering_diagnostic_v1"
        ),
        "formal_human_evidence": False,
        "contains_personal_data": False,
        "case_count": len(rows),
        "model_calls": model_calls,
        "max_calls": MAX_CALLS,
        "model": settings.DEEPSEEK_MODEL,
        "wins": wins,
        "median_latency_ms": {
            style: sorted(row["outputs"][style]["duration_ms"] for row in rows)[
                len(rows) // 2
            ]
            for style in styles
        },
        "rows": rows,
    }
    output_path = args.output_dir / (
        "synthetic_v11_only_diagnostic.json"
        if args.v11_only
        else "synthetic_blind_diagnostic.json"
    )
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.chmod(output_path, 0o600)
    print(
        json.dumps(
            {key: value for key, value in summary.items() if key != "rows"},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
