"""widen prompt template version for immutable runtime prompt identifiers

Revision ID: 20260730_0011
Revises: 20260728_0010
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260730_0011"
down_revision: str | None = "20260728_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        item["name"]: item for item in inspector.get_columns("prompt_template")
    }
    version_column = columns.get("version")
    if version_column is None:
        raise RuntimeError("prompt_template.version is required before this migration")

    current_length = getattr(version_column["type"], "length", None)
    if current_length is None or current_length < 64:
        with op.batch_alter_table("prompt_template") as batch_op:
            batch_op.alter_column(
                "version",
                existing_type=version_column["type"],
                type_=sa.String(length=64),
                existing_nullable=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    too_long_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM prompt_template "
            "WHERE CHAR_LENGTH(version) > 32"
        )
    ).scalar_one()
    if too_long_count:
        raise RuntimeError(
            "cannot narrow prompt_template.version while values exceed 32 characters"
        )

    inspector = sa.inspect(bind)
    columns = {
        item["name"]: item for item in inspector.get_columns("prompt_template")
    }
    version_column = columns.get("version")
    if version_column is None:
        raise RuntimeError("prompt_template.version is missing")
    current_length = getattr(version_column["type"], "length", None)
    if current_length is None or current_length > 32:
        with op.batch_alter_table("prompt_template") as batch_op:
            batch_op.alter_column(
                "version",
                existing_type=version_column["type"],
                type_=sa.String(length=32),
                existing_nullable=False,
            )
