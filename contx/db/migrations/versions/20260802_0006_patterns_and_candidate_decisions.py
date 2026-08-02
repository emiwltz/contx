"""Add replayable patterns, richer candidates, and worker decisions.

Revision ID: 20260802_0006
Revises: 20260802_0005
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0006"
down_revision: str | None = "20260802_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patterns",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("window_start", sa.String(32), nullable=False),
        sa.Column("window_end", sa.String(32), nullable=False),
        sa.Column("epistemic_status", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("source_event_ids", sa.JSON(), nullable=False),
        sa.Column("projects", sa.JSON(), nullable=False),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("valid_from", sa.String(32), nullable=False),
        sa.Column("valid_until", sa.String(32), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("processing_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_patterns"),
        sa.UniqueConstraint("idempotency_key", name="uq_patterns_idempotency_key"),
    )
    for column in (
        "type",
        "window_start",
        "window_end",
        "sensitivity",
        "valid_from",
        "valid_until",
        "status",
        "processing_version",
    ):
        op.create_index(f"ix_patterns_{column}", "patterns", [column])

    op.create_table(
        "pattern_events",
        sa.Column("pattern_id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["pattern_id"],
            ["patterns.id"],
            name="fk_pattern_events_pattern_id_patterns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_pattern_events_event_id_events",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("pattern_id", "event_id", name="pk_pattern_events"),
    )
    op.create_table(
        "pattern_processing_runs",
        sa.Column("pattern_id", sa.String(36), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["pattern_id"],
            ["patterns.id"],
            name="fk_pattern_processing_runs_pattern_id_patterns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name=("fk_pattern_processing_runs_processing_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "pattern_id",
            "processing_run_id",
            name="pk_pattern_processing_runs",
        ),
    )
    op.create_table(
        "pattern_builds",
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("source_timeline_run_id", sa.String(36), nullable=False),
        sa.Column("processing_version", sa.String(64), nullable=False),
        sa.Column("comparison_boundary", sa.String(32), nullable=False),
        sa.Column("min_project_events", sa.Integer(), nullable=False),
        sa.Column("resumption_gap_seconds", sa.Integer(), nullable=False),
        sa.Column("change_ratio", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_pattern_builds_processing_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_timeline_run_id"],
            ["processing_runs.id"],
            name="fk_pattern_builds_source_timeline_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("processing_run_id", name="pk_pattern_builds"),
    )
    op.create_index(
        "ix_pattern_builds_source_timeline_run_id",
        "pattern_builds",
        ["source_timeline_run_id"],
    )
    op.create_index(
        "ix_pattern_builds_processing_version",
        "pattern_builds",
        ["processing_version"],
    )

    with op.batch_alter_table("memory_candidates") as batch:
        batch.add_column(sa.Column("utility", sa.Float(), nullable=True))
        batch.add_column(sa.Column("recurrence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("ambiguity", sa.Float(), nullable=True))
        batch.add_column(sa.Column("redundancy", sa.Float(), nullable=True))
        batch.add_column(sa.Column("scoring_version", sa.String(64), nullable=True))
    op.execute("UPDATE memory_candidates SET utility = importance")
    op.execute("UPDATE memory_candidates SET recurrence = 0.0")
    op.execute("UPDATE memory_candidates SET ambiguity = 1.0 - confidence")
    op.execute("UPDATE memory_candidates SET redundancy = 0.0")
    op.execute("UPDATE memory_candidates SET scoring_version = 'legacy-candidate-v1'")
    with op.batch_alter_table("memory_candidates") as batch:
        batch.alter_column("utility", existing_type=sa.Float(), nullable=False)
        batch.alter_column("recurrence", existing_type=sa.Float(), nullable=False)
        batch.alter_column("ambiguity", existing_type=sa.Float(), nullable=False)
        batch.alter_column("redundancy", existing_type=sa.Float(), nullable=False)
        batch.alter_column(
            "scoring_version",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.create_index(
            "ix_memory_candidates_scoring_version",
            ["scoring_version"],
            unique=False,
        )

    op.create_table(
        "candidate_patterns",
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("pattern_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name="fk_candidate_patterns_candidate_id_memory_candidates",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["pattern_id"],
            ["patterns.id"],
            name="fk_candidate_patterns_pattern_id_patterns",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "candidate_id",
            "pattern_id",
            name="pk_candidate_patterns",
        ),
    )
    op.create_table(
        "candidate_processing_runs",
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name=("fk_candidate_processing_runs_candidate_id_memory_candidates"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name=("fk_candidate_processing_runs_processing_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "candidate_id",
            "processing_run_id",
            name="pk_candidate_processing_runs",
        ),
    )
    op.create_table(
        "candidate_builds",
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("source_pattern_run_id", sa.String(36), nullable=False),
        sa.Column("processing_version", sa.String(64), nullable=False),
        sa.Column("scoring_weights", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_candidate_builds_processing_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_pattern_run_id"],
            ["processing_runs.id"],
            name="fk_candidate_builds_source_pattern_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("processing_run_id", name="pk_candidate_builds"),
    )
    op.create_index(
        "ix_candidate_builds_source_pattern_run_id",
        "candidate_builds",
        ["source_pattern_run_id"],
    )
    op.create_index(
        "ix_candidate_builds_processing_version",
        "candidate_builds",
        ["processing_version"],
    )
    op.create_table(
        "candidate_decisions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("acceptance_threshold", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["memory_candidates.id"],
            name="fk_candidate_decisions_candidate_id_memory_candidates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name="fk_candidate_decisions_processing_run_id_processing_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_candidate_decisions"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_candidate_decisions_idempotency_key",
        ),
    )
    for column in (
        "candidate_id",
        "processing_run_id",
        "policy_version",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_candidate_decisions_{column}",
            "candidate_decisions",
            [column],
        )
    op.create_table(
        "candidate_evaluation_builds",
        sa.Column("processing_run_id", sa.String(36), nullable=False),
        sa.Column("source_candidate_run_id", sa.String(36), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("acceptance_threshold", sa.Float(), nullable=False),
        sa.Column("minimum_confidence", sa.Float(), nullable=False),
        sa.Column("maximum_ambiguity", sa.Float(), nullable=False),
        sa.Column("maximum_redundancy", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["processing_run_id"],
            ["processing_runs.id"],
            name=("fk_candidate_evaluation_builds_processing_run_id_processing_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_candidate_run_id"],
            ["processing_runs.id"],
            name=(
                "fk_candidate_evaluation_builds_source_candidate_run_id_processing_runs"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "processing_run_id",
            name="pk_candidate_evaluation_builds",
        ),
    )
    op.create_index(
        "ix_candidate_evaluation_builds_source_candidate_run_id",
        "candidate_evaluation_builds",
        ["source_candidate_run_id"],
    )
    op.create_index(
        "ix_candidate_evaluation_builds_policy_version",
        "candidate_evaluation_builds",
        ["policy_version"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_evaluation_builds_policy_version",
        table_name="candidate_evaluation_builds",
    )
    op.drop_index(
        "ix_candidate_evaluation_builds_source_candidate_run_id",
        table_name="candidate_evaluation_builds",
    )
    op.drop_table("candidate_evaluation_builds")
    for column in (
        "created_at",
        "status",
        "policy_version",
        "processing_run_id",
        "candidate_id",
    ):
        op.drop_index(
            f"ix_candidate_decisions_{column}",
            table_name="candidate_decisions",
        )
    op.drop_table("candidate_decisions")
    op.drop_index(
        "ix_candidate_builds_processing_version",
        table_name="candidate_builds",
    )
    op.drop_index(
        "ix_candidate_builds_source_pattern_run_id",
        table_name="candidate_builds",
    )
    op.drop_table("candidate_builds")
    op.drop_table("candidate_processing_runs")
    op.drop_table("candidate_patterns")
    with op.batch_alter_table("memory_candidates") as batch:
        batch.drop_index("ix_memory_candidates_scoring_version")
        batch.drop_column("scoring_version")
        batch.drop_column("redundancy")
        batch.drop_column("ambiguity")
        batch.drop_column("recurrence")
        batch.drop_column("utility")
    op.drop_index(
        "ix_pattern_builds_processing_version",
        table_name="pattern_builds",
    )
    op.drop_index(
        "ix_pattern_builds_source_timeline_run_id",
        table_name="pattern_builds",
    )
    op.drop_table("pattern_builds")
    op.drop_table("pattern_processing_runs")
    op.drop_table("pattern_events")
    for column in (
        "processing_version",
        "status",
        "valid_until",
        "valid_from",
        "sensitivity",
        "window_end",
        "window_start",
        "type",
    ):
        op.drop_index(f"ix_patterns_{column}", table_name="patterns")
    op.drop_table("patterns")
