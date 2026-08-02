from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents import (  # noqa: E402
    AgentRuntimeContext,
    DialogueTurnContext,
    FollowupOutput,
    HostOutput,
    ParticipantContext,
    ScenarioContext,
    SessionContext,
    StageContext,
)
from app.agents.followup_agent import FollowupAgent  # noqa: E402
from app.agents.host_agent import HostAgent  # noqa: E402
from app.agents.mock_dialogue import STAGE_FALLBACK_QUESTIONS  # noqa: E402
from app.agents.user_turn_intent import classify_user_turn  # noqa: E402
from check_agent_fixture_cases import (  # noqa: E402
    build_context_from_dialogue_case,
    build_dynamic_infos,
    build_intervention_rules,
    build_rubric_anchors,
    build_rubric_dimensions,
    load_fixtures,
    load_seed_data,
    validate_dialogue_cases,
    validate_scoring_cases,
    validate_users,
)


def assert_case(condition: bool, case_name: str, message: str) -> None:
    if not condition:
        raise AssertionError(f"[{case_name}] {message}")


def run_case_normal_answer(users_by_id: dict, dialogue_cases_by_id: dict) -> None:
    case_name = "normal_answer_followup"
    case = dialogue_cases_by_id["student_medium_s2"]
    context = build_context_from_dialogue_case(
        case, users_by_id, *load_seed_data()
    )
    output = FollowupAgent().generate(context)
    assert_case(
        isinstance(output, FollowupOutput),
        case_name,
        "output must be FollowupOutput",
    )
    assert_case(
        output.next_action == "ask_followup",
        case_name,
        f"expected ask_followup, got {output.next_action}",
    )
    assert_case(
        output.question and len(output.question) > 10,
        case_name,
        "followup question should not be empty",
    )
    assert_case(
        not output.fallback_used,
        case_name,
        "normal case should not fall back",
    )
    HostOutput.model_validate(HostAgent().generate(context).model_dump())
    FollowupOutput.model_validate(output.model_dump())
    print(f"  {case_name}: passed")


def run_case_low_information_per_stage(users_by_id: dict) -> None:
    case_name = "low_information_stage_fallback"
    scenario_seed, rubric_seed = load_seed_data()
    stage_codes = list(STAGE_FALLBACK_QUESTIONS.keys())
    for stage_code in stage_codes:
        stage_seed = next(
            stage for stage in scenario_seed["stages"] if stage["stage_code"] == stage_code
        )
        context = AgentRuntimeContext(
            session=SessionContext(session_uuid=f"reg-{stage_code}", assessment_mode="mock"),
            participant=ParticipantContext(nickname="小秦"),
            scenario=ScenarioContext(
                scenario_code=scenario_seed["scenario_code"],
                title=scenario_seed["title"],
                background=scenario_seed["background"],
            ),
            stage=StageContext(
                stage_code=stage_seed["stage_code"],
                stage_order=stage_seed["stage_order"],
                title=stage_seed["title"],
                stage_goal=stage_seed["stage_goal"],
                context=stage_seed["context"],
                main_question=stage_seed["main_question"],
                max_followups=stage_seed.get("max_followups", 2),
            ),
            dialogue_history=[
                DialogueTurnContext(
                    turn_id=1,
                    turn_index=1,
                    stage_code=stage_code,
                    speaker="ai",
                    content=stage_seed["main_question"],
                    content_type="stage_question",
                ),
                DialogueTurnContext(
                    turn_id=2,
                    turn_index=2,
                    stage_code=stage_code,
                    speaker="user",
                    content="不知道。",
                    content_type="scenario_answer",
                ),
            ],
            latest_user_turn=DialogueTurnContext(
                turn_id=2,
                turn_index=2,
                stage_code=stage_code,
                speaker="user",
                content="不知道。",
                content_type="scenario_answer",
            ),
            rubric_dimensions=build_rubric_dimensions(rubric_seed),
            rubric_anchors=build_rubric_anchors(rubric_seed),
            candidate_dynamic_infos=build_dynamic_infos(stage_seed),
            candidate_intervention_rules=build_intervention_rules(stage_seed),
        )
        output = FollowupAgent().generate(context)
        assert_case(
            output.next_action == "ask_followup",
            case_name,
            f"{stage_code}: expected ask_followup, got {output.next_action}",
        )
        assert_case(
            len(output.question) >= 12 and "？" in output.question,
            case_name,
            f"{stage_code}: expected a clear simplified current question, got {output.question!r}",
        )
        assert_case(
            output.content_type == "guidance_response",
            case_name,
            f"{stage_code}: low-information guidance must not consume a formal followup or clarification",
        )
        FollowupOutput.model_validate(output.model_dump())
    print(f"  {case_name}: passed ({len(stage_codes)} stages)")


