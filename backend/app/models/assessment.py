from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin
from app.models.types import UINT_BIGINT, UINT_INT


class AssessmentSession(TimestampMixin, Base):
    __tablename__ = "assessment_session"
    __table_args__ = (
        UniqueConstraint("session_uuid", name="uk_session_uuid"),
        UniqueConstraint("participant_id", name="uk_session_participant"),
        Index("idx_session_status_created", "status", "created_at"),
        Index("idx_session_scenario_status", "scenario_id", "status"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_uuid: Mapped[str] = mapped_column(String(36), nullable=False)
    participant_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("participant.id"),
        nullable=False,
    )
    scenario_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
        nullable=False,
    )
    scenario_pool_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_pool.id"),
    )
    current_stage_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario_stage.id"),
    )
    selection_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    selection_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    assessment_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    language_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    total_duration_seconds: Mapped[int | None] = mapped_column(UINT_INT)
    flow_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="legacy_v2"
    )
    interviewer_style_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="baseline_v1",
        server_default="baseline_v1",
    )
    interview_state_json: Mapped[dict | None] = mapped_column(JSON)
    state_version: Mapped[int] = mapped_column(UINT_INT, nullable=False, default=0)


class DialogueTurn(CreatedAtMixin, Base):
    __tablename__ = "dialogue_turn"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uk_turn_index"),
        UniqueConstraint(
            "session_id", "client_turn_id", name="uk_turn_client_request"
        ),
        Index("idx_turn_stage", "session_id", "stage_id", "turn_index"),
        Index("idx_turn_rule", "intervention_rule_id"),
        Index("idx_turn_dynamic_info", "dynamic_info_id"),
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
    turn_index: Mapped[int] = mapped_column(UINT_INT, nullable=False)
    speaker: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    analysis_json: Mapped[dict | None] = mapped_column(JSON)
    source_agent_trace_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey(
            "agent_trace.id",
            name="fk_dialogue_turn_source_trace",
            use_alter=True,
        ),
    )
    dynamic_info_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("stage_dynamic_info.id"),
    )
    intervention_rule_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("stage_intervention_rule.id"),
    )
    client_turn_id: Mapped[str | None] = mapped_column(String(36))
    answer_duration_ms: Mapped[int | None] = mapped_column(UINT_INT)
