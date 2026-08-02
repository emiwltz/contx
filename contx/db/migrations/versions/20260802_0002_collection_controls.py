"""Add controlled collection and bounded raw-retention state.

Revision ID: 20260802_0002
Revises: 20260802_0001
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0002"
down_revision: str | None = "20260802_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("observations") as batch:
        batch.add_column(
            sa.Column(
                "activity_state",
                sa.String(32),
                nullable=False,
                server_default="active",
            )
        )
        batch.create_index("ix_observations_activity_state", ["activity_state"])

    op.execute(
        """UPDATE observations
        SET expires_at = strftime('%Y-%m-%dT%H:%M:%fZ', captured_at, '+48 hours')
        WHERE expires_at IS NULL"""
    )
    with op.batch_alter_table("observations") as batch:
        batch.alter_column(
            "expires_at",
            existing_type=sa.String(32),
            nullable=False,
        )

    op.create_table(
        "collection_control",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("paused_at", sa.String(32), nullable=True),
        sa.Column("pause_until", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_collection_control_singleton"),
        sa.PrimaryKeyConstraint("id", name="pk_collection_control"),
    )

    op.create_table(
        "exclusion_rules",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("pattern", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_exclusion_rules"),
        sa.UniqueConstraint(
            "rule_type",
            "pattern",
            "scope",
            name="uq_exclusion_rules_identity",
        ),
    )
    op.create_index("ix_exclusion_rules_enabled", "exclusion_rules", ["enabled"])
    op.create_index("ix_exclusion_rules_rule_type", "exclusion_rules", ["rule_type"])


def downgrade() -> None:
    op.drop_index("ix_exclusion_rules_rule_type", table_name="exclusion_rules")
    op.drop_index("ix_exclusion_rules_enabled", table_name="exclusion_rules")
    op.drop_table("exclusion_rules")
    op.drop_table("collection_control")
    with op.batch_alter_table("observations") as batch:
        batch.alter_column(
            "expires_at",
            existing_type=sa.String(32),
            nullable=True,
        )
        batch.drop_index("ix_observations_activity_state")
        batch.drop_column("activity_state")
