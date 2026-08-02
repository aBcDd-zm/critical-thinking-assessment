"""persist dialogue analysis and language mode

Revision ID: 20260716_0004
Revises: 20260715_0003
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_0004"
down_revision: str | None = "20260715_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    session_columns = {
        column["name"]
        for column in inspector.get_columns("assessment_session")
    }
    turn_columns = {
        column["name"]
        for column in inspector.get_columns("dialogue_turn")
    }

    language_mode_added = "language_mode" not in session_columns
    if language_mode_added:
        op.add_column(
            "assessment_session",
            sa.Column(
                "language_mode",
                sa.String(length=32),
                nullable=False,
                server_default="standard",
            ),
        )
    if "analysis_json" not in turn_columns:
        op.add_column(
            "dialogue_turn",
            sa.Column("analysis_json", sa.JSON(), nullable=True),
        )
    if language_mode_added:
        op.alter_column("assessment_session", "language_mode", server_default=None)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    turn_columns = {
        column["name"] for column in inspector.get_columns("dialogue_turn")
    }
    session_columns = {
        column["name"]
        for column in inspector.get_columns("assessment_session")
    }
    if "analysis_json" in turn_columns:
        op.drop_column("dialogue_turn", "analysis_json")
    if "language_mode" in session_columns:
        op.drop_column("assessment_session", "language_mode")
