from typing import Any

from sqlalchemy import ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import TimestampMixin
from app.models.types import MEDIUM_TEXT, UINT_BIGINT


class PromptTemplate(TimestampMixin, Base):
    __tablename__ = "prompt_template"
    __table_args__ = (
        UniqueConstraint(
            "agent_name",
            "template_code",
            "version",
            name="uk_prompt_template_version",
        ),
        Index("idx_prompt_active", "agent_name", "status"),
    )

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(MEDIUM_TEXT, nullable=False)
    input_schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_by: Mapped[int | None] = mapped_column(
        UINT_BIGINT,
        ForeignKey("admin_user.id"),
    )
