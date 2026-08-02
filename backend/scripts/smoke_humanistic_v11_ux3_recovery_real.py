from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import yaml

from app.agents.interview_blueprint import build_blueprint_from_generated
from app.agents.progressive_schemas import InterviewPlanOutput, PlannerBudget
from app.agents.runtime_interviewer_agent import (
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
from app.services.occupation_skeleton_service import OccupationSkeletonService


CONFIRMATION = "RUN_SYNTHETIC_HUMANISTIC_V11_UX5_RECOVERY"
MAX_CALLS = 18
FORBIDDEN_LOOP_PHRASES = (
    "换一种说法",
    "从最确定的一点重新说",
    "我可能没有理解准确",
    "你能换一种说法",
    "为了判断得更稳妥",
    "顺着这个关注点",
    "顺着这个思路",
    "回到这个话题",
    "围绕这一点",
    "你提到",
    "你把重点放在",
    "你现在关注的是",
    "还有一条情况",
    "我刚才问的是",
    "刚才问的是",
    "刚才是在问",
    "刚才的问题是想了解",
    "我刚才想了解的是",
    "在“",
    "具体说说具体指",
    "你在说什么具体指",
    "这个问题最关键的边界或限制",
    "你现在会先确认哪一点",
)


@dataclass(frozen=True)
class RecoveryCase:
    case_id: str
    latest_user: str
    preceding_ai: str
    response_intent: str
    action: str
    delivery_mode: str
    target_dimension: str | None
    question_intent: str
    target_evidence: str | None
    release_event_code: str | None = None
    earlier_dialogue: tuple[tuple[str, str], ...] = ()


CASES = (
    RecoveryCase(
        "UX3-LOW-01",
        "我不知道",
        "你觉得眼下最需要先判断的问题是什么？",
        "low_information",
        "CLARIFY",
        "clarification",
        None,
        "用更容易回答的方式承接用户",
        None,
    ),
    RecoveryCase(
        "UX3-CLARIFY-01",
        "什么意思",
        "哪些信息还需要核实，才能判断现在的分工是否合理？",
        "clarify_question",
        "CLARIFY",
        "clarification",
        None,
        "用具体日常语言重述前一个问题",
        None,
    ),
    RecoveryCase(
        "UX3-CLARIFY-02",
        "没看懂",
        "这项安排会直接影响谁？",
        "clarify_question",
        "CLARIFY",
        "clarification",
        None,
        "用具体日常语言解释前一个问题",
        None,
        earlier_dialogue=(
            ("ai", "你会怎样比较不同参与者的影响？"),
            ("user", "我不知道"),
        ),
    ),
    RecoveryCase(
        "UX4-CLARIFY-CONSTRAINT-01",
        "什么意思",
        (
            "新安排是减少交接和检查，原安排是逐项交接检查，"
            "也可只在非关键部分试用；面对这项限制，你会依据什么作出初步决定？"
        ),
        "clarify_question",
        "CLARIFY",
        "clarification",
        None,
        "澄清并重述当前问题",
        None,
    ),
    RecoveryCase(
        "UX3-FOCUS-01",
        "组员呀",
        "除了你自己，还有谁会因为你先看进度而需要调整工作？",
        "low_information",
        "PROBE",
        "reflective_probe",
        "problem_definition",
        "不再重复澄清，换一个容易回答的具体角度",
        "换一个具体角度继续访谈",
    ),
    RecoveryCase(
        "UX3-CAUSE-01",
        "为什么会有延迟，是谁负责的",
        "最近10次类似任务中有3次延迟，你会先查什么？",
        "assess_answer",
        "PROBE",
        "reflective_probe",
        "multiple_perspectives",
        "顺着当前话题补充一项尚未充分的证据",
        "补充当前判断的一项关键依据",
    ),
    RecoveryCase(
        "UX3-PROBE-01",
        "我想先确认大家分别负责什么",
        "你觉得眼下最需要先判断什么？",
        "assess_answer",
        "PROBE",
        "reflective_probe",
        "evidence_evaluation",
        "询问用户会先核实哪一类信息",
        "说明一项需要核实的信息",
    ),
    RecoveryCase(
        "UX5-MEETING-01",
        "先召集大家开会吧",
        "你会先做哪一步？",
        "assess_answer",
        "PROBE",
        "reflective_probe",
        "multiple_perspectives",
        "从不同参与者角度追问会议焦点",
        "说明不同参与者的关注",
    ),
    RecoveryCase(
        "UX3-EVENT-01",
        "先确认大家的分工",
        "眼下最想确认的是哪一点？",
        "assess_answer",
        "RELEASE_EVENT",
        "event_link",
        None,
        "结合新出现的不确定信息说明下一步核实重点",
        None,
    ),
    RecoveryCase(
        "UX4-EVENT-STAKEHOLDER-01",
        "先看每个人的任务完成情况",
        "你会先看哪一项进展？",
        "assess_answer",
        "RELEASE_EVENT",
        "event_link",
        None,
        "结合不同参与者的担心追问取舍",
        None,
        release_event_code="stakeholder_conflict",
    ),
    RecoveryCase(
        "UX6-EVENT-STAKEHOLDER-MEETING-01",
        "每个人具体干了什么，最好召集大家开个会",
        "除了分工记录，还缺少什么信息才能排除其他可能？",
        "assess_answer",
        "RELEASE_EVENT",
        "event_link",
        None,
        "为会议讨论明确引入新的参与者信息",
        None,
        release_event_code="stakeholder_conflict",
    ),
    RecoveryCase(
        "UX5-EVENT-CAUSE-01",
        "为什么会有延迟，是谁负责的",
        "你会先查哪项信息？",
        "assess_answer",
        "RELEASE_EVENT",
        "event_link",
        None,
        "说明当前信息边界并自然引入不同参与者的考虑",
        None,
        release_event_code="stakeholder_conflict",
    ),
    RecoveryCase(
        "UX4-EVENT-DECISION-01",
        "我会优先看目前项目进度",
        "你会怎样比较进度和返工风险？",
        "assess_answer",
        "RELEASE_EVENT",
        "event_link",
        None,
        "结合当前限制追问初步安排",
        None,
        release_event_code="decision_pressure",
    ),
    RecoveryCase(
        "UX5-EVENT-COUNTER-01",
        "肯定是组员呀",
        "除了你，谁的工作会跟着调整？",
        "assess_answer",
        "RELEASE_EVENT",
        "event_link",
        None,
        "承接组员焦点后自然引入反向试用结果",
        None,
        release_event_code="counter_evidence",
    ),
    RecoveryCase(
        "UX3-INTEGRATE-01",
        "我会先核对分工，再按影响调整顺序",
        "如果两边的目标冲突，你会依据什么决定先后？",
        "assess_answer",
        "INTEGRATE",
        "integration",
        None,
        "整合已谈到的依据、风险和行动安排",
        None,
    ),
    RecoveryCase(
        "UX7-CLARIFY-ELABORATE-01",
        "能再展开说说吗",
        (
            "为了比较团队里的不同顾虑，我补充一条新的参与者信息："
            "一部分参与者想减少交接和检查以赶进度，"
            "另一部分担心返工和质量风险；这两边先比较什么？"
        ),
        "clarify_question",
        "CLARIFY",
        "clarification",
        None,
        "解释上一问中双方各自关注的内容",
        None,
    ),
    RecoveryCase(
        "UX7-CLARIFY-QUOTE-01",
        "你说的“这两边先比较什么？”",
        (
            "为了比较团队里的不同顾虑，我补充一条新的参与者信息："
            "一部分参与者想减少交接和检查以赶进度，"
            "另一部分担心返工和质量风险；这两边先比较什么？"
        ),
        "clarify_question",
        "CLARIFY",
        "clarification",
        None,
        "解释用户引用的上一个问题",
        None,
    ),
    RecoveryCase(
        "UX7-CLARIFY-META-01",
        "我没跟上，你在问哪件事",
        (
            "为了比较团队里的不同顾虑，我补充一条新的参与者信息："
            "一部分参与者想减少交接和检查以赶进度，"
            "另一部分担心返工和质量风险；这两边先比较什么？"
        ),
        "clarify_question",
        "CLARIFY",
        "clarification",
        None,
        "用日常语言直接说清上一个问题",
        None,
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


def _context(case: RecoveryCase) -> AgentRuntimeContext:
    history: list[DialogueTurnContext] = []
    turn_id = 80
    turn_index = 1
    for speaker, content in case.earlier_dialogue:
        history.append(
            DialogueTurnContext(
                turn_id=turn_id,
                turn_index=turn_index,
                stage_id=11,
                stage_code="s1_problem_definition",
                speaker=speaker,
                content=content,
                content_type="interview_followup"
                if speaker == "ai"
                else "interview_answer",
            )
        )
        turn_id += 1
        turn_index += 1
    history.append(
        DialogueTurnContext(
            turn_id=90,
            turn_index=turn_index,
            stage_id=11,
            stage_code="s1_problem_definition",
            speaker="ai",
            content=case.preceding_ai,
            content_type="interview_followup",
        )
    )
    latest = DialogueTurnContext(
        turn_id=91,
        turn_index=turn_index + 1,
        stage_id=11,
        stage_code="s1_problem_definition",
        speaker="user",
        content=case.latest_user,
        content_type="interview_answer",
    )
    history.append(latest)
    return AgentRuntimeContext(
        session=SessionContext(
            session_id=7,
            session_uuid=str(uuid4()),
            assessment_mode="synthetic",
            status="in_progress",
        ),
        participant=ParticipantContext(participant_id=3, nickname="合成参与者"),
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
        dialogue_history=history,
        latest_user_turn=latest,
    )


def _plan(case: RecoveryCase, blueprint) -> InterviewPlanOutput:
    event = None
    if case.action == "RELEASE_EVENT":
        event_code = case.release_event_code or "evidence_uncertainty"
        event = next(
            item for item in blueprint.event_cards if item.event_code == event_code
        )
    unit = event.presentation_units[0] if event is not None else None
    if case.case_id == "UX5-EVENT-COUNTER-01" and event is not None:
        unit = next(
            item
            for item in event.presentation_units
            if item.unit_code == "error_rate_increase"
        )
    return InterviewPlanOutput(
        response_intent=case.response_intent,
        action=case.action,
        active_topic="合成对话恢复",
        target_dimension=case.target_dimension,
        target_evidence=case.target_evidence,
        delivery_mode=case.delivery_mode,
        question_intent=case.question_intent,
        reflection_basis_turn_ids=(
            [91]
            if case.delivery_mode
            in {"reflective_probe", "event_link", "perspective_shift", "summary_check"}
            else []
        ),
        reason="固定合成计划用于 v1.1 UX3 恢复路径验证",
        release_event_code=event.event_code if event is not None else None,
        release_unit_code=unit.unit_code if unit is not None else None,
        budget=PlannerBudget(
            used_turns=2,
            remaining_turns=8,
            reserved_update_turns=2,
            reserved_closure_turns=1,
        ),
    )


def _prompt_content() -> str:
    source = Path(__file__).resolve().parents[1] / "seeds" / "runtime_prompts.yaml"
    rows = yaml.safe_load(source.read_text(encoding="utf-8"))
    return next(
        item["content"]
        for item in rows["templates"]
        if item["template_code"] == "humanistic_compact_v1_1"
    )


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
    parser.add_argument("--start-case-id")
    args = parser.parse_args()
    if args.confirmation != CONFIRMATION:
        raise SystemExit("confirmation mismatch; no model call made")

    settings = get_settings()
    if settings.MODEL_GATEWAY_MODE.lower() != "real":
        raise SystemExit("MODEL_GATEWAY_MODE must be real")
    if not settings.DEEPSEEK_API_KEY:
        raise SystemExit("DeepSeek API key is not configured")
    _secure_output_dir(args.output_dir)

    selected_cases = CASES
    if args.start_case_id:
        start_indexes = [
            index
            for index, case in enumerate(CASES)
            if case.case_id == args.start_case_id
        ]
        if not start_indexes:
            raise SystemExit("start-case-id is not a known synthetic case")
        selected_cases = CASES[start_indexes[0] :]

    blueprint = _blueprint()
    renderer = InterviewerAgent()
    prompt = _prompt_content()
    rows: list[dict[str, object]] = []
    calls = 0
    fallback_count = 0
    for case in selected_cases:
        context = _context(case)
        plan = _plan(case, blueprint)
        renderer_input = renderer.runtime_renderer_input_payload(
            context,
            blueprint,
            plan,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
        )
        result = renderer.render(
            context,
            blueprint,
            plan,
            previous_questions=[case.preceding_ai],
            template_content=prompt,
            style_version=HUMANISTIC_INTERVIEWER_STYLE_V1_1,
            timeout_seconds=8,
            primary_timeout_seconds=5,
            allow_model_call=True,
            renderer_input=renderer_input,
        )
        calls += result.model_attempt_count
        errors: list[str] = []
        if result.status != "ok":
            fallback_count += 1
            if (
                result.fallback_type != "humanistic_deterministic_renderer"
                or result.transport_errors
            ):
                errors.append(f"unsafe_fallback_status:{result.status}")
        if result.model_attempt_count != 1:
            errors.append(f"model_attempt_count:{result.model_attempt_count}")
        if result.model_name != settings.DEEPSEEK_MODEL:
            errors.append(f"model_identity:{result.model_name}")
        final_contract_errors = renderer._v11_contract_errors(  # noqa: SLF001
            result.output,
            renderer_input,
        )
        if final_contract_errors:
            errors.extend(
                f"final_contract:{item}" for item in final_contract_errors
            )
        for phrase in FORBIDDEN_LOOP_PHRASES:
            if phrase in result.output.message:
                errors.append(f"clarification_loop_phrase:{phrase}")
        if result.output.message == case.preceding_ai:
            errors.append("repeated_preceding_question")
        if (
            case.response_intent == "clarify_question"
            and len(result.output.message) > 64
        ):
            errors.append(f"clarification_too_long:{len(result.output.message)}")
        if case.case_id == "UX3-FOCUS-01":
            if "组员" not in result.output.message:
                errors.append("missing_normalized_focus:组员")
            if "组员呀" in result.output.message:
                errors.append("untrimmed_spoken_particle")
        if case.case_id == "UX5-MEETING-01":
            if "开会" not in result.output.message:
                errors.append("missing_meeting_focus")
            if "直接的做法" in result.output.message:
                errors.append("evaluative_meeting_acknowledgement")
        if case.case_id == "UX5-EVENT-CAUSE-01" and not all(
            marker in result.output.message
            for marker in ("延迟", "还不能说明", "新的参与者信息", "比较")
        ):
            errors.append("missing_cause_boundary_transition")
        if case.case_id == "UX5-EVENT-COUNTER-01":
            if not all(
                marker in result.output.message
                for marker in ("组员", "变化", "调整")
            ) or not any(
                marker in result.output.message
                for marker in ("新的试用结果", "新结果")
            ):
                errors.append("missing_counterevidence_transition")
        if case.case_id == "UX6-EVENT-STAKEHOLDER-MEETING-01" and not all(
            marker in result.output.message
            for marker in ("会上", "新的参与者信息", "为了")
        ):
            errors.append("missing_meeting_event_transition")
        if case.case_id.startswith("UX7-CLARIFY-") and not (
            "进度" in result.output.message
            and any(
                marker in result.output.message for marker in ("返工", "质量")
            )
        ):
            errors.append("missing_two_sided_clarification")
        if case.action == "RELEASE_EVENT":
            if not any(
                marker in result.output.message
                for marker in (
                    "补充一条新",
                    "补充一项新",
                    "新信息：",
                    "新限制：",
                    "新结果：",
                )
            ):
                errors.append("missing_explicit_new_information_marker")
            if not any(
                marker in result.output.message
                for marker in (
                    "为了",
                    "为核实",
                    "为比较",
                    "为选择",
                    "为调整",
                    "为整合",
                )
            ):
                errors.append("missing_new_information_reason")
        rows.append(
            {
                "case_id": case.case_id,
                "latest_user": case.latest_user,
                "preceding_ai": case.preceding_ai,
                "message": result.output.message,
                "status": result.status,
                "duration_ms": result.duration_ms,
                "model_attempt_count": result.model_attempt_count,
                "model": result.model_name,
                "raw_output": result.raw_output,
                "validation_errors": result.validation_errors,
                "assertion_errors": errors,
                "used_safe_fallback": result.status != "ok",
            }
        )
        print(
            json.dumps(
                {
                    "case_id": case.case_id,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "assertion_errors": errors,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if errors:
            break

    summary = {
        "protocol": "humanistic_v1_1_ux7_final_repair_synthetic_v1",
        "contains_personal_data": False,
        "formal_human_evidence": False,
        "case_count": len(rows),
        "expected_case_count": len(selected_cases),
        "model_calls": calls,
        "max_calls": min(MAX_CALLS, len(selected_cases)),
        "model_success_count": len(rows) - fallback_count,
        "safe_fallback_count": fallback_count,
        "passed": len(rows) == len(selected_cases)
        and all(not row["assertion_errors"] for row in rows),
        "rows": rows,
    }
    output_path = args.output_dir / "ux6_event_intro_smoke.json"
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
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
