"""add versioned assessment consent records

Revision ID: 20260721_0008
Revises: 20260721_0007
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260721_0008"
down_revision: str | None = "20260721_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UINT_BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(sa.Integer(), "sqlite")
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "consent_record" in inspector.get_table_names():
        return
    op.create_table(
        "consent_record",
        sa.Column("id", UINT_BIGINT, autoincrement=True, nullable=False),
        sa.Column("session_id", UINT_BIGINT, nullable=False),
        sa.Column("consent_status", sa.String(length=16), nullable=False),
        sa.Column("consent_version", sa.String(length=64), nullable=False),
        sa.Column("consented_at", sa.DateTime(), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["assessment_session.id"],
            name="fk_consent_record_session",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uk_consent_record_session"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "consent_record" in inspector.get_table_names():
        op.drop_table("consent_record")
