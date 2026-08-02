"""Link model-derived events to transformations and processing runs.

Revision ID: 20260802_0004
Revises: 20260802_0003
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0004"
down_revision: str | None = "20260802_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_model_transformations",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("transformation_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_model_transformations_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transformation_id"],
            ["model_transformations.id"],
            name=(
                "fk_event_model_transformations_transformation_id_model_transformations"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "event_id",
            "transformation_id",
            name="pk_event_model_transformations",
        ),
    )
    op.create_table(
        "event_processing_runs",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_processing_runs_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name=("fk_event_processing_runs_processing_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "event_id",
            "processing_run_id",
            name="pk_event_processing_runs",
        ),
    )


def downgrade() -> None:
    op.drop_table("event_processing_runs")
    op.drop_table("event_model_transformations")
