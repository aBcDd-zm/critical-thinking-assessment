from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.scenario import (
    Scenario,
    ScenarioStage,
    StageDynamicInfo,
    StageInterventionRule,
)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@router.get("/default")
def get_default_scenario(db: Session = Depends(get_db)) -> dict[str, object]:
    scenario = db.execute(
        select(Scenario).where(Scenario.is_default.is_(True), Scenario.status == "active")
    ).scalar_one_or_none()

    if scenario is None:
        raise HTTPException(status_code=404, detail="Default scenario not found")

    stage_count = db.execute(
        select(func.count()).select_from(ScenarioStage).where(
            ScenarioStage.scenario_id == scenario.id,
            ScenarioStage.status == "active",
        )
    ).scalar_one()

    dynamic_info_count = db.execute(
        select(func.count()).select_from(StageDynamicInfo).join(
            ScenarioStage,
            StageDynamicInfo.stage_id == ScenarioStage.id,
        ).where(
            ScenarioStage.scenario_id == scenario.id,
            StageDynamicInfo.status == "active",
        )
    ).scalar_one()

    intervention_rule_count = db.execute(
        select(func.count()).select_from(StageInterventionRule).join(
            ScenarioStage,
            StageInterventionRule.stage_id == ScenarioStage.id,
        ).where(
            ScenarioStage.scenario_id == scenario.id,
            StageInterventionRule.status == "active",
        )
    ).scalar_one()

    return {
        "scenario_code": scenario.scenario_code,
        "title": scenario.title,
        "target_audience": scenario.target_audience,
        "scenario_type": scenario.scenario_type,
        "estimated_minutes": scenario.estimated_minutes,
        "version": scenario.version,
        "stage_count": stage_count,
        "dynamic_info_count": dynamic_info_count,
        "intervention_rule_count": intervention_rule_count,
    }

