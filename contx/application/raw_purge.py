"""Restartable purge for expired raw observations and artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import PipelineRepository, RawObservationRepository
from contx.errors import ContxError, PipelineError
from contx.models import Clock, IdentifierSource, ProcessingRun, ProcessingRunStatus
from contx.raw_store import RawStore

RAW_PURGE_VERSION = "raw-purge-v1"


@dataclass(frozen=True, slots=True)
class RawPurgeResult:
    run: ProcessingRun
    purged_observation_ids: tuple[UUID, ...]
    failed_observation_ids: tuple[UUID, ...]
    bytes_reclaimed: int
    orphan_artifacts_deleted: int

    @property
    def succeeded(self) -> bool:
        return self.run.status is ProcessingRunStatus.SUCCEEDED


class RawPurgeService:
    """Delete files first, then retain only a provenance-safe tombstone."""

    def __init__(
        self,
        *,
        engine: Engine,
        raw_store: RawStore,
        clock: Clock,
        identifiers: IdentifierSource,
        batch_size: int = 1000,
    ) -> None:
        if batch_size < 1:
            raise ValueError("raw purge batch size must be positive")
        self._engine = engine
        self._raw_store = raw_store
        self._clock = clock
        self._identifiers = identifiers
        self._batch_size = batch_size

    def run(self, *, include_unexpired: bool = False) -> RawPurgeResult:
        started_at = self._clock.now()
        running_run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline=(
                "raw_purge_immediate" if include_unexpired else "raw_purge"
            ),
            version=RAW_PURGE_VERSION,
            started_at=started_at,
        )
        self._save_run(running_run)
        run = running_run
        purged: list[UUID] = []
        failed: list[UUID] = []
        reclaimed = 0
        orphan_artifacts_deleted = 0
        try:
            self._raw_store.initialize()
            with session_scope(self._engine) as session:
                repository = RawObservationRepository(session)
                referenced_paths = repository.artifact_paths()
                selected = (
                    repository.retained(limit=self._batch_size)
                    if include_unexpired
                    else repository.expired(
                        at=started_at,
                        limit=self._batch_size,
                    )
                )
            for artifact in self._raw_store.list_paths():
                if str(artifact) in referenced_paths:
                    continue
                artifact_size = self._raw_store.size(artifact)
                self._raw_store.delete(artifact)
                reclaimed += artifact_size
                orphan_artifacts_deleted += 1
            for observation in selected:
                try:
                    artifact_size = 0
                    if observation.artifact_path is not None:
                        artifact = Path(observation.artifact_path)
                        artifact_size = self._raw_store.size(artifact)
                        self._raw_store.delete(artifact)
                    with session_scope(self._engine) as session:
                        RawObservationRepository(session).tombstone(observation.id)
                    reclaimed += artifact_size
                    purged.append(observation.id)
                except ContxError:
                    failed.append(observation.id)

            ended_at = self._clock.now()
            if failed:
                run = run.fail(
                    ended_at=ended_at,
                    error_code="raw_purge_partial_failure",
                    input_count=len(selected),
                    output_count=len(purged),
                )
            else:
                run = run.succeed(
                    ended_at=ended_at,
                    input_count=len(selected),
                    output_count=len(purged),
                )
            self._save_run(run)
        except Exception as error:
            failed_run = running_run.fail(
                ended_at=self._clock.now(),
                error_code="raw_purge_failure",
                input_count=len(purged) + len(failed),
                output_count=len(purged),
            )
            try:
                self._save_run(failed_run)
            except Exception:
                raise PipelineError(
                    "Raw purge failed and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Raw purge failed") from error
        return RawPurgeResult(
            run=run,
            purged_observation_ids=tuple(purged),
            failed_observation_ids=tuple(failed),
            bytes_reclaimed=reclaimed,
            orphan_artifacts_deleted=orphan_artifacts_deleted,
        )

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as session:
            PipelineRepository(session).save_processing_run(run)
