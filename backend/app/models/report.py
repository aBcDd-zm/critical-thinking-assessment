from typing import Any

from sqlalchemy import ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import TimestampMixin
from app.models.types import UINT_BIGINT


class ReportTemplate(TimestampMixin, Base):
    __tablename__ = "report_template"
    __table_args__ = (
        UniqueConstraint("template_code", "version", name="uk_report_template_version"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    structure_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    scenario_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("scenario.id"),
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
    )


class AssessmentReport(TimestampMixin, Base):
    __tablename__ = "assessment_report"
    __table_args__ = (
        UniqueConstraint("session_id", name="uk_report_session"),
        Index("idx_report_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("assessment_session.id"),
        nullable=False,
    )
    report_template_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("report_template.id"),
    )
    agent_trace_id: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("agent_trace.id"),
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")

