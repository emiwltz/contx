"""Add audited local-model adoption for agent proposals.

Revision ID: 20260802_0010
Revises: 20260802_0009
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0010"
down_revision: str | None = "20260802_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_proposal_adoption_builds",
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("model_digest", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("output_schema_version", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reference_sha256", sa.String(64), nullable=False),
        sa.Column("active_memory_sha256", sa.String(64), nullable=False),
        sa.Column("active_memory_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32), nullable=False),
        sa.Column("wall_duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["agent_proposals.id"],
            name=(
                "fk_agent_proposal_adoption_builds_proposal_id_agent_proposals"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name=(
                "fk_agent_proposal_adoption_builds_candidate_id_memory_candidates"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "proposal_id",
            name="pk_agent_proposal_adoption_builds",
        ),
        sa.UniqueConstraint(
            "candidate_id",
            name="uq_agent_proposal_adoption_builds_candidate_id",
        ),
    )
    for column in ("model_digest", "prompt_version", "decision", "reason_code"):
        op.create_index(
            f"ix_agent_proposal_adoption_builds_{column}",
            "agent_proposal_adoption_builds",
            [column],
        )


def downgrade() -> None:
    for column in ("reason_code", "decision", "prompt_version", "model_digest"):
        op.drop_index(
            f"ix_agent_proposal_adoption_builds_{column}",
            table_name="agent_proposal_adoption_builds",
        )
    op.drop_table("agent_proposal_adoption_builds")
