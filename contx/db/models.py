"""SQLAlchemy persistence models for the first vertical slice."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
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
    expires_at: Mapped[UtcTimestamp | None] = mapped_column(String(32), index=True)
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))


class EventModel(Base):
    """A bounded interpretation backed by source observations."""

    __tablename__ = "events"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[dict[str, Any]] = mapped_column(JSON)
    started_at: Mapped[UtcTimestamp] = mapped_column(String(32), index=True)
    ended_at: Mapped[UtcTimestamp] = mapped_column(String(32))
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


class MemoryCandidateModel(Base):
    """A memory proposal with an explicit worker state."""

    __tablename__ = "memory_candidates"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    text: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(64))
    source_ids: Mapped[list[str]] = mapped_column(JSON)
    importance: Mapped[float] = mapped_column(Float)
    durability: Mapped[float] = mapped_column(Float)
    novelty: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    sensitivity: Mapped[str] = mapped_column(String(32))
    score: Mapped[float] = mapped_column(Float, index=True)
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


class MemoryLinkModel(Base):
    """Link durable memory identity to its accepted candidate."""

    __tablename__ = "memory_links"

    id: Mapped[Identifier] = mapped_column(String(36), primary_key=True)
    memory_backend_id: Mapped[str] = mapped_column(String(255), unique=True)
    candidate_id: Mapped[Identifier] = mapped_column(
        String(36),
        ForeignKey("memory_candidates.id", ondelete="RESTRICT"),
        unique=True,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), index=True)
    supersedes_memory_id: Mapped[Identifier | None] = mapped_column(
        String(36), ForeignKey("memory_links.id", ondelete="RESTRICT")
    )
    created_at: Mapped[UtcTimestamp] = mapped_column(String(32))


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