def run_case_dynamic_info_dedup(users_by_id: dict) -> None:
    case_name = "dynamic_info_release_dedup"
    scenario_seed, rubric_seed = load_seed_data()
    stage_seed = next(
        stage for stage in scenario_seed["stages"] if stage["stage_code"] == "s5_dynamic_adjustment"
    )
    infos = build_dynamic_infos(stage_seed)
    assert_case(len(infos) >= 2, case_name, "s5 needs at least 2 dynamic infos for dedup test")

    info_a, info_b = infos[0], infos[1]
    base_history = [
        DialogueTurnContext(
            turn_id=1,
            turn_index=1,
            stage_code="s5_dynamic_adjustment",
            speaker="ai",
            content=stage_seed["main_question"],
            content_type="stage_question",
        ),
        DialogueTurnContext(
            turn_id=2,
            turn_index=2,
            stage_code="s5_dynamic_adjustment",
            speaker="user",
            content="我倾向于按时上线。",
            content_type="scenario_answer",
        ),
    ]
    released_turn = DialogueTurnContext(
        turn_id=3,
        turn_index=3,
        stage_code="s5_dynamic_adjustment",
        speaker="ai",
        content=f"现在补充一条新信息：{info_a.content}",
        content_type="dynamic_info_question",
        dynamic_info_id=info_a.dynamic_info_id,
        selected_dynamic_info_code=info_a.info_code,
    )
    context = AgentRuntimeContext(
        session=SessionContext(session_uuid="reg-dedup", assessment_mode="mock"),
        participant=ParticipantContext(nickname="小秦"),
        scenario=ScenarioContext(
            scenario_code=scenario_seed["scenario_code"],
            title=scenario_seed["title"],
            background=scenario_seed["background"],
        ),
        stage=StageContext(
            stage_code=stage_seed["stage_code"],
            stage_order=stage_seed["stage_order"],
            title=stage_seed["title"],
            stage_goal=stage_seed["stage_goal"],
            context=stage_seed["context"],
            main_question=stage_seed["main_question"],
            max_followups=3,
        ),
        dialogue_history=base_history + [released_turn],
        latest_user_turn=DialogueTurnContext(
            turn_id=4,
            turn_index=4,
            stage_code="s5_dynamic_adjustment",
            speaker="user",
            content="还是需要再想想。",
            content_type="scenario_answer",
        ),
        rubric_dimensions=build_rubric_dimensions(rubric_seed),
        rubric_anchors=build_rubric_anchors(rubric_seed),
        candidate_dynamic_infos=infos,
        candidate_intervention_rules=build_intervention_rules(stage_seed),
    )
    output = FollowupAgent().generate(context)
    assert_case(
        output.selected_dynamic_info_code != info_a.info_code,
        case_name,
        f"dynamic info {info_a.info_code} was already released and should not be re-selected",
    )
    if output.selected_dynamic_info_code:
        assert_case(
            output.selected_dynamic_info_code == info_b.info_code,
            case_name,
            f"expected unreleased info {info_b.info_code}, got {output.selected_dynamic_info_code}",
        )
        assert_case(
            info_b.content not in output.question,
            case_name,
            f"dynamic info must be rendered as a separate turn: {output.question!r}",
        )
        assert_case(
            output.released_dynamic_info_text == info_b.content,
            case_name,
            "released_dynamic_info_text must match selected info content",
        )
    FollowupOutput.model_validate(output.model_dump())
    print(f"  {case_name}: passed")


