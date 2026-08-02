"""Observable bounded maintenance for the local final-memory backend."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from contx.db import session_scope
from contx.db.repositories import PipelineRepository
from contx.errors import ContxError, LocalModelError, MemoryStoreError, PipelineError
from contx.memory_store import MemoryCompressor, MemoryMaintenance, MemoryStore
from contx.models import Clock, IdentifierSource, ProcessingRun

MEMORY_MAINTENANCE_VERSION = "memory-maintenance-v1"


@dataclass(frozen=True, slots=True)
class MemoryMaintenanceResult:
    run: ProcessingRun
    maintenance: MemoryMaintenance


class MemoryMaintenanceService:
    """Run a bounded number of offline-capable local compression steps."""

    def __init__(
        self,
        *,
        engine: Engine,
        memory_store: MemoryStore,
        compressor: MemoryCompressor,
        clock: Clock,
        identifiers: IdentifierSource,
        max_compressions: int,
    ) -> None:
        if not 1 <= max_compressions <= 100:
            raise ValueError("maximum compressions must be between 1 and 100")
        self._engine = engine
        self._memory_store = memory_store
        self._compressor = compressor
        self._clock = clock
        self._identifiers = identifiers
        self._max_compressions = max_compressions

    def run(self) -> MemoryMaintenanceResult:
        run = ProcessingRun(
            id=self._identifiers.new(),
            pipeline="memory_maintenance",
            version=MEMORY_MAINTENANCE_VERSION,
            started_at=self._clock.now(),
        )
        self._save_run(run)
        try:
            self._memory_store.initialize()
            maintenance = self._memory_store.maintain(
                self._compressor,
                max_compressions=self._max_compressions,
            )
            succeeded = run.succeed(
                ended_at=self._clock.now(),
                input_count=(
                    maintenance.completed_compressions
                    + (0 if maintenance.complete else 1)
                ),
                output_count=maintenance.completed_compressions,
            )
            self._save_run(succeeded)
        except Exception as error:
            failed = run.fail(
                ended_at=self._clock.now(),
                error_code=(
                    "memory_store_error"
                    if isinstance(error, MemoryStoreError)
                    else (
                        "local_model_error"
                        if isinstance(error, LocalModelError)
                        else "unexpected_memory_maintenance_failure"
                    )
                ),
            )
            try:
                self._save_run(failed)
            except Exception:
                raise PipelineError(
                    "Memory maintenance failed and its status could not be recorded"
                ) from error
            if isinstance(error, ContxError):
                raise
            raise PipelineError("Memory maintenance failed") from error
        return MemoryMaintenanceResult(run=succeeded, maintenance=maintenance)

    def _save_run(self, run: ProcessingRun) -> None:
        with session_scope(self._engine) as database_session:
            PipelineRepository(database_session).save_processing_run(run)
