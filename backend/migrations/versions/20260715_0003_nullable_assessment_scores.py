"""support insufficient-evidence assessment scores

Revision ID: 20260715_0003
Revises: 20260630_0002
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260715_0003"
down_revision: str | None = "20260630_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UINT_TINYINT = sa.SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("score_result")}

    assessment_status_added = "assessment_status" not in columns
    if assessment_status_added:
        op.add_column(
            "score_result",
            sa.Column(
                "assessment_status",
                sa.String(length=32),
                nullable=False,
                server_default="scored",
            ),
        )

    score_column = columns.get("score")
    if score_column is not None and not score_column.get("nullable", False):
        op.alter_column(
            "score_result",
            "score",
            existing_type=UINT_TINYINT,
            nullable=True,
        )

    check_names = {
        constraint.get("name")
        for constraint in sa.inspect(bind).get_check_constraints("score_result")
    }
    if "ck_score_result_assessment_status" not in check_names:
        op.create_check_constraint(
            "ck_score_result_assessment_status",
            "score_result",
            "assessment_status IN ('scored', 'insufficient_evidence')",
        )

    if assessment_status_added:
        op.alter_column("score_result", "assessment_status", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("score_result")}
    if "assessment_status" not in columns:
        return

    op.execute(
        "UPDATE score_result SET score = 1, assessment_status = 'scored' "
        "WHERE score IS NULL"
    )
    check_names = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints("score_result")
    }
    if "ck_score_result_assessment_status" in check_names:
        op.drop_constraint(
            "ck_score_result_assessment_status",
            "score_result",
            type_="check",
        )
    score_column = columns.get("score")
    if score_column is not None and score_column.get("nullable", True):
        op.alter_column(
            "score_result",
            "score",
            existing_type=UINT_TINYINT,
            nullable=False,
        )
    op.drop_column("score_result", "assessment_status")
