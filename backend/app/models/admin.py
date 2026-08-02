from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.mixins import TimestampMixin
from app.models.types import UINT_BIGINT


class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_user"
    __table_args__ = (UniqueConstraint("username", name="uk_admin_user_username"),)

    id: Mapped[int] = mapped_column(UINT_BIGINT, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="ADMIN")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))

