from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import TimestampMixin
from app.models.types import UINT_BIGINT, UINT_TINYINT


class SessionFeedback(TimestampMixin, Base):
    __tablename__ = "session_feedback"
    __table_args__ = (
        UniqueConstraint("session_id", name="uk_feedback_session"),
        Index("idx_feedback_created", "created_at"),
        CheckConstraint(
            "realism_score >= 1 AND realism_score <= 5",
            name="ck_feedback_realism_score",
        ),
        CheckConstraint(
            "difficulty_score >= 1 AND difficulty_score <= 5",
            name="ck_feedback_difficulty_score",
        ),
        CheckConstraint(
            "naturalness_score >= 1 AND naturalness_score <= 5",
            name="ck_feedback_naturalness_score",
        ),
        CheckConstraint(
            "fatigue_score >= 1 AND fatigue_score <= 5",
            name="ck_feedback_fatigue_score",
        ),
        CheckConstraint(
            "report_trust_score >= 1 AND report_trust_score <= 5",
            name="ck_feedback_report_trust_score",
        ),
        CheckConstraint(
            "overall_satisfaction_score >= 1 AND overall_satisfaction_score <= 5",
            name="ck_feedback_overall_satisfaction_score",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
    realism_score: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    difficulty_score: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    naturalness_score: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    fatigue_score: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    report_trust_score: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    overall_satisfaction_score: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    open_feedback: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
