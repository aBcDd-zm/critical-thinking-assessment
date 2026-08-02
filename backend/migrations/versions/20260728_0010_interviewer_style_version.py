"""add versioned interviewer style to assessment sessions

Revision ID: 20260728_0010
Revises: 20260723_0009
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260728_0010"
down_revision: str | None = "20260723_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        item["name"]: item
        for item in inspector.get_columns("assessment_session")
    }
    column = columns.get("interviewer_style_version")

    if column is None:
        op.add_column(
            "assessment_session",
            sa.Column(
                "interviewer_style_version",
                sa.String(length=32),
                nullable=False,
                server_default="baseline_v1",
            ),
        )
    else:
        op.execute(
            sa.text(
                "UPDATE assessment_session "
                "SET interviewer_style_version = 'baseline_v1' "
                "WHERE interviewer_style_version IS NULL "
                "OR interviewer_style_version = ''"
            )
        )
        if column["nullable"]:
            with op.batch_alter_table("assessment_session") as batch_op:
                batch_op.alter_column(
                    "interviewer_style_version",
                    existing_type=sa.String(length=32),
                    nullable=False,
                    server_default="baseline_v1",
                )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        item["name"] for item in inspector.get_columns("assessment_session")
    }
    if "interviewer_style_version" in columns:
        op.drop_column("assessment_session", "interviewer_style_version")
