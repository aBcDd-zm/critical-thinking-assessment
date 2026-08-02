from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    JSON,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin
from app.models.types import MEDIUM_TEXT, UINT_BIGINT, UINT_INT, UINT_TINYINT


class AgentTrace(CreatedAtMixin, Base):
    __tablename__ = "agent_trace"
    __table_args__ = (
        Index("idx_trace_session_created", "session_id", "created_at"),
        Index("idx_trace_agent_status", "agent_name", "status", "created_at"),
        Index("idx_trace_stage", "stage_id", "created_at"),
        Index("idx_trace_selected_rule", "selected_rule_id"),
        Index("idx_trace_selected_info", "selected_dynamic_info_id"),
        CheckConstraint("duration_ms >= 0", name="ck_trace_duration_ms"),
        CheckConstraint(
            "ai_generation_weight IS NULL OR "
            "(ai_generation_weight >= 0 AND ai_generation_weight <= 100)",
            name="ck_trace_ai_generation_weight",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
    stage_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_stage.id"),
    )
    trigger_turn_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey(
            "dialogue_turn.id",
            name="fk_agent_trace_trigger_turn",
            use_alter=True,
        ),
    )
    prompt_template_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("prompt_template.id"),
    )
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_mode: Mapped[str | None] = mapped_column(String(32))
    ai_generation_weight: Mapped[int | None] = mapped_column(UINT_TINYINT)
    config_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_output: Mapped[str | None] = mapped_column(MEDIUM_TEXT)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    fallback_type: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(UINT_INT)
    selected_dynamic_info_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("stage_dynamic_info.id"),
    )
    selected_rule_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("stage_intervention_rule.id"),
    )
