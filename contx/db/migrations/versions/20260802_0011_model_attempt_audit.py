"""Add content-free immutable local-model attempt outcomes.

Revision ID: 20260802_0011
Revises: 20260802_0010
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0011"
down_revision: str | None = "20260802_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_attempts",
        sa.Column("transformation_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("invocation", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_model_attempts_processing_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transformation_id"],
            ["model_transformations.id"],
            name="fk_model_attempts_transformation_id_model_transformations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "transformation_id",
            "attempt_number",
            name="pk_model_attempts",
        ),
        sa.UniqueConstraint(
            "transformation_id",
            "processing_run_id",
            name="uq_model_attempts_transformation_run",
        ),
    )
    for column in (
        "processing_run_id",
        "invocation",
        "status",
        "error_code",
        "started_at",
        "ended_at",
    ):
        op.create_index(
            f"ix_model_attempts_{column}",
            "model_attempts",
            [column],
        )


def downgrade() -> None:
    for column in (
        "ended_at",
        "started_at",
        "error_code",
        "status",
        "invocation",
        "processing_run_id",
    ):
        op.drop_index(f"ix_model_attempts_{column}", table_name="model_attempts")
    op.drop_table("model_attempts")
