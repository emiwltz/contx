"""Observable bounded final-memory maintenance behavior."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from contx.application import MemoryMaintenanceService
from contx.db import (
    create_database_engine,
    session_scope,
    upgrade_database,
)
from contx.db.repositories import PipelineRepository
from contx.errors import MemoryStoreError
from contx.memory_store import (
    MemoryCompressionRequest,
    MemoryMaintenance,
    RecordingMemoryStore,
)
from contx.models import ProcessingRunStatus
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
RUN_ID = UUID("90000000-0000-0000-0000-000000000001")
FAILED_RUN_ID = UUID("90000000-0000-0000-0000-000000000002")


def test_bounded_maintenance_progress_is_persisted(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    memory = PendingMemoryStore()
    try:
        result = MemoryMaintenanceService(
            engine=engine,
            memory_store=memory,
            compressor=StaticCompressor(),
            clock=FixedClock(NOW),
            identifiers=SequenceIdentifiers((RUN_ID,)),
            max_compressions=1,
        ).run()

        assert result.run.status is ProcessingRunStatus.SUCCEEDED
        assert result.run.input_count == 2
        assert result.run.output_count == 1
        assert not result.maintenance.complete
        assert result.maintenance.next_request is not None
        with session_scope(engine) as database_session:
            persisted = PipelineRepository(database_session).processing_run_by_id(
                RUN_ID
            )
            assert persisted == result.run
    finally:
        engine.dispose()


def test_maintenance_failure_is_visible_and_retryable(tmp_path: Path) -> None:
    database_path = tmp_path / "contx.db"
    upgrade_database(database_path)
    engine = create_database_engine(database_path)
    try:
        with pytest.raises(MemoryStoreError, match="synthetic maintenance failure"):
            MemoryMaintenanceService(
                engine=engine,
                memory_store=FailingMemoryStore(),
                compressor=StaticCompressor(),
                clock=FixedClock(NOW),
                identifiers=SequenceIdentifiers((FAILED_RUN_ID,)),
                max_compressions=1,
            ).run()

        with session_scope(engine) as database_session:
            failed = PipelineRepository(database_session).processing_run_by_id(
                FAILED_RUN_ID
            )
            assert failed is not None
            assert failed.status is ProcessingRunStatus.FAILED
            assert failed.error_code == "memory_store_error"
    finally:
        engine.dispose()


class StaticCompressor:
    def compress(self, request: MemoryCompressionRequest) -> str:
        return "Synthetic durable summary."


class PendingMemoryStore(RecordingMemoryStore):
    def maintain(
        self,
        compressor: StaticCompressor,
        *,
        max_compressions: int,
    ) -> MemoryMaintenance:
        return MemoryMaintenance(
            completed_compressions=1,
            complete=False,
            next_request=MemoryCompressionRequest(
                block="2-3",
                prompt="Synthetic pending evidence.",
                max_bytes=280,
            ),
        )


class FailingMemoryStore(RecordingMemoryStore):
    def maintain(
        self,
        compressor: StaticCompressor,
        *,
        max_compressions: int,
    ) -> MemoryMaintenance:
        raise MemoryStoreError("synthetic maintenance failure")
