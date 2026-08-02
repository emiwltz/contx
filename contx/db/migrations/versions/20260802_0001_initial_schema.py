"""Create the v0.0.1 provenance schema.

Revision ID: 20260802_0001
Revises: None
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=True),
        sa.Column("ended_at", sa.String(32), nullable=True),
        sa.Column("app_name", sa.String(255), nullable=True),
        sa.Column("app_bundle_id", sa.String(255), nullable=True),
        sa.Column("window_title", sa.Text(), nullable=True),
        sa.Column("artifact_path", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("perceptual_hash", sa.String(128), nullable=True),
        sa.Column("excluded", sa.Boolean(), nullable=False),
        sa.Column("exclusion_reason", sa.String(255), nullable=True),
        sa.Column("processing_status", sa.String(32), nullable=False),
        sa.Column("expires_at", sa.String(32), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_observations"),
        sa.UniqueConstraint("idempotency_key", name="uq_observations_idempotency_key"),
    )
    op.create_index("ix_observations_captured_at", "observations", ["captured_at"])
    op.create_index("ix_observations_expires_at", "observations", ["expires_at"])
    op.create_index(
        "ix_observations_processing_status", "observations", ["processing_status"]
    )
    op.create_index("ix_observations_source_type", "observations", ["source_type"])

    op.create_table(
        "events",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32), nullable=False),
        sa.Column("epistemic_status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("projects", sa.JSON(), nullable=False),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("source_observation_ids", sa.JSON(), nullable=False),
        sa.Column("processing_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.UniqueConstraint("idempotency_key", name="uq_events_idempotency_key"),
    )
    op.create_index("ix_events_started_at", "events", ["started_at"])
    op.create_index("ix_events_type", "events", ["type"])

    op.create_table(
        "event_observations",
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("observation_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_observations_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["observations.id"],
            name="fk_event_observations_observation_id_observations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "event_id", "observation_id", name="pk_event_observations"
        ),
    )

    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("durability", sa.Float(), nullable=False),
        sa.Column("novelty", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rejection_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("processed_at", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_memory_candidates"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_memory_candidates_idempotency_key"
        ),
    )
    op.create_index("ix_memory_candidates_score", "memory_candidates", ["score"])
    op.create_index("ix_memory_candidates_status", "memory_candidates", ["status"])

    op.create_table(
        "candidate_events",
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name="fk_candidate_events_candidate_id_memory_candidates",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_candidate_events_event_id_events",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("candidate_id", "event_id", name="pk_candidate_events"),
    )

    op.create_table(
        "memory_links",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("memory_backend_id", sa.String(255), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("supersedes_memory_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name="fk_memory_links_candidate_id_memory_candidates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_memory_id"],
            ["memory_links.id"],
            name="fk_memory_links_supersedes_memory_id_memory_links",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memory_links"),
        sa.UniqueConstraint("candidate_id", name="uq_memory_links_candidate_id"),
        sa.UniqueConstraint(
            "memory_backend_id", name="uq_memory_links_memory_backend_id"
        ),
    )
    op.create_index("ix_memory_links_status", "memory_links", ["status"])

    op.create_table(
        "processing_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("pipeline", sa.String(64), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("ended_at", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("input_count", sa.Integer(), nullable=False),
        sa.Column("output_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.String(255), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_processing_runs"),
    )
    op.create_index("ix_processing_runs_pipeline", "processing_runs", ["pipeline"])
    op.create_index("ix_processing_runs_started_at", "processing_runs", ["started_at"])
    op.create_index("ix_processing_runs_status", "processing_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_processing_runs_status", table_name="processing_runs")
    op.drop_index("ix_processing_runs_started_at", table_name="processing_runs")
    op.drop_index("ix_processing_runs_pipeline", table_name="processing_runs")
    op.drop_table("processing_runs")
    op.drop_index("ix_memory_links_status", table_name="memory_links")
    op.drop_table("memory_links")
    op.drop_table("candidate_events")
    op.drop_index("ix_memory_candidates_status", table_name="memory_candidates")
    op.drop_index("ix_memory_candidates_score", table_name="memory_candidates")
    op.drop_table("memory_candidates")
    op.drop_table("event_observations")
    op.drop_index("ix_events_type", table_name="events")
    op.drop_index("ix_events_started_at", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_observations_source_type", table_name="observations")
    op.drop_index("ix_observations_processing_status", table_name="observations")
    op.drop_index("ix_observations_expires_at", table_name="observations")
    op.drop_index("ix_observations_captured_at", table_name="observations")
    op.drop_table("observations")
