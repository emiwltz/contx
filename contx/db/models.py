"""SQLAlchemy persistence models for the first vertical slice."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from contx.db.base import Base

Identifier = str
UtcTimestamp = str


class ObservationModel(Base):
    """One persisted source observation."""

    __tablename__ = "observations"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_type: Mapped[str] = mapped_column(String(64), index=True)
    activity_state: Mapped[str] = mapped_column(String(32), index=True)
    captured_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    started_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    ended_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    app_name: Mapped[str | None] = mapped_column(String(255))
    app_bundle_id: Mapped[str | None] = mapped_column(String(255))
    window_title: Mapped[str | None] = mapped_column(Text)
    artifact_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(128))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(255))
    processing_status: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class EventModel(Base):
    """A bounded interpretation backed by source observations."""

    __tablename__ = "events"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    lineage_key: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON)
    started_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    ended_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    valid_from: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    valid_until: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    epistemic_status: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    sensitivity: Mapped[str] = mapped_column(String(32))
    projects: Mapped[list[str]] = mapped_column(JSON)
    entities: Mapped[list[str]] = mapped_column(JSON)
    source_observation_ids: Mapped[list[str]] = mapped_column(JSON)
    processing_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    updated_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class EventObservationModel(Base):
    """Foreign-key-backed event provenance."""

    __tablename__ = "event_observations"

    event_id: Mapped[Identifier] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    observation_id: Mapped[Identifier] = mapped_column(
        String(36), ForeignKey("observations.id", ondelete="RESTRICT"), primary_key=True
    )


class EventModelTransformationModel(Base):
    """Link an enriched event to the local-model evidence that produced it."""

    __tablename__ = "event_model_transformations"

    event_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    transformation_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("model_transformations.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class EventProcessingRunModel(Base):
    """Link an enriched event to its deterministic builder execution."""

    __tablename__ = "event_processing_runs"

    event_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class EventCorrectionModel(Base):
    """Append-only corrected semantics for a stable event lineage."""

    __tablename__ = "event_corrections"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    event_lineage_key: Mapped[str] = mapped_column(String(64), index=True)
    target_event_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="RESTRICT"),
    )
    replacement: Mapped[dict[str, Any]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    supersedes_correction_id: Mapped[Identifier | None] = mapped_column(
        String(36),
        ForeignKey("event_corrections.id", ondelete="RESTRICT"),
        unique=True,
    )
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)


class TimelineBuildModel(Base):
    """Content-free parameters for one reproducible timeline run."""

    __tablename__ = "timeline_builds"

    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    processing_version: Mapped[str] = mapped_column(String(64), index=True)
    window_start: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    window_end: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    session_gap_seconds: Mapped[int] = mapped_column(Integer)
    max_session_duration_seconds: Mapped[int] = mapped_column(Integer)


class PatternModel(Base):
    """One multi-event temporal inference."""

    __tablename__ = "patterns"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    window_start: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    window_end: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    epistemic_status: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    sensitivity: Mapped[str] = mapped_column(String(32), index=True)
    evidence_count: Mapped[int] = mapped_column(Integer)
    source_event_ids: Mapped[list[str]] = mapped_column(JSON)
    projects: Mapped[list[str]] = mapped_column(JSON)
    entities: Mapped[list[str]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    valid_from: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    valid_until: Mapped[UtcTimestamp | None] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    processing_version: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class PatternEventModel(Base):
    """Foreign-key-backed event evidence for one pattern."""

    __tablename__ = "pattern_events"

    pattern_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("patterns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("events.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class PatternProcessingRunModel(Base):
    """Link one pattern to the processing run that selected it."""

    __tablename__ = "pattern_processing_runs"

    pattern_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("patterns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class PatternBuildModel(Base):
    """Content-free policy inputs for one pattern replay."""

    __tablename__ = "pattern_builds"

    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_timeline_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    processing_version: Mapped[str] = mapped_column(String(64), index=True)
    comparison_boundary: Mapped[UtcTimestamp] = mapped_column(String(32))
    min_project_events: Mapped[int] = mapped_column(Integer)
    resumption_gap_seconds: Mapped[int] = mapped_column(Integer)
    change_ratio: Mapped[float] = mapped_column(Float)


class MemoryCandidateModel(Base):
    """A memory proposal with an explicit worker state."""

    __tablename__ = "memory_candidates"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    source_ids: Mapped[list[str]] = mapped_column(JSON)
    utility: Mapped[float] = mapped_column(Float)
    importance: Mapped[float] = mapped_column(Float)
    durability: Mapped[float] = mapped_column(Float)
    novelty: Mapped[float] = mapped_column(Float)
    recurrence: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    ambiguity: Mapped[float] = mapped_column(Float)
    redundancy: Mapped[float] = mapped_column(Float)
    sensitivity: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float, index=True)
    scoring_version: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    processed_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))


class CandidateEventModel(Base):
    """Foreign-key-backed candidate provenance."""

    __tablename__ = "candidate_events"

    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[Identifier] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="RESTRICT"), primary_key=True
    )


class CandidatePatternModel(Base):
    """Foreign-key-backed pattern provenance for one candidate."""

    __tablename__ = "candidate_patterns"

    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pattern_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("patterns.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class CandidateProcessingRunModel(Base):
    """Link one candidate to the run that produced or selected it."""

    __tablename__ = "candidate_processing_runs"

    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class CandidateBuildModel(Base):
    """Content-free inputs for one reproducible candidate build."""

    __tablename__ = "candidate_builds"

    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_pattern_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    processing_version: Mapped[str] = mapped_column(String(64), index=True)
    scoring_weights: Mapped[dict[str, float]] = mapped_column(JSON)


class CandidateDecisionModel(Base):
    """Append-only worker evaluation under one explicit policy."""

    __tablename__ = "candidate_decisions"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="RESTRICT"),
        index=True,
    )
    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(64), index=True)
    acceptance_threshold: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)


class CandidateEvaluationBuildModel(Base):
    """Content-free policy inputs for one candidate evaluation replay."""

    __tablename__ = "candidate_evaluation_builds"

    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_candidate_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    policy_version: Mapped[str] = mapped_column(String(64), index=True)
    acceptance_threshold: Mapped[float] = mapped_column(Float)
    minimum_confidence: Mapped[float] = mapped_column(Float)
    maximum_ambiguity: Mapped[float] = mapped_column(Float)
    maximum_redundancy: Mapped[float] = mapped_column(Float)


class AgentProposalModel(Base):
    """An agent submission held outside final memory until adoption."""

    __tablename__ = "agent_proposals"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    agent_id: Mapped[str] = mapped_column(String(120), index=True)
    agent_role: Mapped[str] = mapped_column(String(32), index=True)
    text: Mapped[str] = mapped_column(Text)
    proposal_type: Mapped[str] = mapped_column(String(32), index=True)
    reference_type: Mapped[str | None] = mapped_column(String(32), index=True)
    reference_id: Mapped[Identifier | None] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    processed_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))


class AgentProposalAdoptionBuildModel(Base):
    """Content-free local-model audit for explicit proposal adoption."""

    __tablename__ = "agent_proposal_adoption_builds"

    proposal_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("agent_proposals.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    candidate_id: Mapped[Identifier | None] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="RESTRICT"),
        unique=True,
    )
    provider: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    model_digest: Mapped[str] = mapped_column(String(128), index=True)
    prompt_version: Mapped[str] = mapped_column(String(64), index=True)
    output_schema_version: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32), index=True)
    reason_code: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    reference_sha256: Mapped[str] = mapped_column(String(64))
    active_memory_sha256: Mapped[str] = mapped_column(String(64))
    active_memory_count: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    ended_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    wall_duration_ms: Mapped[int] = mapped_column(Integer)


class MemoryLinkModel(Base):
    """Link durable memory identity to its accepted candidate."""

    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint(
            "supersedes_memory_id",
            name="uq_memory_links_supersedes_memory_id",
        ),
    )

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    memory_backend_id: Mapped[str] = mapped_column(String(255), unique=True)
    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="RESTRICT"),
        unique=True,
    )
    candidate_decision_id: Mapped[Identifier | None] = mapped_column(
        String(36),
        ForeignKey("candidate_decisions.id", ondelete="RESTRICT"),
        unique=True,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), index=True)
    supersedes_memory_id: Mapped[Identifier | None] = mapped_column(
        String(36), ForeignKey("memory_links.id", ondelete="RESTRICT")
    )
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class MemoryLinkProcessingRunModel(Base):
    """Link a durable memory to each promotion run that selected it."""

    __tablename__ = "memory_link_processing_runs"

    memory_link_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_links.id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class MemoryCorrectionBuildModel(Base):
    """Content-free local-model provenance for one memory correction."""

    __tablename__ = "memory_correction_builds"

    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    target_memory_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_links.id", ondelete="RESTRICT"),
        unique=True,
    )
    provider: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    model_digest: Mapped[str] = mapped_column(String(128), index=True)
    prompt_version: Mapped[str] = mapped_column(String(64), index=True)
    output_schema_version: Mapped[str] = mapped_column(String(64))
    replacement_sha256: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    ended_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    wall_duration_ms: Mapped[int] = mapped_column(Integer)


class MemoryPromotionBuildModel(Base):
    """Content-free source selection for one promotion replay."""

    __tablename__ = "memory_promotion_builds"

    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_evaluation_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    processing_version: Mapped[str] = mapped_column(String(64), index=True)


class ProcessingRunModel(Base):
    """One observable pipeline execution without private error payloads."""

    __tablename__ = "processing_runs"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    pipeline: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    ended_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    input_count: Mapped[int] = mapped_column(Integer, default=0)
    output_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(String(255))


class ModelTransformationModel(Base):
    """Replayable local-model work and its validated structured result."""

    __tablename__ = "model_transformations"
    __table_args__ = (
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_model_transformations_attempt_count",
        ),
    )

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_observation_ids: Mapped[list[str]] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[str] = mapped_column(String(255))
    configured_model: Mapped[str] = mapped_column(String(255))
    runtime_version: Mapped[str | None] = mapped_column(String(64))
    resolved_model: Mapped[str | None] = mapped_column(String(255))
    model_digest: Mapped[str | None] = mapped_column(String(128), index=True)
    prompt_version: Mapped[str] = mapped_column(String(64))
    output_schema_version: Mapped[str] = mapped_column(String(64))
    image_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    interpretation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sensitivity: Mapped[str | None] = mapped_column(String(32), index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    ended_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    wall_duration_ms: Mapped[int | None] = mapped_column(Integer)
    runtime_duration_ms: Mapped[int | None] = mapped_column(Integer)
    load_duration_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_eval_count: Mapped[int | None] = mapped_column(Integer)
    eval_count: Mapped[int | None] = mapped_column(Integer)
    next_attempt_at: Mapped[UtcTimestamp | None] = mapped_column(
        String(32),
        index=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    updated_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class ModelTransformationObservationModel(Base):
    """Foreign-key-backed observation provenance for model work."""

    __tablename__ = "model_transformation_observations"

    transformation_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("model_transformations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    observation_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("observations.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class ModelTransformationRunModel(Base):
    """Link every model attempt to its observable processing run."""

    __tablename__ = "model_transformation_runs"

    transformation_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("model_transformations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    processing_run_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("processing_runs.id", ondelete="RESTRICT"),
        primary_key=True,
    )


class CollectionControlModel(Base):
    """Singleton state controlling whether collection may run."""

    __tablename__ = "collection_control"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_collection_control_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paused_at: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    pause_until: Mapped[UtcTimestamp | None] = mapped_column(String(32))
    updated_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class ExclusionRuleModel(Base):
    """One pre-capture application, window, or situation rule."""

    __tablename__ = "exclusion_rules"
    __table_args__ = (
        UniqueConstraint(
            "rule_type",
            "pattern",
            "scope",
            name="uq_exclusion_rules_identity",
        ),
    )

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(32), index=True)
    pattern: Mapped[str] = mapped_column(String(255))
    scope: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))
    updated_at: Mapped[UtcTimestamp] = mapped_column(String(32))
