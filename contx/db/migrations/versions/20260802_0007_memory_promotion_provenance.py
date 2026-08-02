"""Link accepted J4 decisions to final-memory promotion runs.

Revision ID: 20260802_0007
Revises: 20260802_0006
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0007"
down_revision: str | None = "20260802_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_links") as batch:
        batch.add_column(
            sa.Column("candidate_decision_id", sa.String(36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_memory_links_candidate_decision_id_candidate_decisions",
            "candidate_decisions",
            ["candidate_decision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_unique_constraint(
            "uq_memory_links_candidate_decision_id",
            ["candidate_decision_id"],
        )

    op.create_table(
        "memory_link_processing_runs",
        sa.Column("memory_link_id", sa.String(36), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_link_id"],
            ["memory_links.id"],
            name="fk_memory_link_processing_runs_memory_link_id_memory_links",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name=("fk_memory_link_processing_runs_processing_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "memory_link_id",
            "processing_run_id",
            name="pk_memory_link_processing_runs",
        ),
    )
    op.create_table(
        "memory_promotion_builds",
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("source_evaluation_run_id", sa.String(36), nullable=False),
        sa.Column("processing_version", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_memory_promotion_builds_processing_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_evaluation_run_id"],
            ["processing_runs.id"],
            name=(
                "fk_memory_promotion_builds_source_evaluation_run_id_processing_runs"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "processing_run_id",
            name="pk_memory_promotion_builds",
        ),
    )
    op.create_index(
        "ix_memory_promotion_builds_source_evaluation_run_id",
        "memory_promotion_builds",
        ["source_evaluation_run_id"],
    )
    op.create_index(
        "ix_memory_promotion_builds_processing_version",
        "memory_promotion_builds",
        ["processing_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_promotion_builds_processing_version",
        table_name="memory_promotion_builds",
    )
    op.drop_index(
        "ix_memory_promotion_builds_source_evaluation_run_id",
        table_name="memory_promotion_builds",
    )
    op.drop_table("memory_promotion_builds")
    op.drop_table("memory_link_processing_runs")
    with op.batch_alter_table("memory_links") as batch:
        batch.drop_constraint(
            "uq_memory_links_candidate_decision_id",
            type_="unique",
        )
        batch.drop_constraint(
            "fk_memory_links_candidate_decision_id_candidate_decisions",
            type_="foreignkey",
        )
        batch.drop_column("candidate_decision_id")
