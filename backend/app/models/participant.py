from typing import Any

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import TimestampMixin
from app.models.types import UINT_BIGINT


class Participant(TimestampMixin, Base):
    __tablename__ = "participant"
    __table_args__ = (Index("idx_participant_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    age_range: Mapped[str | None] = mapped_column(String(32))
    identity_type: Mapped[str | None] = mapped_column(String(32))
    education_stage: Mapped[str | None] = mapped_column(String(64))
    major_direction: Mapped[str | None] = mapped_column(String(128))
    career_direction: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    work_years_range: Mapped[str | None] = mapped_column(String(32))
    organization_role: Mapped[str | None] = mapped_column(String(64))
    self_description: Mapped[str | None] = mapped_column(Text)
    info_collect_method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ai_dialogue",
    )
    raw_basic_info: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="self_assessment",
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class ParticipantProfile(TimestampMixin, Base):
    __tablename__ = "participant_profile"
    __table_args__ = (
        UniqueConstraint("session_id", name="uk_participant_profile_session"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
    raw_background_answers: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ai_profile_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    population_type: Mapped[str | None] = mapped_column(String(32))
    adaptation_tags: Mapped[list[str] | None] = mapped_column(JSON)
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")


class ConsentRecord(TimestampMixin, Base):
    __tablename__ = "consent_record"
    __table_args__ = (
        UniqueConstraint("session_id", name="uk_consent_record_session"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="accepted"
    )
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, default=datetime.utcnow
    )
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
