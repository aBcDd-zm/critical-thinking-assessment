from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.agents.schemas import (  # noqa: E402
    AgentRuntimeContext,
    DialogueTurnContext,
    DynamicInfoContext,
    InterventionRuleContext,
    ParticipantContext,
    RubricAnchorContext,
    RubricDimensionContext,
    ScenarioContext,
    ScoreGapSummary,
    SessionContext,
    StageContext,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"
SEED_DIR = BACKEND_DIR / "seeds"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_fixtures() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        load_json(FIXTURE_DIR / "assessment_users.json"),
        load_json(FIXTURE_DIR / "dialogue_cases.json"),
        load_json(FIXTURE_DIR / "scoring_cases.json"),
    )


def load_seed_data() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        load_yaml(SEED_DIR / "scenario_product_48h.yaml"),
        load_yaml(SEED_DIR / "rubric.yaml"),
    )


def index_by(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        value = item.get(key)
        if not isinstance(value, str) or not value:
            raise AssertionError(f"Missing string field {key}: {item}")
        if value in indexed:
            raise AssertionError(f"Duplicate {key}: {value}")
        indexed[value] = item
    return indexed


def find_stage(scenario_seed: dict[str, Any], stage_code: str) -> dict[str, Any]:
    for stage in scenario_seed.get("stages", []):
        if stage.get("stage_code") == stage_code:
            return stage
    raise AssertionError(f"Unknown stage_code in fixture: {stage_code}")


def build_rubric_dimensions(rubric_seed: dict[str, Any]) -> list[RubricDimensionContext]:
    dimensions: list[RubricDimensionContext] = []
    for item in rubric_seed.get("dimensions", []):
        dimensions.append(
            RubricDimensionContext(
                dimension_key=item["dimension_key"],
                name=item["name"],
                definition=item["definition"],
                observable_behaviors=item.get("observable_behaviors") or [],
                invalid_evidence_desc=item.get("invalid_evidence_desc"),
            )
        )
    return dimensions


def build_rubric_anchors(rubric_seed: dict[str, Any]) -> list[RubricAnchorContext]:
    anchors: list[RubricAnchorContext] = []
    for dimension in rubric_seed.get("dimensions", []):
        for item in dimension.get("anchors", []):
            anchors.append(
                RubricAnchorContext(
                    dimension_key=dimension["dimension_key"],
                    score_level=item["score_level"],
                    level_name=item["level_name"],
                    behavior_desc=item["behavior_desc"],
                    evidence_examples=item.get("evidence_examples"),
                    counter_examples=item.get("counter_examples"),
                )
            )
    return anchors


def build_dynamic_infos(stage_seed: dict[str, Any]) -> list[DynamicInfoContext]:
    items: list[DynamicInfoContext] = []
    for item in stage_seed.get("dynamic_infos", []):
        items.append(
            DynamicInfoContext(
                info_code=item["info_code"],
                title=item["title"],
                content=item["content"],
                info_type=item["info_type"],
                trigger_condition=item.get("trigger_condition"),
                priority=item.get("priority", 100),
                target_dimensions=[
                    dimension["dimension_key"] for dimension in item.get("dimensions", [])
                ],
            )
        )
    return items


def build_intervention_rules(stage_seed: dict[str, Any]) -> list[InterventionRuleContext]:
    items: list[InterventionRuleContext] = []
    for item in stage_seed.get("intervention_rules", []):
        items.append(
            InterventionRuleContext(
                rule_code=item["rule_code"],
                rule_type=item["rule_type"],
                trigger_condition=item.get("trigger_condition"),
                strategy_direction=item["strategy_direction"],
                sample_question=item.get("sample_question"),
                question_generation_mode=item.get("question_generation_mode", "strategy_guided"),
                question_ai_weight=item.get("question_ai_weight", 40),
                question_generation_constraints_json=item.get(
                    "question_generation_constraints_json"
                ),
                fallback_question=item.get("fallback_question"),
                exit_prompt=item.get("exit_prompt"),
                priority=item.get("priority", 100),
                max_use_count=item.get("max_use_count"),
                target_dimensions=[
                    dimension["dimension_key"] for dimension in item.get("dimensions", [])
                ],
            )
        )
    return items


def build_context_from_dialogue_case(
    dialogue_case: dict[str, Any],
    users_by_id: dict[str, dict[str, Any]],
    scenario_seed: dict[str, Any],
    rubric_seed: dict[str, Any],
) -> AgentRuntimeContext:
    user = users_by_id[dialogue_case["user_id"]]
    stage_seed = find_stage(scenario_seed, dialogue_case["stage_code"])
    history = [
        DialogueTurnContext(
            turn_id=index + 1,
            turn_index=index + 1,
            stage_code=dialogue_case["stage_code"],
            speaker=turn["speaker"],
            content=turn["content"],
            content_type=turn.get("content_type", "scenario_answer"),
            dynamic_info_id=turn.get("dynamic_info_id"),
            selected_dynamic_info_code=turn.get("selected_dynamic_info_code"),
        )
        for index, turn in enumerate(dialogue_case["history"])
    ]
    latest_user_turn = next(
        (turn for turn in reversed(history) if turn.speaker == "user"),
        None,
    )
    if latest_user_turn is None:
        raise AssertionError(f"Dialogue case has no user turn: {dialogue_case['case_id']}")

    return AgentRuntimeContext(
        session=SessionContext(
            session_uuid=f"fixture-{dialogue_case['case_id']}",
            assessment_mode="mock",
        ),
        participant=ParticipantContext(
            nickname=user["nickname"],
            profile_summary=user["profile"]["background"],
        ),
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
            context_generation_mode=stage_seed.get("context_generation_mode", "config_guided"),
            context_ai_weight=stage_seed.get("context_ai_weight", 30),
            max_followups=stage_seed.get("max_followups", 2),
            estimated_minutes=stage_seed.get("estimated_minutes"),
        ),
        dialogue_history=history,
        latest_user_turn=latest_user_turn,
        rubric_dimensions=build_rubric_dimensions(rubric_seed),
        rubric_anchors=build_rubric_anchors(rubric_seed),
        candidate_dynamic_infos=build_dynamic_infos(stage_seed),
        candidate_intervention_rules=build_intervention_rules(stage_seed),
        score_gap_summary=ScoreGapSummary(**dialogue_case["score_gap_summary"]),
        professional_context=[
            "本 fixture 仅用于本地开发和自动化验收，不包含真实个人隐私数据。",
            "评分证据必须来自用户原话、已释放动态信息或情境配置，不允许编造。",
        ],
    )


