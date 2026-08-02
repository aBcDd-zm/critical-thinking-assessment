import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import get_sessionmaker
from app.models.prompt import PromptTemplate
from app.models.report import ReportTemplate
from app.models.rubric import RubricAnchor, RubricDimension
from app.models.scenario import (
    Scenario,
    ScenarioPool,
    ScenarioPoolItem,
    ScenarioStage,
    ScenarioStageDimension,
    StageDynamicInfo,
    StageDynamicInfoDimension,
    StageInterventionRule,
    StageInterventionRuleDimension,
)
from app.services.scenario_materialization_service import ScenarioMaterializationService

SEED_FILES = [
    "rubric.yaml",
    "scenario_product_48h.yaml",
    "scenario_pool.yaml",
    "prompts.yaml",
    "runtime_prompts.yaml",
    "report_template.yaml",
]


def load_yaml(path: Path) -> object:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def get_one(db: Session, model: type, **filters: Any) -> Any | None:
    return db.execute(select(model).filter_by(**filters)).scalar_one_or_none()


def upsert(db: Session, model: type, lookup: dict[str, Any], values: dict[str, Any]) -> Any:
    instance = get_one(db, model, **lookup)
    if instance is None:
        instance = model(**lookup, **values)
        db.add(instance)
    else:
        for key, value in values.items():
            setattr(instance, key, value)
    db.flush()
    return instance


def seed_rubric(db: Session, seed_dir: Path) -> None:
    data = load_yaml(seed_dir / "rubric.yaml")
    version = data.get("version", "v1")

    for item in data.get("dimensions", []):
        anchors = item.pop("anchors", [])
        dimension = upsert(
            db,
            RubricDimension,
            {"dimension_key": item["dimension_key"]},
            {
                "name": item["name"],
                "definition": item["definition"],
                "observable_behaviors": item.get("observable_behaviors", []),
                "invalid_evidence_desc": item.get("invalid_evidence_desc"),
                "version": item.get("version", version),
                "status": item.get("status", "active"),
            },
        )

        for anchor in anchors:
            upsert(
                db,
                RubricAnchor,
                {
                    "dimension_id": dimension.id,
                    "score_level": anchor["score_level"],
                },
                {
                    "level_name": anchor["level_name"],
                    "behavior_desc": anchor["behavior_desc"],
                    "evidence_examples": anchor.get("evidence_examples"),
                    "counter_examples": anchor.get("counter_examples"),
                    "status": anchor.get("status", "active"),
                },
            )


