"""Add replayable local-model transformations.

Revision ID: 20260802_0003
Revises: 20260802_0002
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0003"
down_revision: str | None = "20260802_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_transformations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("source_observation_ids", sa.JSON(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("configured_model", sa.String(255), nullable=False),
        sa.Column("runtime_version", sa.String(64), nullable=True),
        sa.Column("resolved_model", sa.String(255), nullable=True),
        sa.Column("model_digest", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("interpretation", sa.JSON(), nullable=True),
        sa.Column("sensitivity", sa.String(32), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=True),
        sa.Column("ended_at", sa.String(32), nullable=True),
        sa.Column("wall_duration_ms", sa.Integer(), nullable=True),
        sa.Column("runtime_duration_ms", sa.Integer(), nullable=True),
        sa.Column("load_duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_eval_count", sa.Integer(), nullable=True),
        sa.Column("eval_count", sa.Integer(), nullable=True),
        sa.Column("next_attempt_at", sa.String(32), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_model_transformations_attempt_count",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_transformations"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_model_transformations_idempotency_key",
        ),
    )
    op.create_index(
        "ix_model_transformations_created_at",
        "model_transformations",
        ["created_at"],
    )
    op.create_index(
        "ix_model_transformations_model_digest",
        "model_transformations",
        ["model_digest"],
    )
    op.create_index(
        "ix_model_transformations_next_attempt_at",
        "model_transformations",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_model_transformations_sensitivity",
        "model_transformations",
        ["sensitivity"],
    )
    op.create_index(
        "ix_model_transformations_status",
        "model_transformations",
        ["status"],
    )

    op.create_table(
        "model_transformation_observations",
        sa.Column("transformation_id", sa.String(36), nullable=False),
        sa.Column("observation_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["transformation_id"],
            ["model_transformations.id"],
            name=(
                "fk_model_transformation_observations_transformation_id_"
                "model_transformations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name=("fk_model_transformation_observations_observation_id_observations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "transformation_id",
            "observation_id",
            name="pk_model_transformation_observations",
        ),
    )

    op.create_table(
        "model_transformation_runs",
        sa.Column("transformation_id", sa.String(36), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name=("fk_model_transformation_runs_processing_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transformation_id"],
            ["model_transformations.id"],
            name=(
                "fk_model_transformation_runs_transformation_id_model_transformations"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "transformation_id",
            "processing_run_id",
            name="pk_model_transformation_runs",
        ),
    )


def downgrade() -> None:
    op.drop_table("model_transformation_runs")
    op.drop_table("model_transformation_observations")
    op.drop_index(
        "ix_model_transformations_status",
        table_name="model_transformations",
    )
    op.drop_index(
        "ix_model_transformations_sensitivity",
        table_name="model_transformations",
    )
    op.drop_index(
        "ix_model_transformations_next_attempt_at",
        table_name="model_transformations",
    )
    op.drop_index(
        "ix_model_transformations_model_digest",
        table_name="model_transformations",
    )
    op.drop_index(
        "ix_model_transformations_created_at",
        table_name="model_transformations",
    )
    op.drop_table("model_transformations")
