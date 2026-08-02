"""add session feedback

Revision ID: 20260630_0002
Revises: 20260627_0001
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql

revision: str = "20260630_0002"
down_revision: str | None = "20260627_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UINT_BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(sa.Integer(), "sqlite")
)
UINT_TINYINT = sa.SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    if inspect(bind).has_table("session_feedback"):
        return

    op.create_table(
        "session_feedback",
        sa.Column("id", UINT_BIGINT, primary_key=True, autoincrement=True),
        sa.Column("session_id", UINT_BIGINT, nullable=False),
        sa.Column("realism_score", UINT_TINYINT, nullable=False),
        sa.Column("difficulty_score", UINT_TINYINT, nullable=False),
        sa.Column("naturalness_score", UINT_TINYINT, nullable=False),
        sa.Column("fatigue_score", UINT_TINYINT, nullable=False),
        sa.Column("report_trust_score", UINT_TINYINT, nullable=False),
        sa.Column("overall_satisfaction_score", UINT_TINYINT, nullable=False),
        sa.Column("open_feedback", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["assessment_session.id"]),
        sa.UniqueConstraint("session_id", name="uk_feedback_session"),
        sa.CheckConstraint(
            "realism_score >= 1 AND realism_score <= 5",
            name="ck_feedback_realism_score",
        ),
        sa.CheckConstraint(
            "difficulty_score >= 1 AND difficulty_score <= 5",
            name="ck_feedback_difficulty_score",
        ),
        sa.CheckConstraint(
            "naturalness_score >= 1 AND naturalness_score <= 5",
            name="ck_feedback_naturalness_score",
        ),
        sa.CheckConstraint(
            "fatigue_score >= 1 AND fatigue_score <= 5",
            name="ck_feedback_fatigue_score",
        ),
        sa.CheckConstraint(
            "report_trust_score >= 1 AND report_trust_score <= 5",
            name="ck_feedback_report_trust_score",
        ),
        sa.CheckConstraint(
            "overall_satisfaction_score >= 1 AND overall_satisfaction_score <= 5",
            name="ck_feedback_overall_satisfaction_score",
        ),
    )
    op.create_index("idx_feedback_created", "session_feedback", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table("session_feedback"):
        return
    op.drop_index("idx_feedback_created", table_name="session_feedback")
    op.drop_table("session_feedback")