def seed_scenario(db: Session, seed_dir: Path) -> None:
    data = load_yaml(seed_dir / "scenario_product_48h.yaml")
    stages = data.pop("stages", [])

    scenario = upsert(
        db,
        Scenario,
        {"scenario_code": data["scenario_code"]},
        {
            "title": data["title"],
            "background": data["background"],
            "target_audience": data["target_audience"],
            "scenario_type": data["scenario_type"],
            "difficulty_level": data["difficulty_level"],
            "estimated_minutes": data["estimated_minutes"],
            "rotation_weight": data.get("rotation_weight", 1),
            "is_default": data.get("is_default", False),
            "version": data.get("version", "v1"),
            "status": data.get("status", "active"),
            "source_type": data.get("source_type", "seeded"),
            "is_immutable": data.get("is_immutable", False),
        },
    )

    for stage_item in stages:
        dimension_items = stage_item.pop("dimensions", [])
        dynamic_items = stage_item.pop("dynamic_infos", [])
        rule_items = stage_item.pop("intervention_rules", [])

        stage = upsert(
            db,
            ScenarioStage,
            {"scenario_id": scenario.id, "stage_code": stage_item["stage_code"]},
            {
                "stage_order": stage_item["stage_order"],
                "title": stage_item["title"],
                "stage_goal": stage_item["stage_goal"],
                "context": stage_item["context"],
                "main_question": stage_item["main_question"],
                "context_generation_mode": stage_item.get(
                    "context_generation_mode",
                    "config_guided",
                ),
                "context_ai_weight": stage_item.get("context_ai_weight", 30),
                "context_generation_constraints_json": stage_item.get(
                    "context_generation_constraints_json"
                ),
                "max_followups": stage_item.get("max_followups", 2),
                "estimated_minutes": stage_item["estimated_minutes"],
                "exit_criteria_json": stage_item.get("exit_criteria_json"),
                "status": stage_item.get("status", "active"),
            },
        )

        for dimension_item in dimension_items:
            dimension = get_one(
                db,
                RubricDimension,
                dimension_key=dimension_item["dimension_key"],
            )
            if dimension is None:
                raise ValueError(f"Unknown dimension_key: {dimension_item['dimension_key']}")

            upsert(
                db,
                ScenarioStageDimension,
                {"stage_id": stage.id, "dimension_id": dimension.id},
                {
                    "observe_role": dimension_item.get("observe_role", "secondary"),
                    "weight": dimension_item.get("weight"),
                },
            )

        for dynamic_item in dynamic_items:
            dynamic_dimensions = dynamic_item.pop("dimensions", [])
            dynamic_info = upsert(
                db,
                StageDynamicInfo,
                {"stage_id": stage.id, "info_code": dynamic_item["info_code"]},
                {
                    "title": dynamic_item["title"],
                    "content": dynamic_item["content"],
                    "info_type": dynamic_item["info_type"],
                    "trigger_condition": dynamic_item.get("trigger_condition"),
                    "priority": dynamic_item.get("priority", 100),
                    "status": dynamic_item.get("status", "active"),
                },
            )
            for dimension_item in dynamic_dimensions:
                dimension = get_one(
                    db,
                    RubricDimension,
                    dimension_key=dimension_item["dimension_key"],
                )
                if dimension is None:
                    raise ValueError(
                        f"Unknown dimension_key: {dimension_item['dimension_key']}"
                    )
                upsert(
                    db,
                    StageDynamicInfoDimension,
                    {"dynamic_info_id": dynamic_info.id, "dimension_id": dimension.id},
                    {"weight": dimension_item.get("weight")},
                )

        for rule_item in rule_items:
            rule_dimensions = rule_item.pop("dimensions", [])
            rule = upsert(
                db,
                StageInterventionRule,
                {"stage_id": stage.id, "rule_code": rule_item["rule_code"]},
                {
                    "rule_type": rule_item["rule_type"],
                    "trigger_condition": rule_item.get("trigger_condition"),
                    "strategy_direction": rule_item["strategy_direction"],
                    "sample_question": rule_item.get("sample_question"),
                    "question_generation_mode": rule_item.get(
                        "question_generation_mode",
                        "strategy_guided",
                    ),
                    "question_ai_weight": rule_item.get("question_ai_weight", 40),
                    "question_generation_constraints_json": rule_item.get(
                        "question_generation_constraints_json"
                    ),
                    "fallback_question": rule_item.get("fallback_question"),
                    "exit_prompt": rule_item.get("exit_prompt"),
                    "priority": rule_item.get("priority", 100),
                    "max_use_count": rule_item.get("max_use_count"),
                    "status": rule_item.get("status", "active"),
                },
            )
            for dimension_item in rule_dimensions:
                dimension = get_one(
                    db,
                    RubricDimension,
                    dimension_key=dimension_item["dimension_key"],
                )
                if dimension is None:
                    raise ValueError(
                        f"Unknown dimension_key: {dimension_item['dimension_key']}"
                    )
                upsert(
                    db,
                    StageInterventionRuleDimension,
                    {"rule_id": rule.id, "dimension_id": dimension.id},
                    {"weight": dimension_item.get("weight")},
                )


