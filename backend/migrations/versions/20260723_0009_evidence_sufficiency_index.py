"""add evidence sufficiency index to score results

Revision ID: 20260723_0009
Revises: 20260721_0008
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20260723_0009"
down_revision: str | None = "20260721_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("score_result")}
    if "evidence_sufficiency_index" not in columns:
        op.add_column(
            "score_result",
            sa.Column("evidence_sufficiency_index", sa.SmallInteger(), nullable=True),
        )
    checks = {
        item.get("name") for item in sa.inspect(op.get_bind()).get_check_constraints(
            "score_result"
        )
    }
    if "ck_score_result_esi" not in checks:
        op.create_check_constraint(
            "ck_score_result_esi",
            "score_result",
            "evidence_sufficiency_index IS NULL OR "
            "(evidence_sufficiency_index >= 0 AND evidence_sufficiency_index <= 100)",
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    checks = {
        item.get("name") for item in inspector.get_check_constraints("score_result")
    }
    if "ck_score_result_esi" in checks:
        op.drop_constraint("ck_score_result_esi", "score_result", type_="check")
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("score_result")}
    if "evidence_sufficiency_index" in columns:
        op.drop_column("score_result", "evidence_sufficiency_index")
