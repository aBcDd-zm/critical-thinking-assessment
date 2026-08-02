from decimal import Decimal
from typing import Any

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin
from app.models.types import UINT_BIGINT, UINT_INT, UINT_TINYINT


class Scenario(TimestampMixin, Base):
    __tablename__ = "scenario"
    __table_args__ = (
        UniqueConstraint("scenario_code", name="uk_scenario_code"),
        Index("idx_scenario_status_default", "status", "is_default"),
        Index(
            "idx_scenario_occupation_cache",
            "source_type",
            "occupation_category",
            "occupation_key",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    scenario_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    background: Mapped[str] = mapped_column(Text, nullable=False)
    target_audience: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(64), nullable=False)
    difficulty_level: Mapped[str] = mapped_column(String(32), nullable=False)
    estimated_minutes: Mapped[int] = mapped_column(UINT_INT, nullable=False)
    rotation_weight: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=1)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="seeded"
    )
    base_scenario_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
    )
    occupation_category: Mapped[str | None] = mapped_column(String(64))
    occupation_key: Mapped[str | None] = mapped_column(String(160))
    generation_prompt_version: Mapped[str | None] = mapped_column(String(32))
    generation_model: Mapped[str | None] = mapped_column(String(128))
    generation_metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_immutable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ScenarioStage(TimestampMixin, Base):
    __tablename__ = "scenario_stage"
    __table_args__ = (
        UniqueConstraint("scenario_id", "stage_code", name="uk_stage_code"),
        UniqueConstraint("scenario_id", "stage_order", name="uk_stage_order"),
        Index("idx_stage_status", "scenario_id", "status", "stage_order"),
        CheckConstraint("max_followups >= 0", name="ck_stage_max_followups"),
        CheckConstraint("estimated_minutes > 0", name="ck_stage_estimated_minutes"),
        CheckConstraint(
            "context_ai_weight >= 0 AND context_ai_weight <= 100",
            name="ck_stage_context_ai_weight",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    scenario_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
        nullable=False,
    )
    stage_code: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_order: Mapped[int] = mapped_column(UINT_INT, nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    stage_goal: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    main_question: Mapped[str] = mapped_column(Text, nullable=False)
    context_generation_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="config_guided",
    )
    context_ai_weight: Mapped[int] = mapped_column(
        UINT_TINYINT,
        nullable=False,
        default=30,
    )
    context_generation_constraints_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON
    )
    max_followups: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=2)
    estimated_minutes: Mapped[int] = mapped_column(UINT_INT, nullable=False)
    exit_criteria_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class StageDynamicInfo(TimestampMixin, Base):
    __tablename__ = "stage_dynamic_info"
    __table_args__ = (
        UniqueConstraint("stage_id", "info_code", name="uk_dynamic_info_code"),
        Index("idx_dynamic_info_active", "stage_id", "status", "priority"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    stage_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_stage.id"),
        nullable=False,
    )
    info_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    info_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_condition: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=100)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class StageInterventionRule(TimestampMixin, Base):
    __tablename__ = "stage_intervention_rule"
    __table_args__ = (
        UniqueConstraint("stage_id", "rule_code", name="uk_intervention_rule_code"),
        Index("idx_rule_active", "stage_id", "status", "priority"),
        CheckConstraint(
            "question_ai_weight >= 0 AND question_ai_weight <= 100",
            name="ck_rule_question_ai_weight",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    stage_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_stage.id"),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_condition: Mapped[str | None] = mapped_column(Text)
    strategy_direction: Mapped[str] = mapped_column(Text, nullable=False)
    sample_question: Mapped[str | None] = mapped_column(Text)
    question_generation_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="strategy_guided",
    )
    question_ai_weight: Mapped[int] = mapped_column(
        UINT_TINYINT,
        nullable=False,
        default=40,
    )
    question_generation_constraints_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON
    )
    fallback_question: Mapped[str | None] = mapped_column(Text)
    exit_prompt: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=100)
    max_use_count: Mapped[int | None] = mapped_column(UINT_INT)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class ScenarioStageDimension(Base):
    __tablename__ = "scenario_stage_dimension"
    __table_args__ = (
        UniqueConstraint("stage_id", "dimension_id", name="uk_stage_dimension"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    stage_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_stage.id"),
        nullable=False,
    )
    dimension_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("rubric_dimension.id"),
        nullable=False,
    )
    observe_role: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))


class StageDynamicInfoDimension(Base):
    __tablename__ = "stage_dynamic_info_dimension"
    __table_args__ = (
        UniqueConstraint(
            "dynamic_info_id",
            "dimension_id",
            name="uk_dynamic_info_dimension",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    dynamic_info_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("stage_dynamic_info.id"),
        nullable=False,
    )
    dimension_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("rubric_dimension.id"),
        nullable=False,
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))


class StageInterventionRuleDimension(Base):
    __tablename__ = "stage_intervention_rule_dimension"
    __table_args__ = (
        UniqueConstraint("rule_id", "dimension_id", name="uk_rule_dimension"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("stage_intervention_rule.id"),
        nullable=False,
    )
    dimension_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("rubric_dimension.id"),
        nullable=False,
    )
    weight: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))


class ScenarioPool(TimestampMixin, Base):
    __tablename__ = "scenario_pool"
    __table_args__ = (UniqueConstraint("pool_code", name="uk_pool_code"),)

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    pool_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rotation_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    target_audience: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
    )


class ScenarioPoolItem(Base):
    __tablename__ = "scenario_pool_item"
    __table_args__ = (
        UniqueConstraint("pool_id", "scenario_id", name="uk_pool_scenario"),
        Index("idx_pool_item_order", "pool_id", "status", "display_order"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    pool_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_pool.id"),
        nullable=False,
    )
    scenario_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
        nullable=False,
    )
    rotation_weight: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=1)
    display_order: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class ScenarioGenerationJob(TimestampMixin, Base):
    __tablename__ = "scenario_generation_job"
    __table_args__ = (
        UniqueConstraint("session_id", name="uk_scenario_generation_session"),
        Index("idx_scenario_generation_status", "status", "locked_at"),
        Index("idx_scenario_generation_cache", "occupation_cache_key", "status"),
        CheckConstraint("profile_call_count >= 0", name="ck_profile_call_count"),
        CheckConstraint("design_call_count >= 0", name="ck_design_call_count"),
        CheckConstraint("adaptation_call_count >= 0", name="ck_adaptation_call_count"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
    occupation_cache_key: Mapped[str] = mapped_column(String(192), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    base_scenario_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
    )
    adapted_scenario_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
    )
    draft_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reviewed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    profile_call_count: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=0)
    design_call_count: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=0)
    adaptation_call_count: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
