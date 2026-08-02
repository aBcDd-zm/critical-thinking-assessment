from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from app.models.mixins import TimestampMixin
from app.models.types import UINT_BIGINT, UINT_TINYINT


class HumanReview(TimestampMixin, Base):
    __tablename__ = "human_review"
    __table_args__ = (
        UniqueConstraint("session_id", name="uk_human_review_session"),
        Index("idx_human_review_status", "status", "updated_at"),
        CheckConstraint(
            "status IN ('pending', 'in_review', 'completed', 'needs_adjudication')",
            name="ck_human_review_status",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('valid', 'needs_adjudication', 'exclude')",
            name="ck_human_review_decision",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
    reviewer_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    decision: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))


class ExpertScoreAnnotation(TimestampMixin, Base):
    __tablename__ = "expert_score_annotation"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "stage_id",
            "dimension_id",
            "annotator_id",
            name="uk_expert_score_target",
        ),
        Index("idx_expert_score_session", "session_id", "annotator_id"),
        Index("idx_expert_score_dimension", "dimension_id", "score"),
        CheckConstraint(
            "assessment_status IN ('scored', 'insufficient_evidence')",
            name="ck_expert_score_status",
        ),
        CheckConstraint(
            "(assessment_status = 'scored' AND score >= 1 AND score <= 5) "
            "OR (assessment_status = 'insufficient_evidence' AND score IS NULL)",
            name="ck_expert_score_value",
        ),
        CheckConstraint(
            "annotator_confidence IN ('high', 'medium', 'low')",
            name="ck_expert_score_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
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
    annotator_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
        nullable=False,
    )
    assessment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int | None] = mapped_column(UINT_TINYINT)
    evidence_ids_json: Mapped[list[int] | None] = mapped_column(JSON)
    bars_reason: Mapped[str] = mapped_column(Text, nullable=False)
    next_level_gap: Mapped[str | None] = mapped_column(Text)
    annotator_confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    review_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    import_batch_id: Mapped[str | None] = mapped_column(String(36))


__all__ = ["ExpertScoreAnnotation", "HumanReview"]