def validate_users(users: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    users_by_id = index_by(users, "user_id")
    for user in users:
        if user.get("privacy_note") != "synthetic_fixture_only":
            raise AssertionError(f"Fixture user must be synthetic: {user['user_id']}")
        if not user.get("nickname"):
            raise AssertionError(f"Missing nickname: {user['user_id']}")
        profile = user.get("profile") or {}
        for key in ("role_type", "background", "answer_style"):
            if not profile.get(key):
                raise AssertionError(f"Missing profile.{key}: {user['user_id']}")
    return users_by_id


def validate_dialogue_cases(
    dialogue_cases: list[dict[str, Any]],
    users_by_id: dict[str, dict[str, Any]],
    scenario_seed: dict[str, Any],
    rubric_seed: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    cases_by_id = index_by(dialogue_cases, "case_id")
    allowed_stage_codes = {stage["stage_code"] for stage in scenario_seed.get("stages", [])}
    for case in dialogue_cases:
        if case["user_id"] not in users_by_id:
            raise AssertionError(f"Unknown dialogue user_id: {case['user_id']}")
        if case["scenario_code"] != scenario_seed["scenario_code"]:
            raise AssertionError(f"Unknown scenario_code: {case['scenario_code']}")
        if case["stage_code"] not in allowed_stage_codes:
            raise AssertionError(f"Unknown stage_code: {case['stage_code']}")
        if not case.get("history") or len(case["history"]) < 2:
            raise AssertionError(f"Dialogue case needs at least ai+user turns: {case['case_id']}")
        if case["history"][-1]["speaker"] != "user":
            raise AssertionError(f"Latest dialogue turn must be user: {case['case_id']}")
        if case["latest_user_answer"] != case["history"][-1]["content"]:
            raise AssertionError(f"latest_user_answer mismatch: {case['case_id']}")
        expected = case.get("expected_followup") or {}
        if not expected.get("allowed_next_actions"):
            raise AssertionError(f"Missing allowed_next_actions: {case['case_id']}")
        build_context_from_dialogue_case(case, users_by_id, scenario_seed, rubric_seed)
    return cases_by_id


def validate_scoring_cases(
    scoring_cases: list[dict[str, Any]],
    users_by_id: dict[str, dict[str, Any]],
    dialogue_cases_by_id: dict[str, dict[str, Any]],
    rubric_seed: dict[str, Any],
) -> None:
    dimension_keys = {
        dimension["dimension_key"] for dimension in rubric_seed.get("dimensions", [])
    }
    for case in scoring_cases:
        if case["user_id"] not in users_by_id:
            raise AssertionError(f"Unknown scoring user_id: {case['user_id']}")
        for dialogue_case_id in case.get("dialogue_case_ids", []):
            if dialogue_case_id not in dialogue_cases_by_id:
                raise AssertionError(f"Unknown dialogue_case_id: {dialogue_case_id}")
        for item in case.get("expected_dimension_ranges", []):
            if item["dimension_key"] not in dimension_keys:
                raise AssertionError(f"Unknown dimension_key: {item['dimension_key']}")
            if not 1 <= item["min_score"] <= item["max_score"] <= 5:
                raise AssertionError(f"Invalid score range: {case['case_id']}")
        if case.get("must_have_disclaimer") is not True:
            raise AssertionError(f"Scoring case must require disclaimer: {case['case_id']}")


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

    print("Agent fixture check passed:")
    print(f"  users={len(users)}")
    print(f"  dialogue_cases={len(dialogue_cases)}")
    print(f"  scoring_cases={len(scoring_cases)}")
    print(f"  scenario={scenario_seed['scenario_code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
