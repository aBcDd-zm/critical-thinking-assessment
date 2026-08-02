"""add expert scoring and human review loop

Revision ID: 20260716_0005
Revises: 20260716_0004
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260716_0005"
down_revision: str | None = "20260716_0004"
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
    inspector = sa.inspect(bind)

    if not inspector.has_table("human_review"):
        op.create_table(
            "human_review",
            sa.Column("id", UINT_BIGINT, primary_key=True, autoincrement=True),
            sa.Column("session_id", UINT_BIGINT, nullable=False),
            sa.Column("reviewer_id", UINT_BIGINT, nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["assessment_session.id"]),
            sa.ForeignKeyConstraint(["reviewer_id"], ["admin_user.id"]),
            sa.UniqueConstraint("session_id", name="uk_human_review_session"),
            sa.CheckConstraint(
                "status IN ('pending', 'in_review', 'completed', 'needs_adjudication')",
                name="ck_human_review_status",
            ),
            sa.CheckConstraint(
                "decision IS NULL OR "
                "decision IN ('valid', 'needs_adjudication', 'exclude')",
                name="ck_human_review_decision",
            ),
        )
        op.create_index(
            "idx_human_review_status",
            "human_review",
            ["status", "updated_at"],
        )

    inspector = sa.inspect(bind)
    if not inspector.has_table("expert_score_annotation"):
        op.create_table(
            "expert_score_annotation",
            sa.Column("id", UINT_BIGINT, primary_key=True, autoincrement=True),
            sa.Column("session_id", UINT_BIGINT, nullable=False),
            sa.Column("stage_id", UINT_BIGINT, nullable=False),
            sa.Column("dimension_id", UINT_BIGINT, nullable=False),
            sa.Column("annotator_id", UINT_BIGINT, nullable=False),
            sa.Column("assessment_status", sa.String(length=32), nullable=False),
            sa.Column("score", UINT_TINYINT, nullable=True),
            sa.Column("evidence_ids_json", sa.JSON(), nullable=True),
            sa.Column("bars_reason", sa.Text(), nullable=False),
            sa.Column("next_level_gap", sa.Text(), nullable=True),
            sa.Column("annotator_confidence", sa.String(length=16), nullable=False),
            sa.Column("review_flag", sa.Boolean(), nullable=False),
            sa.Column("review_reason", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("import_batch_id", sa.String(length=36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["assessment_session.id"]),
            sa.ForeignKeyConstraint(["stage_id"], ["scenario_stage.id"]),
            sa.ForeignKeyConstraint(["dimension_id"], ["rubric_dimension.id"]),
            sa.ForeignKeyConstraint(["annotator_id"], ["admin_user.id"]),
            sa.UniqueConstraint(
                "session_id",
                "stage_id",
                "dimension_id",
                "annotator_id",
                name="uk_expert_score_target",
            ),
            sa.CheckConstraint(
                "assessment_status IN ('scored', 'insufficient_evidence')",
                name="ck_expert_score_status",
            ),
            sa.CheckConstraint(
                "(assessment_status = 'scored' AND score >= 1 AND score <= 5) "
                "OR (assessment_status = 'insufficient_evidence' AND score IS NULL)",
                name="ck_expert_score_value",
            ),
            sa.CheckConstraint(
                "annotator_confidence IN ('high', 'medium', 'low')",
                name="ck_expert_score_confidence",
            ),
        )
        op.create_index(
            "idx_expert_score_session",
            "expert_score_annotation",
            ["session_id", "annotator_id"],
        )
        op.create_index(
            "idx_expert_score_dimension",
            "expert_score_annotation",
            ["dimension_id", "score"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("expert_score_annotation"):
        op.drop_index(
            "idx_expert_score_dimension",
            table_name="expert_score_annotation",
        )
        op.drop_index(
            "idx_expert_score_session",
            table_name="expert_score_annotation",
        )
        op.drop_table("expert_score_annotation")
    if inspector.has_table("human_review"):
        op.drop_index("idx_human_review_status", table_name="human_review")
        op.drop_table("human_review")
