"""add progressive interview v3 state and audit fields

Revision ID: 20260721_0007
Revises: 20260717_0006
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260721_0007"
down_revision: str | None = "20260717_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    session_columns = {
        item["name"] for item in inspector.get_columns("assessment_session")
    }
    turn_columns = {
        item["name"] for item in inspector.get_columns("dialogue_turn")
    }
    trace_columns = {
        item["name"] for item in inspector.get_columns("agent_trace")
    }

    flow_added = "flow_version" not in session_columns
    state_version_added = "state_version" not in session_columns
    if flow_added:
        op.add_column(
            "assessment_session",
            sa.Column(
                "flow_version",
                sa.String(length=32),
                nullable=False,
                server_default="legacy_v2",
            ),
        )
    if "interview_state_json" not in session_columns:
        op.add_column(
            "assessment_session",
            sa.Column("interview_state_json", sa.JSON(), nullable=True),
        )
    if state_version_added:
        op.add_column(
            "assessment_session",
            sa.Column(
                "state_version",
                sa.Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql"),
                nullable=False,
                server_default="0",
            ),
        )
    if "client_turn_id" not in turn_columns:
        op.add_column(
            "dialogue_turn",
            sa.Column("client_turn_id", sa.String(length=36), nullable=True),
        )
    if "answer_duration_ms" not in turn_columns:
        op.add_column(
            "dialogue_turn",
            sa.Column(
                "answer_duration_ms",
                sa.Integer().with_variant(mysql.INTEGER(unsigned=True), "mysql"),
                nullable=True,
            ),
        )
    unique_names = {
        item["name"]
        for item in inspector.get_unique_constraints("dialogue_turn")
    }
    if "uk_turn_client_request" not in unique_names:
        op.create_unique_constraint(
            "uk_turn_client_request",
            "dialogue_turn",
            ["session_id", "client_turn_id"],
        )
    if "fallback_type" not in trace_columns:
        op.add_column(
            "agent_trace",
            sa.Column("fallback_type", sa.String(length=64), nullable=True),
        )
    op.alter_column(
        "assessment_session",
        "flow_version",
        existing_type=sa.String(length=32),
        existing_nullable=False,
        server_default=None,
    )
    op.alter_column(
        "assessment_session",
        "state_version",
        existing_type=sa.Integer().with_variant(
            mysql.INTEGER(unsigned=True), "mysql"
        ),
        existing_nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    trace_columns = {
        item["name"] for item in inspector.get_columns("agent_trace")
    }
    if "fallback_type" in trace_columns:
        op.drop_column("agent_trace", "fallback_type")
    unique_names = {
        item["name"]
        for item in inspector.get_unique_constraints("dialogue_turn")
    }
    if "uk_turn_client_request" in unique_names:
        op.drop_constraint(
            "uk_turn_client_request", "dialogue_turn", type_="unique"
        )
    turn_columns = {
        item["name"] for item in inspector.get_columns("dialogue_turn")
    }
    if "answer_duration_ms" in turn_columns:
        op.drop_column("dialogue_turn", "answer_duration_ms")
    if "client_turn_id" in turn_columns:
        op.drop_column("dialogue_turn", "client_turn_id")
    session_columns = {
        item["name"] for item in inspector.get_columns("assessment_session")
    }
    if "state_version" in session_columns:
        op.drop_column("assessment_session", "state_version")
    if "interview_state_json" in session_columns:
        op.drop_column("assessment_session", "interview_state_json")
    if "flow_version" in session_columns:
        op.drop_column("assessment_session", "flow_version")
