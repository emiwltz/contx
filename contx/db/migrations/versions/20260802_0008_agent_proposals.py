"""Add the validated agent-proposal inbox outside final memory.

Revision ID: 20260802_0008
Revises: 20260802_0007
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0008"
down_revision: str | None = "20260802_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_proposals",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(120), nullable=False),
        sa.Column("agent_role", sa.String(32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("proposal_type", sa.String(32), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=True),
        sa.Column("reference_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("processed_at", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_proposals"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_agent_proposals_idempotency_key",
        ),
    )
    for column in (
        "agent_id",
        "agent_role",
        "proposal_type",
        "reference_type",
        "reference_id",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_agent_proposals_{column}",
            "agent_proposals",
            [column],
        )


def downgrade() -> None:
    for column in (
        "created_at",
        "status",
        "reference_id",
        "reference_type",
        "proposal_type",
        "agent_role",
        "agent_id",
    ):
        op.drop_index(
            f"ix_agent_proposals_{column}",
            table_name="agent_proposals",
        )
    op.drop_table("agent_proposals")
