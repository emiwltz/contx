"""Read-only application views for the local API and user interface."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy import Engine

from contx.collectors.macos import CollectionCapability, detect_collection_capabilities
from contx.db import session_scope
from contx.db.repositories import (
    AgentProposalAdoptionRepository,
    AgentProposalRepository,
    CandidateDecisionRepository,
    EventCorrectionRepository,
    MemoryCorrectionRepository,
    ModelEventRepository,
    ModelTransformationRepository,
    PatternRepository,
    PipelineRepository,
)
from contx.errors import ConfigurationError
from contx.events import MODEL_EVENT_PROCESSING_VERSION
from contx.model_provider import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    LocalModelRuntimeStatus,
    ModelProvider,
    ModelTransformation,
)
from contx.models import (
    AgentProposal,
    AgentProposalAdoptionBuild,
    CandidateDecision,
    Clock,
    CollectionControl,
    Event,
    EventCorrection,
    MemoryCandidate,
    MemoryCorrectionBuild,
    MemoryLink,
    Observation,
    Pattern,
    ProcessingRun,
)
from contx.raw_store import RawStore
from contx.settings import AppSettings, RuntimePaths

if TYPE_CHECKING:
    from contx.daemon.lease import DaemonLeaseStatus

DEFAULT_INSPECTION_LIMIT = 100


class _StatusProvider(Protocol):
    def status(self) -> LocalModelRuntimeStatus: ...


@dataclass(frozen=True, slots=True)
class OperationalIssue:
    code: str
    summary: str
    occurred_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RawArtifactInspection:
    observation_id: UUID
    source_type: str
    captured_at: datetime
    expires_at: datetime
    size_bytes: int
    available: bool


@dataclass(frozen=True, slots=True)
class OverviewInspection:
    collection: CollectionControl
    daemon: DaemonLeaseStatus
    capabilities: tuple[CollectionCapability, ...]
    model: LocalModelRuntimeStatus
    counts: dict[str, int]
    raw_usage_bytes: int
    memory_usage_bytes: int
    model_backlog: int
    model_event_backlog: int
    abandoned_transformations: int
    last_processing_run: ProcessingRun | None
    issues: tuple[OperationalIssue, ...]


@dataclass(frozen=True, slots=True)
class ActivityInspection:
    observations: tuple[Observation, ...]
    events: tuple[Event, ...]
    event_corrections: tuple[EventCorrection, ...]


@dataclass(frozen=True, slots=True)
class MemoryInspection:
    candidates: tuple[MemoryCandidate, ...]
    records: tuple[tuple[MemoryLink, MemoryCandidate], ...]
    corrections: tuple[MemoryCorrectionBuild, ...]


@dataclass(frozen=True, slots=True)
class PrivacyInspection:
    raw_artifacts: tuple[RawArtifactInspection, ...]
    transformations: tuple[ModelTransformation, ...]
    candidate_decisions: tuple[CandidateDecision, ...]


@dataclass(frozen=True, slots=True)
class AgentInspection:
    proposals: tuple[AgentProposal, ...]
    validations: tuple[AgentProposalAdoptionBuild, ...]


class InspectionService:
    """Expose bounded, explicit views without adding semantic interpretation."""

    def __init__(
        self,
        *,
        engine: Engine,
        paths: RuntimePaths,
        settings: AppSettings,
        raw_store: RawStore,
        model_provider: ModelProvider | _StatusProvider,
        clock: Clock,
    ) -> None:
        self._engine = engine
        self._paths = paths
        self._settings = settings
        self._raw_store = raw_store
        self._model_provider = model_provider
        self._clock = clock

    def overview(self) -> OverviewInspection:
        from contx.daemon.lease import probe_daemon_lease

        now = self._clock.now()
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            runs = pipeline.processing_runs(limit=20)
            counts = pipeline.record_counts()
            transformations = ModelTransformationRepository(database_session)
            model_backlog = transformations.backlog_count(
                provider="ollama",
                endpoint=self._settings.model.endpoint,
                configured_model=self._settings.model.model_name,
                prompt_version=PROMPT_VERSION,
                output_schema_version=OUTPUT_SCHEMA_VERSION,
            ) + transformations.unqueued_screenshot_count(
                at=now,
                provider="ollama",
                endpoint=self._settings.model.endpoint,
                configured_model=self._settings.model.model_name,
                prompt_version=PROMPT_VERSION,
                output_schema_version=OUTPUT_SCHEMA_VERSION,
            )
            abandoned = transformations.abandoned_count()
            event_backlog = ModelEventRepository(database_session).pending_count(
                processing_version=MODEL_EVENT_PROCESSING_VERSION
            )

        from contx.collection import CollectionControlService

        controls = CollectionControlService(engine=self._engine, clock=self._clock)
        controls.initialize()
        model_status = self._model_provider.status()
        issues = _operational_issues(
            runs,
            model=model_status,
            model_backlog=model_backlog,
            abandoned_transformations=abandoned,
        )
        daemon = probe_daemon_lease(self._paths.daemon_lock)
        memory_usage = _safe_directory_usage(
            self._paths.memory
        ) + _safe_directory_usage(self._paths.memory_projection)
        return OverviewInspection(
            collection=controls.control(),
            daemon=daemon,
            capabilities=detect_collection_capabilities(self._settings.collection),
            model=model_status,
            counts=counts,
            raw_usage_bytes=self._raw_store.usage_bytes(),
            memory_usage_bytes=memory_usage,
            model_backlog=model_backlog,
            model_event_backlog=event_backlog,
            abandoned_transformations=abandoned,
            last_processing_run=runs[0] if runs else None,
            issues=issues,
        )

    def activity(self, *, limit: int = DEFAULT_INSPECTION_LIMIT) -> ActivityInspection:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            return ActivityInspection(
                observations=pipeline.observations(limit=limit),
                events=pipeline.events(limit=limit),
                event_corrections=EventCorrectionRepository(database_session).list(
                    limit=limit
                ),
            )

    def patterns(self, *, limit: int = DEFAULT_INSPECTION_LIMIT) -> tuple[Pattern, ...]:
        with session_scope(self._engine) as database_session:
            return PatternRepository(database_session).list(limit=limit)

    def memory(self, *, limit: int = DEFAULT_INSPECTION_LIMIT) -> MemoryInspection:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            return MemoryInspection(
                candidates=pipeline.candidates(limit=limit),
                records=pipeline.memory_records(limit=limit),
                corrections=MemoryCorrectionRepository(database_session).list_builds(
                    limit=limit
                ),
            )

    def privacy(self, *, limit: int = DEFAULT_INSPECTION_LIMIT) -> PrivacyInspection:
        with session_scope(self._engine) as database_session:
            pipeline = PipelineRepository(database_session)
            observations = pipeline.observations(limit=limit)
            transformations = ModelTransformationRepository(database_session).list(
                limit=limit
            )
            decisions = CandidateDecisionRepository(database_session).list(limit=limit)
        artifacts: list[RawArtifactInspection] = []
        for observation in observations:
            if observation.artifact_path is None:
                continue
            artifact = Path(observation.artifact_path)
            size = self._raw_store.size(artifact)
            artifacts.append(
                RawArtifactInspection(
                    observation_id=observation.id,
                    source_type=observation.source_type.value,
                    captured_at=observation.captured_at,
                    expires_at=observation.expires_at,
                    size_bytes=size,
                    available=size > 0,
                )
            )
        return PrivacyInspection(
            raw_artifacts=tuple(artifacts),
            transformations=transformations,
            candidate_decisions=decisions,
        )

    def processing_runs(
        self, *, limit: int = DEFAULT_INSPECTION_LIMIT
    ) -> tuple[ProcessingRun, ...]:
        with session_scope(self._engine) as database_session:
            return PipelineRepository(database_session).processing_runs(limit=limit)

    def agent(self, *, limit: int = DEFAULT_INSPECTION_LIMIT) -> AgentInspection:
        with session_scope(self._engine) as database_session:
            return AgentInspection(
                proposals=AgentProposalRepository(database_session).list(limit=limit),
                validations=AgentProposalAdoptionRepository(
                    database_session
                ).list_builds(limit=limit),
            )


def _operational_issues(
    runs: tuple[ProcessingRun, ...],
    *,
    model: LocalModelRuntimeStatus,
    model_backlog: int,
    abandoned_transformations: int,
) -> tuple[OperationalIssue, ...]:
    issues: list[OperationalIssue] = []
    if not model.runtime_available or not model.model_available:
        issues.append(
            OperationalIssue(
                code=model.reason_code or "local_model_unavailable",
                summary="The required local model is unavailable.",
            )
        )
    if model_backlog:
        issues.append(
            OperationalIssue(
                code="local_model_backlog",
                summary=f"{model_backlog} local-model item(s) are waiting.",
            )
        )
    if abandoned_transformations:
        issues.append(
            OperationalIssue(
                code="abandoned_model_transformations",
                summary=(
                    f"{abandoned_transformations} local-model transformation(s) "
                    "were abandoned."
                ),
            )
        )
    for run in runs:
        if run.error_code is None:
            continue
        issues.append(
            OperationalIssue(
                code=run.error_code,
                summary=run.error_summary or f"{run.pipeline} failed.",
                occurred_at=run.ended_at,
            )
        )
        if len(issues) >= 10:
            break
    return tuple(issues)


def _safe_directory_usage(root: Path) -> int:
    if not root.exists():
        return 0
    if root.is_symlink() or not root.is_dir():
        raise ConfigurationError("CONTX memory storage is not a safe directory")
    total = 0
    pending = [root]
    try:
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        raise ConfigurationError(
                            "CONTX memory storage contains an unsafe link"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):
                        file_status = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(file_status.st_mode):
                            raise ConfigurationError(
                                "CONTX memory storage contains an unsafe entry"
                            )
                        total += file_status.st_size
                    else:
                        raise ConfigurationError(
                            "CONTX memory storage contains an unsafe entry"
                        )
    except ConfigurationError:
        raise
    except OSError as error:
        raise ConfigurationError("Cannot inspect CONTX memory storage") from error
    return total
