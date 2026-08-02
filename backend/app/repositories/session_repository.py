from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.agent import AgentTrace
from app.models.assessment import AssessmentSession, DialogueTurn
from app.models.feedback import SessionFeedback
from app.models.participant import Participant
from app.models.report import AssessmentReport
from app.models.rubric import RubricAnchor, RubricDimension
from app.models.scenario import (
    Scenario,
    ScenarioStage,
    ScenarioStageDimension,
    StageDynamicInfo,
    StageDynamicInfoDimension,
    StageInterventionRule,
    StageInterventionRuleDimension,
)


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_default_scenario(self) -> Scenario | None:
        return self.db.execute(
            select(Scenario).where(Scenario.is_default.is_(True), Scenario.status == "active")
        ).scalar_one_or_none()

    def get_first_active_stage(self, scenario_id: int) -> ScenarioStage | None:
        return self.db.execute(
            select(ScenarioStage)
            .where(ScenarioStage.scenario_id == scenario_id, ScenarioStage.status == "active")
            .order_by(ScenarioStage.stage_order)
            .limit(1)
        ).scalar_one_or_none()

    def get_next_active_stage(
        self,
        scenario_id: int,
        current_stage_order: int,
    ) -> ScenarioStage | None:
        return self.db.execute(
            select(ScenarioStage)
            .where(
                ScenarioStage.scenario_id == scenario_id,
                ScenarioStage.status == "active",
                ScenarioStage.stage_order > current_stage_order,
            )
            .order_by(ScenarioStage.stage_order)
            .limit(1)
        ).scalar_one_or_none()

    def list_active_stages(self, scenario_id: int) -> list[ScenarioStage]:
        return list(
            self.db.execute(
                select(ScenarioStage)
                .where(
                    ScenarioStage.scenario_id == scenario_id,
                    ScenarioStage.status == "active",
                )
                .order_by(ScenarioStage.stage_order)
            ).scalars()
        )

    def get_session_by_uuid(self, session_uuid: str) -> AssessmentSession | None:
        return self.db.execute(
            select(AssessmentSession).where(
                AssessmentSession.session_uuid == session_uuid
            )
        ).scalar_one_or_none()

    def try_mark_session_generating(self, session_uuid: str) -> bool:
        result = self.db.execute(
            update(AssessmentSession)
            .where(
                AssessmentSession.session_uuid == session_uuid,
                AssessmentSession.status.in_(["created", "in_progress"]),
            )
            .values(status="generating")
        )
        return bool(result.rowcount)

    def try_mark_opening_generating(self, session_uuid: str) -> bool:
        result = self.db.execute(
            update(AssessmentSession)
            .where(
                AssessmentSession.session_uuid == session_uuid,
                AssessmentSession.status == "opening_pending",
                AssessmentSession.flow_version.in_(
                    ["progressive_v3_2", "progressive_v3_3"]
                ),
            )
            .values(status="generating")
        )
        return bool(result.rowcount)

    def list_stage_dimension_bindings(
        self,
        scenario_id: int,
    ) -> list[tuple[str, str, str, float | None]]:
        rows = self.db.execute(
            select(
                ScenarioStage.stage_code,
                RubricDimension.dimension_key,
                ScenarioStageDimension.observe_role,
                ScenarioStageDimension.weight,
            )
            .join(
                ScenarioStageDimension,
                ScenarioStageDimension.stage_id == ScenarioStage.id,
            )
            .join(
                RubricDimension,
                RubricDimension.id == ScenarioStageDimension.dimension_id,
            )
            .where(
                ScenarioStage.scenario_id == scenario_id,
                ScenarioStage.status == "active",
                RubricDimension.status == "active",
            )
            .order_by(ScenarioStage.stage_order, ScenarioStageDimension.id)
        ).all()
        return [
            (
                stage_code,
                dimension_key,
                observe_role,
                float(weight) if weight is not None else None,
            )
            for stage_code, dimension_key, observe_role, weight in rows
        ]

    def get_participant(self, participant_id: int) -> Participant | None:
        return self.db.get(Participant, participant_id)

    def get_scenario(self, scenario_id: int) -> Scenario | None:
        return self.db.get(Scenario, scenario_id)

    def get_stage(self, stage_id: int | None) -> ScenarioStage | None:
        if stage_id is None:
            return None
        return self.db.get(ScenarioStage, stage_id)

    def get_stage_by_code(
        self, scenario_id: int, stage_code: str
    ) -> ScenarioStage | None:
        return self.db.execute(
            select(ScenarioStage).where(
                ScenarioStage.scenario_id == scenario_id,
                ScenarioStage.stage_code == stage_code,
                ScenarioStage.status == "active",
            )
        ).scalar_one_or_none()

    def get_user_turn_by_client_id(
        self, session_id: int, client_turn_id: str
    ) -> DialogueTurn | None:
        return self.db.execute(
            select(DialogueTurn).where(
                DialogueTurn.session_id == session_id,
                DialogueTurn.client_turn_id == client_turn_id,
                DialogueTurn.speaker == "user",
            )
        ).scalar_one_or_none()

    def get_interviewer_turn_for_trigger(
        self, session_id: int, trigger_turn_id: int
    ) -> DialogueTurn | None:
        return self.db.execute(
            select(DialogueTurn)
            .join(
                AgentTrace,
                DialogueTurn.source_agent_trace_id == AgentTrace.id,
            )
            .where(
                DialogueTurn.session_id == session_id,
                AgentTrace.trigger_turn_id == trigger_turn_id,
                AgentTrace.agent_name.in_(
                    ["interviewer", "consultative_turn", "interviewer_renderer"]
                ),
            )
            .order_by(DialogueTurn.turn_index.desc())
        ).scalars().first()

    def list_turns(self, session_id: int) -> list[DialogueTurn]:
        return list(
            self.db.execute(
                select(DialogueTurn)
                .where(DialogueTurn.session_id == session_id)
                .order_by(DialogueTurn.turn_index)
            ).scalars()
        )

    def get_turn_by_index(
        self,
        session_id: int,
        turn_index: int,
    ) -> DialogueTurn | None:
        return self.db.execute(
            select(DialogueTurn).where(
                DialogueTurn.session_id == session_id,
                DialogueTurn.turn_index == turn_index,
            )
        ).scalar_one_or_none()

    def next_turn_index(self, session_id: int) -> int:
        current = self.db.execute(
            select(func.max(DialogueTurn.turn_index)).where(
                DialogueTurn.session_id == session_id
            )
        ).scalar_one()
        return int(current or 0) + 1

    def list_active_dynamic_infos(self, stage_id: int) -> list[StageDynamicInfo]:
        return list(
            self.db.execute(
                select(StageDynamicInfo)
                .where(
                    StageDynamicInfo.stage_id == stage_id,
                    StageDynamicInfo.status == "active",
                )
                .order_by(StageDynamicInfo.priority, StageDynamicInfo.id)
            ).scalars()
        )

    def list_active_intervention_rules(
        self,
        stage_id: int,
    ) -> list[StageInterventionRule]:
        return list(
            self.db.execute(
                select(StageInterventionRule)
                .where(
                    StageInterventionRule.stage_id == stage_id,
                    StageInterventionRule.status == "active",
                )
                .order_by(StageInterventionRule.priority, StageInterventionRule.id)
            ).scalars()
        )

    def get_dynamic_info_by_code(
        self,
        stage_id: int,
        info_code: str | None,
    ) -> StageDynamicInfo | None:
        if not info_code:
            return None
        return self.db.execute(
            select(StageDynamicInfo).where(
                StageDynamicInfo.stage_id == stage_id,
                StageDynamicInfo.info_code == info_code,
            )
        ).scalar_one_or_none()

    def get_intervention_rule_by_code(
        self,
        stage_id: int,
        rule_code: str | None,
    ) -> StageInterventionRule | None:
        if not rule_code:
            return None
        return self.db.execute(
            select(StageInterventionRule).where(
                StageInterventionRule.stage_id == stage_id,
                StageInterventionRule.rule_code == rule_code,
            )
        ).scalar_one_or_none()

    def list_active_rubric_dimensions(self) -> list[RubricDimension]:
        return list(
            self.db.execute(
                select(RubricDimension)
                .where(RubricDimension.status == "active")
                .order_by(RubricDimension.id)
            ).scalars()
        )

    def list_active_rubric_anchors(self) -> list[tuple[RubricAnchor, str]]:
        return list(
            self.db.execute(
                select(RubricAnchor, RubricDimension.dimension_key)
                .join(RubricDimension, RubricAnchor.dimension_id == RubricDimension.id)
                .where(
                    RubricAnchor.status == "active",
                    RubricDimension.status == "active",
                )
                .order_by(RubricDimension.id, RubricAnchor.score_level)
            ).all()
        )

    def list_dynamic_info_dimension_keys(self, stage_id: int) -> dict[int, list[str]]:
        rows = self.db.execute(
            select(StageDynamicInfoDimension.dynamic_info_id, RubricDimension.dimension_key)
            .join(
                StageDynamicInfo,
                StageDynamicInfoDimension.dynamic_info_id == StageDynamicInfo.id,
            )
            .join(RubricDimension, StageDynamicInfoDimension.dimension_id == RubricDimension.id)
            .where(StageDynamicInfo.stage_id == stage_id)
        ).all()
        return _group_dimension_keys(rows)

    def list_rule_dimension_keys(self, stage_id: int) -> dict[int, list[str]]:
        rows = self.db.execute(
            select(StageInterventionRuleDimension.rule_id, RubricDimension.dimension_key)
            .join(
                StageInterventionRule,
                StageInterventionRuleDimension.rule_id == StageInterventionRule.id,
            )
            .join(
                RubricDimension,
                StageInterventionRuleDimension.dimension_id == RubricDimension.id,
            )
            .where(StageInterventionRule.stage_id == stage_id)
        ).all()
        return _group_dimension_keys(rows)

    def get_report(self, session_id: int) -> AssessmentReport | None:
        return self.db.execute(
            select(AssessmentReport).where(AssessmentReport.session_id == session_id)
        ).scalar_one_or_none()

    def get_feedback(self, session_id: int) -> SessionFeedback | None:
        return self.db.execute(
            select(SessionFeedback).where(SessionFeedback.session_id == session_id)
        ).scalar_one_or_none()


def _group_dimension_keys(rows: list[tuple[int, str]]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for owner_id, dimension_key in rows:
        grouped.setdefault(owner_id, []).append(dimension_key)
    return grouped
