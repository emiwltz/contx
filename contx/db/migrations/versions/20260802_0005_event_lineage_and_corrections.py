"""Add stable event lineage, validity, and append-only corrections.

Revision ID: 20260802_0005
Revises: 20260802_0004
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0005"
down_revision: str | None = "20260802_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("lineage_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("valid_from", sa.String(32), nullable=True))
        batch.add_column(sa.Column("valid_until", sa.String(32), nullable=True))

    op.execute("UPDATE events SET lineage_key = idempotency_key")
    op.execute("UPDATE events SET valid_from = started_at, valid_until = ended_at")

    with op.batch_alter_table("events") as batch:
        batch.alter_column("lineage_key", existing_type=sa.String(64), nullable=False)
        batch.alter_column("valid_from", existing_type=sa.String(32), nullable=False)
        batch.create_index("ix_events_lineage_key", ["lineage_key"], unique=False)
        batch.create_index("ix_events_valid_from", ["valid_from"], unique=False)

    op.create_table(
        "event_corrections",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("event_lineage_key", sa.String(64), nullable=False),
        sa.Column("target_event_id", sa.String(36), nullable=False),
        sa.Column("replacement", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("supersedes_correction_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["target_event_id"],
            ["events.id"],
            name="fk_event_corrections_target_event_id_events",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_correction_id"],
            ["event_corrections.id"],
            name=("fk_event_corrections_supersedes_correction_id_event_corrections"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_event_corrections"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_event_corrections_idempotency_key",
        ),
        sa.UniqueConstraint(
            "supersedes_correction_id",
            name="uq_event_corrections_supersedes_correction_id",
        ),
    )
    op.create_index(
        "ix_event_corrections_event_lineage_key",
        "event_corrections",
        ["event_lineage_key"],
    )
    op.create_index(
        "ix_event_corrections_created_at",
        "event_corrections",
        ["created_at"],
    )
    op.create_table(
        "timeline_builds",
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("processing_version", sa.String(64), nullable=False),
        sa.Column("window_start", sa.String(32), nullable=False),
        sa.Column("window_end", sa.String(32), nullable=False),
        sa.Column("session_gap_seconds", sa.Integer(), nullable=False),
        sa.Column("max_session_duration_seconds", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_timeline_builds_processing_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("processing_run_id", name="pk_timeline_builds"),
    )
    op.create_index(
        "ix_timeline_builds_processing_version",
        "timeline_builds",
        ["processing_version"],
    )
    op.create_index(
        "ix_timeline_builds_window_start",
        "timeline_builds",
        ["window_start"],
    )
    op.create_index(
        "ix_timeline_builds_window_end",
        "timeline_builds",
        ["window_end"],
    )


def downgrade() -> None:
    op.drop_index("ix_timeline_builds_window_end", table_name="timeline_builds")
    op.drop_index("ix_timeline_builds_window_start", table_name="timeline_builds")
    op.drop_index(
        "ix_timeline_builds_processing_version",
        table_name="timeline_builds",
    )
    op.drop_table("timeline_builds")
    op.drop_index(
        "ix_event_corrections_created_at",
        table_name="event_corrections",
    )
    op.drop_index(
        "ix_event_corrections_event_lineage_key",
        table_name="event_corrections",
    )
    op.drop_table("event_corrections")
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_events_valid_from")
        batch.drop_index("ix_events_lineage_key")
        batch.drop_column("valid_until")
        batch.drop_column("valid_from")
        batch.drop_column("lineage_key")
