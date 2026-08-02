"""Validate correction semantics across a real long OptMem merge tree."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Engine

from contx.application import ActiveMemoryProjectionService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.models import MemoryLinkModel
from contx.db.repositories import PipelineRepository
from contx.memory_store import (
    MemoryCompressionRequest,
    OllamaMemoryCompressor,
    OptMemAdapter,
    resolve_optmem_executable,
)
from contx.model_provider import DEFAULT_ENDPOINT, DEFAULT_MODEL
from contx.models import (
    CandidateStatus,
    EpistemicStatus,
    Event,
    EventType,
    MemoryCandidate,
    MemoryLink,
    MemoryLinkStatus,
    MemoryProvenance,
    Observation,
    Sensitivity,
    SourceType,
)

ENTRY_COUNT = 32
EXPECTED_COMPRESSIONS = 31
EXPECTED_ACTIVE_COUNT = 29
MAX_PROJECTION_COMPRESSIONS = EXPECTED_ACTIVE_COUNT - 1
NOW = datetime(2026, 8, 2, 20, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("e0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("e0000000-0000-0000-0000-000000000002")


class ProgressCompressor:
    def __init__(
        self,
        compressor: OllamaMemoryCompressor,
        *,
        label: str,
        expected_compressions: int,
    ) -> None:
        self._compressor = compressor
        self._label = label
        self._expected_compressions = expected_compressions
        self.blocks: list[str] = []

    @property
    def model_digest(self) -> str | None:
        return self._compressor.model_digest

    def compress(self, request: MemoryCompressionRequest) -> str:
        self.blocks.append(request.block)
        print(
            f"{self._label} compression {len(self.blocks)}/"
            f"{self._expected_compressions}: "
            f"{request.block}",
            flush=True,
        )
        return self._compressor.compress(request)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a synthetic 32-entry OptMem correction tree."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument(
        "--projection-only",
        action="store_true",
        help="Skip the already-documented unsafe historical root rebuild.",
    )
    arguments = parser.parse_args()

    executable = resolve_optmem_executable()
    with tempfile.TemporaryDirectory(prefix="contx-optmem-long-history-") as root:
        memory_directory = Path(root) / "memory"
        adapter = OptMemAdapter(
            executable=executable,
            memory_directory=memory_directory,
            wake_budget_bytes=4096,
        )
        historical_compressor = ProgressCompressor(
            OllamaMemoryCompressor(
                model=arguments.model,
                endpoint=arguments.endpoint,
            ),
            label="historical",
            expected_compressions=EXPECTED_COMPRESSIONS,
        )
        adapter.initialize()
        for index, entry in enumerate(_entries()):
            adapter.append(entry, idempotency_key=f"long-history-{index}")

        started = time.monotonic()
        historical_result = None
        if not arguments.projection_only:
            maintained = adapter.maintain(
                historical_compressor,
                max_compressions=100,
            )
            default_wake = adapter.wake()
            historical_zoom = adapter.zoom("0-31")
            _set_wake_lines(
                executable=executable,
                memory_directory=memory_directory,
                wake_lines=1,
            )
            root_wake = adapter.wake()
            historical_result = {
                "compressions": maintained.completed_compressions,
                "maintenance_complete": maintained.complete,
                "default_wake": default_wake.content,
                "root_wake": root_wake.content,
                "historical_zoom": historical_zoom,
                "fallback_trigger_reproduced": _contains_obsolete_claim(
                    root_wake.content
                ),
            }
        recalled = adapter.recall("Atlas deployment target")

        database_path = Path(root) / "contx.db"
        engine = _projection_engine(database_path)
        projection_compressor = ProgressCompressor(
            OllamaMemoryCompressor(
                model=arguments.model,
                endpoint=arguments.endpoint,
            ),
            label="projection",
            expected_compressions=MAX_PROJECTION_COMPRESSIONS,
        )
        try:
            _seed_projection_database(engine)
            projection_root = Path(root) / "memory-active"
            projection_service = ActiveMemoryProjectionService(
                engine=engine,
                projection_root=projection_root,
                memory_store_factory=lambda directory: OptMemAdapter(
                    executable=executable,
                    memory_directory=directory,
                    wake_budget_bytes=4096,
                ),
                compressor=projection_compressor,
            )
            projection_wake = projection_service.wake()
            pointer = json.loads(
                (projection_root / "CURRENT.json").read_text(encoding="utf-8")
            )
            projection_directory = (
                projection_root / "generations" / str(pointer["generation"])
            )
            _set_wake_lines(
                executable=executable,
                memory_directory=projection_directory,
                wake_lines=1,
            )
            projection_root_wake = OptMemAdapter(
                executable=executable,
                memory_directory=projection_directory,
                wake_budget_bytes=4096,
            ).wake()
            calls_before_reuse = len(projection_compressor.blocks)
            reused = projection_service.synchronize()
        finally:
            engine.dispose()

        projection_default = projection_wake.wake.content
        projection_root_content = projection_root_wake.content
        recall_lower = recalled.casefold()
        checks = {
            "active_count": (
                projection_wake.projection.active_memory_count
                == EXPECTED_ACTIVE_COUNT
            ),
            "projection_rebuilt": projection_wake.projection.rebuilt,
            "projection_compression_count": (
                0 < projection_wake.projection.completed_compressions
                <= MAX_PROJECTION_COMPRESSIONS
                and projection_wake.projection.completed_compressions
                == len(projection_compressor.blocks)
            ),
            "projection_default_complete": projection_wake.wake.complete,
            "projection_default_current_present": _contains_current_claim(
                projection_default
            ),
            "projection_default_current_explicit": (
                "correction:" in projection_default.casefold()
            ),
            "projection_default_obsolete_absent": not _contains_obsolete_claim(
                projection_default
            ),
            "projection_default_older_fact_present": (
                "python 3.12" in projection_default.casefold()
            ),
            "projection_default_newer_fact_present": _contains_docs_fact(
                projection_default
            ),
            "projection_root_complete": projection_root_wake.complete,
            "projection_root_current_present": _contains_current_claim(
                projection_root_content
            ),
            "projection_root_obsolete_absent": not _contains_obsolete_claim(
                projection_root_content
            ),
            "projection_root_older_fact_present": (
                "python 3.12" in projection_root_content.casefold()
            ),
            "projection_root_newer_fact_present": _contains_docs_fact(
                projection_root_content
            ),
            "historical_recall_keeps_initial": "target is staging" in recall_lower,
            "historical_recall_keeps_local": "target is local-only" in recall_lower,
            "historical_recall_keeps_production": (
                "target is production-only" in recall_lower
            ),
            "historical_recall_keeps_edge": "target is edge-only" in recall_lower,
            "unchanged_projection_reused": (
                not reused.rebuilt
                and len(projection_compressor.blocks) == calls_before_reuse
            ),
        }
        if historical_result is not None:
            checks.update(
                {
                    "historical_maintenance_complete": bool(
                        historical_result["maintenance_complete"]
                    ),
                    "historical_compression_count": (
                        historical_result["compressions"] == EXPECTED_COMPRESSIONS
                    ),
                    "historical_zoom_available": bool(
                        str(historical_result["historical_zoom"]).strip()
                    ),
                }
            )
        duration_seconds = round(time.monotonic() - started, 3)
        output = {
            "historical_entries": ENTRY_COUNT,
            "active_entries": EXPECTED_ACTIVE_COUNT,
            "duration_seconds": duration_seconds,
            "model": arguments.model,
            "model_digest": projection_compressor.model_digest,
            "checks": checks,
            "projection_default_wake": projection_default,
            "projection_root_wake": projection_root_content,
            "historical": historical_result,
        }
        print(json.dumps(output, ensure_ascii=False, sort_keys=True), flush=True)
        failed = tuple(name for name, passed in checks.items() if not passed)
        if failed:
            raise RuntimeError(
                "Long-history active projection validation failed: "
                + ", ".join(failed)
            )
    return 0


def _entries() -> tuple[str, ...]:
    entries = [
        "Atlas deployment target is staging.",
        "Correction: Atlas deployment target is local-only.",
        "Correction: Atlas deployment target is production-only.",
        "Atlas runtime requires Python 3.12.",
    ]
    entries.extend(
        f"Synthetic durable reference {index:02d} remains stable and independent."
        for index in range(4, 16)
    )
    entries.append("Correction: Atlas deployment target is edge-only.")
    entries.append("Atlas documentation is maintained in English.")
    entries.extend(
        f"Synthetic durable reference {index:02d} remains stable and independent."
        for index in range(18, ENTRY_COUNT)
    )
    if len(entries) != ENTRY_COUNT:
        raise RuntimeError("Synthetic long-history fixture has the wrong size")
    return tuple(entries)


def _projection_engine(database_path: Path) -> Engine:
    upgrade_database(database_path)
    return create_database_engine(database_path)


def _seed_projection_database(engine: Engine) -> None:
    observation = Observation(
        id=OBSERVATION_ID,
        idempotency_key="1" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )
    event = Event(
        id=EVENT_ID,
        idempotency_key="2" * 64,
        lineage_key="3" * 64,
        type=EventType.PROJECT_WORK,
        summary="Synthetic long-history projection evidence.",
        facts={},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        valid_from=NOW,
        valid_until=NOW + timedelta(minutes=10),
        epistemic_status=EpistemicStatus.INFERRED,
        confidence=0.9,
        sensitivity=Sensitivity.PERSONAL,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="long-history-validation-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    correction_sources = {1: 0, 2: 1, 16: 2}
    superseded = {0, 1, 2}
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_observation(observation)
        repository.save_event(event)
        links: dict[int, MemoryLink] = {}
        for index, text in enumerate(_entries()):
            source_index = correction_sources.get(index)
            candidate_id = uuid5(
                NAMESPACE_URL,
                f"contx:long-history-candidate:{index}",
            )
            memory_id = uuid5(
                NAMESPACE_URL,
                f"contx:long-history-memory:{index}",
            )
            candidate = MemoryCandidate(
                id=candidate_id,
                idempotency_key=_sha256(f"long-history-{index}"),
                text=text,
                source_type=(
                    "event" if source_index is None else "memory_correction"
                ),
                source_ids=(
                    (EVENT_ID,)
                    if source_index is None
                    else (links[source_index].id,)
                ),
                utility=0.8,
                importance=0.8,
                durability=0.9,
                novelty=0.9,
                confidence=0.9,
                sensitivity=Sensitivity.PERSONAL,
                score=0.8,
                status=CandidateStatus.STORED,
                created_at=NOW + timedelta(seconds=index),
                processed_at=NOW + timedelta(seconds=index),
            )
            link = MemoryLink(
                id=memory_id,
                memory_backend_id=str(index),
                candidate_id=candidate.id,
                provenance=MemoryProvenance(
                    candidate_id=candidate.id,
                    event_ids=(EVENT_ID,),
                    observation_ids=(OBSERVATION_ID,),
                ),
                confidence=candidate.confidence,
                status=MemoryLinkStatus.ACTIVE,
                supersedes_memory_id=(
                    None if source_index is None else links[source_index].id
                ),
                created_at=candidate.created_at,
            )
            repository.save_candidate(candidate)
            links[index] = repository.save_memory_link(link)
        for index in superseded:
            model = database_session.get(MemoryLinkModel, str(links[index].id))
            if model is None:
                raise RuntimeError("Synthetic correction chain is incomplete")
            model.status = MemoryLinkStatus.SUPERSEDED.value


def _sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contains_current_claim(value: str) -> bool:
    normalized = value.casefold().replace("-", " ")
    return "edge only" in normalized


def _contains_obsolete_claim(value: str) -> bool:
    normalized = value.casefold().replace("-", " ")
    return any(claim in normalized for claim in ("staging", "local only", "production"))


def _contains_docs_fact(value: str) -> bool:
    normalized = value.casefold()
    return "english" in normalized and (
        "documentation" in normalized or "docs" in normalized
    )


def _set_wake_lines(
    *,
    executable: Path,
    memory_directory: Path,
    wake_lines: int,
) -> None:
    environment = {
        "HOME": str(Path.home()),
        "MEMORY_DIR": str(memory_directory),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        result = subprocess.run(
            (str(executable), "config", f"WAKE_LINES={wake_lines}"),
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Cannot configure disposable OptMem validation") from error
    if result.returncode != 0:
        raise RuntimeError("Disposable OptMem validation config was rejected")


if __name__ == "__main__":
    raise SystemExit(main())
