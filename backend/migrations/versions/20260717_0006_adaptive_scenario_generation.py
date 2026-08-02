"""add occupation-adaptive scenario generation

Revision ID: 20260717_0006
Revises: 20260716_0005
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "20260717_0006"
down_revision: str | None = "20260716_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UINT_BIGINT = (
    sa.BigInteger()
    .with_variant(mysql.BIGINT(unsigned=True), "mysql")
    .with_variant(sa.Integer(), "sqlite")
)
UINT_INT = (
    sa.Integer()
    .with_variant(mysql.INTEGER(unsigned=True), "mysql")
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    scenario_columns = {column["name"] for column in inspector.get_columns("scenario")}
    columns = {
        "source_type": sa.Column(
            "source_type", sa.String(length=32), nullable=False, server_default="seeded"
        ),
        "base_scenario_id": sa.Column("base_scenario_id", UINT_BIGINT, nullable=True),
        "occupation_category": sa.Column(
            "occupation_category", sa.String(length=64), nullable=True
        ),
        "occupation_key": sa.Column(
            "occupation_key", sa.String(length=160), nullable=True
        ),
        "generation_prompt_version": sa.Column(
            "generation_prompt_version", sa.String(length=32), nullable=True
        ),
        "generation_model": sa.Column(
            "generation_model", sa.String(length=128), nullable=True
        ),
        "generation_metadata_json": sa.Column(
            "generation_metadata_json", sa.JSON(), nullable=True
        ),
        "is_immutable": sa.Column(
            "is_immutable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    }
    added_base_column = False
    for name, column in columns.items():
        if name not in scenario_columns:
            op.add_column("scenario", column)
            if name == "base_scenario_id":
                added_base_column = True

    inspector = sa.inspect(bind)
    foreign_key_names = {
        item.get("name") for item in inspector.get_foreign_keys("scenario")
    }
    if added_base_column and "fk_scenario_base_scenario" not in foreign_key_names:
        op.create_foreign_key(
            "fk_scenario_base_scenario",
            "scenario",
            "scenario",
            ["base_scenario_id"],
            ["id"],
        )
    index_names = {item["name"] for item in inspector.get_indexes("scenario")}
    if "idx_scenario_occupation_cache" not in index_names:
        op.create_index(
            "idx_scenario_occupation_cache",
            "scenario",
            ["source_type", "occupation_category", "occupation_key", "status"],
        )

    inspector = sa.inspect(bind)
    if inspector.has_table("scenario_generation_job"):
        return
    op.create_table(
        "scenario_generation_job",
        sa.Column("id", UINT_BIGINT, primary_key=True, autoincrement=True),
        sa.Column("session_id", UINT_BIGINT, nullable=False),
        sa.Column("occupation_cache_key", sa.String(length=192), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("base_scenario_id", UINT_BIGINT, nullable=True),
        sa.Column("adapted_scenario_id", UINT_BIGINT, nullable=True),
        sa.Column("draft_json", sa.JSON(), nullable=True),
        sa.Column("reviewed_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("profile_call_count", UINT_INT, nullable=False, server_default="0"),
        sa.Column("design_call_count", UINT_INT, nullable=False, server_default="0"),
        sa.Column("adaptation_call_count", UINT_INT, nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["assessment_session.id"]),
        sa.ForeignKeyConstraint(["base_scenario_id"], ["scenario.id"]),
        sa.ForeignKeyConstraint(["adapted_scenario_id"], ["scenario.id"]),
        sa.UniqueConstraint("session_id", name="uk_scenario_generation_session"),
        sa.CheckConstraint("profile_call_count >= 0", name="ck_profile_call_count"),
        sa.CheckConstraint("design_call_count >= 0", name="ck_design_call_count"),
        sa.CheckConstraint("adaptation_call_count >= 0", name="ck_adaptation_call_count"),
    )
    op.create_index(
        "idx_scenario_generation_status",
        "scenario_generation_job",
        ["status", "locked_at"],
    )
    op.create_index(
        "idx_scenario_generation_cache",
        "scenario_generation_job",
        ["occupation_cache_key", "status"],
    )


def downgrade() -> None:
    op.drop_index("idx_scenario_generation_cache", table_name="scenario_generation_job")
    op.drop_index("idx_scenario_generation_status", table_name="scenario_generation_job")
    op.drop_table("scenario_generation_job")
    op.drop_index("idx_scenario_occupation_cache", table_name="scenario")
    op.drop_constraint("fk_scenario_base_scenario", "scenario", type_="foreignkey")
    op.drop_column("scenario", "is_immutable")
    op.drop_column("scenario", "generation_metadata_json")
    op.drop_column("scenario", "generation_model")
    op.drop_column("scenario", "generation_prompt_version")
    op.drop_column("scenario", "occupation_key")
    op.drop_column("scenario", "occupation_category")
    op.drop_column("scenario", "base_scenario_id")
    op.drop_column("scenario", "source_type")