def seed_prompts(db: Session, seed_dir: Path) -> None:
    for file_name in ("prompts.yaml", "runtime_prompts.yaml"):
        data = load_yaml(seed_dir / file_name)
        version = data.get("version", "v1")

        for item in data.get("templates", []):
            upsert(
                db,
                PromptTemplate,
                {
                    "agent_name": item["agent_name"],
                    "template_code": item["template_code"],
                    "version": item.get("version", version),
                },
                {
                    "name": item["name"],
                    "content": item["content"],
                    "input_schema_json": item.get("input_schema_json"),
                    "output_schema_json": item.get("output_schema_json"),
                    "status": item.get("status", "active"),
                },
            )

    # Keep old rows for historical AgentTrace references while ensuring runtime
    # lookup only selects the currently versioned templates.
    active_versions = {
        "profile": {"occupation_profile_v2"},
        "scenario_design": {"occupation_cctst_v2_4"},
        "scenario_review": {"occupation_cctst_v2_4"},
        "scenario_adaptation": {"occupation_cctst_v2_4"},
        "planner": {"progressive_planner_v3_1"},
        "interviewer": {
            "progressive_interviewer_compact_v2",
            "humanistic_interviewer_compact_v2",
            "humanistic_compact_v1_2",
        },
    }
    for agent_name, allowed_versions in active_versions.items():
        db.query(PromptTemplate).filter(
            PromptTemplate.agent_name == agent_name,
            PromptTemplate.version.notin_(allowed_versions),
        ).update({"status": "disabled"}, synchronize_session=False)


def seed_report_template(db: Session, seed_dir: Path) -> None:
    item = load_yaml(seed_dir / "report_template.yaml")
    scenario = None
    if item.get("scenario_code"):
        scenario = get_one(db, Scenario, scenario_code=item["scenario_code"])

    upsert(
        db,
        ReportTemplate,
        {
            "template_code": item["template_code"],
            "version": item.get("version", "v1"),
        },
        {
            "name": item["name"],
            "scenario_id": scenario.id if scenario else None,
            "structure_json": item["structure_json"],
            "status": item.get("status", "active"),
        },
    )


def seed_scenario_pool(db: Session, seed_dir: Path) -> None:
    data = load_yaml(seed_dir / "scenario_pool.yaml")
    items = data.pop("items", [])

    pool = upsert(
        db,
        ScenarioPool,
        {"pool_code": data["pool_code"]},
        {
            "name": data["name"],
            "rotation_strategy": data["rotation_strategy"],
            "target_audience": data["target_audience"],
            "description": data.get("description"),
            "status": data.get("status", "active"),
        },
    )

    for item in items:
        scenario = get_one(db, Scenario, scenario_code=item["scenario_code"])
        if scenario is None:
            raise ValueError(f"Unknown scenario_code: {item['scenario_code']}")
        upsert(
            db,
            ScenarioPoolItem,
            {"pool_id": pool.id, "scenario_id": scenario.id},
            {
                "rotation_weight": item.get("rotation_weight", 1),
                "display_order": item.get("display_order", 1),
                "status": item.get("status", "active"),
            },
        )


def seed_database(seed_dir: Path) -> None:
    session_factory = get_sessionmaker()
    with session_factory() as db:
        seed_rubric(db, seed_dir)
        seed_scenario(db, seed_dir)
        ScenarioMaterializationService(db).ensure_fallback()
        seed_prompts(db, seed_dir)
        seed_report_template(db, seed_dir)
        seed_scenario_pool(db, seed_dir)
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load YAML seed files.")
    parser.add_argument("--env", default="local", help="Runtime environment label.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse files without writing to the database.",
    )
    args = parser.parse_args()

    seed_dir = Path(__file__).resolve().parents[1] / "seeds"
    loaded_count = 0

    for file_name in SEED_FILES:
        path = seed_dir / file_name
        if not path.exists():
            raise FileNotFoundError(f"Missing seed file: {path}")
        load_yaml(path)
        loaded_count += 1
        print(f"Loaded {file_name}")

    if not args.dry_run:
        seed_database(seed_dir)

    mode = "dry-run" if args.dry_run else "upsert"
    print(f"Seed files validated for env={args.env}. mode={mode}. files={loaded_count}")


if __name__ == "__main__":
    main()
