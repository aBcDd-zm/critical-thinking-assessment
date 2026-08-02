from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import TimestampMixin
from app.models.types import UINT_BIGINT, UINT_TINYINT


class RubricDimension(TimestampMixin, Base):
    __tablename__ = "rubric_dimension"
    __table_args__ = (UniqueConstraint("dimension_key", name="uk_dimension_key"),)

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    dimension_key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    observable_behaviors: Mapped[list[str] | dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    invalid_evidence_desc: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
    )


class RubricAnchor(TimestampMixin, Base):
    __tablename__ = "rubric_anchor"
    __table_args__ = (
        UniqueConstraint(
            "dimension_id",
            "score_level",
            name="uk_anchor_dimension_score",
        ),
        CheckConstraint(
            "score_level >= 1 AND score_level <= 5",
            name="ck_anchor_score_level",
        ),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    dimension_id: Mapped[int] = mapped_column(
        UINT_BIGINT,
        ForeignKey("rubric_dimension.id"),
        nullable=False,
    )
    score_level: Mapped[int] = mapped_column(UINT_TINYINT, nullable=False)
    level_name: Mapped[str] = mapped_column(String(64), nullable=False)
    behavior_desc: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_examples: Mapped[list[str] | None] = mapped_column(JSON)
    counter_examples: Mapped[list[str] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

