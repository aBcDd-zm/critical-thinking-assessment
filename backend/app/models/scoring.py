from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import CreatedAtMixin
from app.models.types import UINT_BIGINT, UINT_TINYINT


class ScoreSnapshot(CreatedAtMixin, Base):
    __tablename__ = "score_snapshot"
    __table_args__ = (
        Index("idx_snapshot_session_type", "session_id", "snapshot_type", "created_at"),
        Index("idx_snapshot_stage", "stage_id", "created_at"),
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
    dialogue_turn_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("dialogue_turn.id"),
    )
    snapshot_type: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    trend_analysis: Mapped[str | None] = mapped_column(Text)
    agent_trace_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("agent_trace.id"),
    )


class ScoreResult(CreatedAtMixin, Base):
    __tablename__ = "score_result"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "dimension_id", name="uk_snapshot_dimension"),
        Index("idx_score_dimension", "dimension_id", "score"),
        CheckConstraint("score >= 1 AND score <= 5", name="ck_score_result_score"),
        CheckConstraint(
            "assessment_status IN ('scored', 'insufficient_evidence')",
            name="ck_score_result_assessment_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_score_result_confidence",
        ),
        CheckConstraint(
            "evidence_sufficiency_index IS NULL OR "
            "(evidence_sufficiency_index >= 0 AND evidence_sufficiency_index <= 100)",
            name="ck_score_result_esi",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("score_snapshot.id"),
        nullable=False,
    )
    dimension_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("rubric_dimension.id"),
        nullable=False,
    )
    score: Mapped[int | None] = mapped_column(UINT_TINYINT)
    assessment_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scored"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    evidence_sufficiency_index: Mapped[int | None] = mapped_column(UINT_TINYINT)
    scoring_source: Mapped[str] = mapped_column(String(32), nullable=False)


class ScoreEvidence(CreatedAtMixin, Base):
    __tablename__ = "score_evidence"
    __table_args__ = (
        Index("idx_evidence_score_result", "score_result_id"),
        Index("idx_evidence_turn", "dialogue_turn_id"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    score_result_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("score_result.id"),
        nullable=False,
    )
    dialogue_turn_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("dialogue_turn.id"),
    )
    evidence_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