def run_case_dynamic_info_visible(users_by_id: dict) -> None:
    case_name = "dynamic_info_visible"
    scenario_seed, rubric_seed = load_seed_data()
    stage_seed = next(
        stage for stage in scenario_seed["stages"] if stage["stage_code"] == "s5_dynamic_adjustment"
    )
    infos = build_dynamic_infos(stage_seed)
    assert_case(len(infos) >= 1, case_name, "s5 needs at least 1 dynamic info")
    info = infos[0]
    context = AgentRuntimeContext(
        session=SessionContext(session_uuid="reg-visible", assessment_mode="mock"),
        participant=ParticipantContext(nickname="小秦"),
        scenario=ScenarioContext(
            scenario_code=scenario_seed["scenario_code"],
            title=scenario_seed["title"],
            background=scenario_seed["background"],
        ),
        stage=StageContext(
            stage_code=stage_seed["stage_code"],
            stage_order=stage_seed["stage_order"],
            title=stage_seed["title"],
            stage_goal=stage_seed["stage_goal"],
            context=stage_seed["context"],
            main_question=stage_seed["main_question"],
            max_followups=3,
        ),
        dialogue_history=[
            DialogueTurnContext(
                turn_id=1,
                turn_index=1,
                stage_code="s5_dynamic_adjustment",
                speaker="ai",
                content=stage_seed["main_question"],
                content_type="stage_question",
            ),
            DialogueTurnContext(
                turn_id=2,
                turn_index=2,
                stage_code="s5_dynamic_adjustment",
                speaker="user",
                content="我倾向于按时上线。",
                content_type="scenario_answer",
            ),
        ],
        latest_user_turn=DialogueTurnContext(
            turn_id=2,
            turn_index=2,
            stage_code="s5_dynamic_adjustment",
            speaker="user",
            content="我倾向于按时上线。",
            content_type="scenario_answer",
        ),
        rubric_dimensions=build_rubric_dimensions(rubric_seed),
        rubric_anchors=build_rubric_anchors(rubric_seed),
        candidate_dynamic_infos=infos,
        candidate_intervention_rules=build_intervention_rules(stage_seed),
    )
    output = FollowupAgent().generate(context)
    assert_case(
        output.selected_dynamic_info_code == info.info_code,
        case_name,
        f"expected dynamic info {info.info_code} to be selected",
    )
    assert_case(
        info.content not in output.question,
        case_name,
        f"dynamic info must not be duplicated inside the followup: {output.question!r}",
    )
    assert_case(
        output.released_dynamic_info_text == info.content,
        case_name,
        "released_dynamic_info_text must equal selected info content",
    )
    FollowupOutput.model_validate(output.model_dump())
    print(f"  {case_name}: passed")


def run_case_stage_advancement(users_by_id: dict) -> None:
    case_name = "stage_advancement_after_max_followups"
    scenario_seed, rubric_seed = load_seed_data()
    stage_seed = next(
        stage for stage in scenario_seed["stages"] if stage["stage_code"] == "s1_problem_definition"
    )
    context = AgentRuntimeContext(
        session=SessionContext(session_uuid="reg-advance", assessment_mode="mock"),
        participant=ParticipantContext(nickname="小秦"),
        scenario=ScenarioContext(
            scenario_code=scenario_seed["scenario_code"],
            title=scenario_seed["title"],
            background=scenario_seed["background"],
        ),
        stage=StageContext(
            stage_code=stage_seed["stage_code"],
            stage_order=stage_seed["stage_order"],
            title=stage_seed["title"],
            stage_goal=stage_seed["stage_goal"],
            context=stage_seed["context"],
            main_question=stage_seed["main_question"],
            max_followups=2,
        ),
        dialogue_history=[
            DialogueTurnContext(
                turn_id=1,
                turn_index=1,
                stage_code="s1_problem_definition",
                speaker="ai",
                content=stage_seed["main_question"],
                content_type="stage_question",
            ),
            DialogueTurnContext(
                turn_id=2,
                turn_index=2,
                stage_code="s1_problem_definition",
                speaker="user",
                content="核心问题是上线窗口。",
                content_type="scenario_answer",
            ),
            DialogueTurnContext(
                turn_id=3,
                turn_index=3,
                stage_code="s1_problem_definition",
                speaker="ai",
                content="追问1",
                content_type="followup_question",
            ),
            DialogueTurnContext(
                turn_id=4,
                turn_index=4,
                stage_code="s1_problem_definition",
                speaker="user",
                content="不知道。",
                content_type="scenario_answer",
            ),
            DialogueTurnContext(
                turn_id=5,
                turn_index=5,
                stage_code="s1_problem_definition",
                speaker="ai",
                content="追问2",
                content_type="followup_question",
            ),
            DialogueTurnContext(
                turn_id=6,
                turn_index=6,
                stage_code="s1_problem_definition",
                speaker="user",
                content="我认为核心问题仍是上线风险是否可控。",
                content_type="scenario_answer",
            ),
        ],
        latest_user_turn=DialogueTurnContext(
            turn_id=6,
            turn_index=6,
            stage_code="s1_problem_definition",
            speaker="user",
            content="我认为核心问题仍是上线风险是否可控。",
            content_type="scenario_answer",
        ),
        rubric_dimensions=build_rubric_dimensions(rubric_seed),
        rubric_anchors=build_rubric_anchors(rubric_seed),
        candidate_dynamic_infos=build_dynamic_infos(stage_seed),
        candidate_intervention_rules=build_intervention_rules(stage_seed),
    )
    output = FollowupAgent().generate(context)
    assert_case(
        output.next_action in {"advance_stage", "finish_ready"},
        case_name,
        f"expected advance after max followups, got {output.next_action}",
    )
    assert_case(
        any("followups_used=2" in warning for warning in output.warnings),
        case_name,
        f"expected followup-limit audit warning, got {output.warnings}",
    )
    FollowupOutput.model_validate(output.model_dump())
    print(f"  {case_name}: passed")


