"""Add memory correction integrity and local-model provenance.

Revision ID: 20260802_0009
Revises: 20260802_0008
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0009"
down_revision: str | None = "20260802_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_links") as batch:
        batch.create_unique_constraint(
            "uq_memory_links_supersedes_memory_id",
            ["supersedes_memory_id"],
        )
    op.create_table(
        "memory_correction_builds",
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("target_memory_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("model_digest", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("replacement_sha256", sa.String(64), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32), nullable=False),
        sa.Column("wall_duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name=("fk_memory_correction_builds_candidate_id_memory_candidates"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_memory_id"],
            ["memory_links.id"],
            name="fk_memory_correction_builds_target_memory_id_memory_links",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "candidate_id",
            name="pk_memory_correction_builds",
        ),
        sa.UniqueConstraint(
            "target_memory_id",
            name="uq_memory_correction_builds_target_memory_id",
        ),
    )
    op.create_index(
        "ix_memory_correction_builds_model_digest",
        "memory_correction_builds",
        ["model_digest"],
    )
    op.create_index(
        "ix_memory_correction_builds_prompt_version",
        "memory_correction_builds",
        ["prompt_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_memory_correction_builds_prompt_version",
        table_name="memory_correction_builds",
    )
    op.drop_index(
        "ix_memory_correction_builds_model_digest",
        table_name="memory_correction_builds",
    )
    op.drop_table("memory_correction_builds")
    with op.batch_alter_table("memory_links") as batch:
        batch.drop_constraint(
            "uq_memory_links_supersedes_memory_id",
            type_="unique",
        )