def run_case_clarification_and_short_answer(users_by_id: dict) -> None:
    case_name = "clarification_intent_and_short_substantive_answer"
    scenario_seed, rubric_seed = load_seed_data()
    stage_seed = next(
        stage for stage in scenario_seed["stages"] if stage["stage_code"] == "s1_problem_definition"
    )

    def build_context(answer: str) -> AgentRuntimeContext:
        latest = DialogueTurnContext(
            turn_id=2,
            turn_index=2,
            stage_code=stage_seed["stage_code"],
            speaker="user",
            content=answer,
            content_type="scenario_answer",
        )
        return AgentRuntimeContext(
            session=SessionContext(session_uuid=f"reg-intent-{answer}", assessment_mode="mock"),
            participant=ParticipantContext(nickname="小秦"),
            scenario=ScenarioContext(
                scenario_code=scenario_seed["scenario_code"],
                title=scenario_seed["title"],
                background=scenario_seed["background"],
            ),
            stage=StageContext(
                stage_code=stage_seed["stage_code"],
                stage_order=stage_seed["stage_order"],
                title=stage_seed["title"],
                stage_goal=stage_seed["stage_goal"],
                context=stage_seed["context"],
                main_question=stage_seed["main_question"],
                max_followups=2,
            ),
            dialogue_history=[
                DialogueTurnContext(
                    turn_id=1,
                    turn_index=1,
                    stage_code=stage_seed["stage_code"],
                    speaker="ai",
                    content=stage_seed["main_question"],
                    content_type="stage_question",
                ),
                latest,
            ],
            latest_user_turn=latest,
            rubric_dimensions=build_rubric_dimensions(rubric_seed),
            rubric_anchors=build_rubric_anchors(rubric_seed),
            candidate_dynamic_infos=build_dynamic_infos(stage_seed),
            candidate_intervention_rules=build_intervention_rules(stage_seed),
        )

    clarification = FollowupAgent().generate(build_context("现在到底有什么信息什么问题啊"))
    assert_case(
        classify_user_turn("现在到底有什么信息什么问题啊") == "clarification_request",
        case_name,
        "clarification request was not recognized",
    )
    assert_case(
        clarification.content_type == "clarification_response"
        and clarification.selected_dynamic_info_code is None,
        case_name,
        "clarification must not consume a followup or release dynamic info",
    )

    short_answer = FollowupAgent().generate(build_context("延期"))
    assert_case(
        classify_user_turn("延期") == "substantive_answer",
        case_name,
        "short but substantive answer was misclassified",
    )
    assert_case(
        short_answer.content_type not in {"clarification_response", "guidance_response"},
        case_name,
        "short substantive answer must enter the formal dialogue policy",
    )
    print(f"  {case_name}: passed")


def run_case_output_contract(users_by_id: dict, dialogue_cases_by_id: dict) -> None:
    case_name = "output_schema_contract"
    for case_id in ["student_weak_s1", "student_medium_s2", "workplace_strong_s6"]:
        case = dialogue_cases_by_id[case_id]
        context = build_context_from_dialogue_case(
            case, users_by_id, *load_seed_data()
        )
        host_output = HostAgent().generate(context)
        followup_output = FollowupAgent().generate(context)
        HostOutput.model_validate(host_output.model_dump())
        FollowupOutput.model_validate(followup_output.model_dump())
    print(f"  {case_name}: passed")


def main() -> int:
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

    print("Dialogue flow regression check:")
    run_case_normal_answer(users_by_id, dialogue_cases_by_id)
    run_case_low_information_per_stage(users_by_id)
    run_case_dynamic_info_dedup(users_by_id)
    run_case_dynamic_info_visible(users_by_id)
    run_case_stage_advancement(users_by_id)
    run_case_clarification_and_short_answer(users_by_id)
    run_case_output_contract(users_by_id, dialogue_cases_by_id)
    print("All dialogue flow regression checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
