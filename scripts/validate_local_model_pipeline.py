"""Validate the persisted local-model worker with synthetic pixels only."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from contx.application import LocalModelProcessingService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import ModelTransformationRepository, PipelineRepository
from contx.model_provider import DEFAULT_ENDPOINT, DEFAULT_MODEL, OllamaModelProvider
from contx.models import Observation, SourceType, SystemClock, UuidIdentifierSource
from contx.raw_store import FilesystemRawStore
from scripts.benchmark_local_model import build_synthetic_activity_png


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the persisted CONTX model pipeline synthetically."
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    arguments = parser.parse_args(argv)

    clock = SystemClock()
    identifiers = UuidIdentifierSource()
    provider = OllamaModelProvider(
        endpoint=arguments.endpoint,
        model=arguments.model,
        clock=clock,
    )
    with tempfile.TemporaryDirectory(prefix="contx-model-pipeline-") as directory:
        root = Path(directory)
        database = root / "contx.db"
        raw_store = FilesystemRawStore(
            root / "raw",
            disk_budget_bytes=64 * 1024 * 1024,
        )
        captured_at = clock.now()
        observation_id = identifiers.new()
        artifact = raw_store.write(
            build_synthetic_activity_png(),
            artifact_id=observation_id,
            suffix=".png",
            captured_at=captured_at,
            retention=timedelta(hours=1),
        )
        observation = Observation(
            id=observation_id,
            idempotency_key=artifact.content_hash,
            source_type=SourceType.SCREENSHOT,
            captured_at=captured_at,
            started_at=captured_at,
            ended_at=captured_at,
            app_name="Synthetic Code Editor",
            app_bundle_id="io.contx.synthetic-pipeline",
            window_title="CONTX persisted local-model validation",
            artifact_path=str(artifact.path),
            content_hash=artifact.content_hash,
            expires_at=artifact.expires_at,
            created_at=captured_at,
        )

        upgrade_database(database)
        engine = create_database_engine(database)
        try:
            with session_scope(engine) as session:
                PipelineRepository(session).save_observation(observation)
            result = LocalModelProcessingService(
                engine=engine,
                raw_store=raw_store,
                provider=provider,
                endpoint=arguments.endpoint,
                configured_model=arguments.model,
                max_image_bytes=20 * 1024 * 1024,
                clock=clock,
                identifiers=identifiers,
                batch_size=1,
            ).run_once()
            transformation = None
            persisted_observation = None
            processing_run_ids: tuple[object, ...] = ()
            if result.queued_transformation_ids:
                with session_scope(engine) as session:
                    model_repository = ModelTransformationRepository(session)
                    transformation = model_repository.by_id(
                        result.queued_transformation_ids[0]
                    )
                    processing_run_ids = model_repository.processing_run_ids(
                        result.queued_transformation_ids[0]
                    )
                    persisted_observation = PipelineRepository(
                        session
                    ).observation_by_id(observation_id)
            print(
                json.dumps(
                    {
                        "run_status": result.run.status.value,
                        "run_error_code": result.run.error_code,
                        "queued_count": len(result.queued_transformation_ids),
                        "succeeded_count": len(result.succeeded_transformation_ids),
                        "failed_count": len(result.failed_transformation_ids),
                        "abandoned_count": len(result.abandoned_transformation_ids),
                        "backlog_count": result.backlog_count,
                        "observation_status": (
                            None
                            if persisted_observation is None
                            else persisted_observation.processing_status.value
                        ),
                        "processing_run_count": len(processing_run_ids),
                        "model_digest": (
                            None
                            if transformation is None
                            else transformation.model_digest
                        ),
                        "prompt_version": (
                            None
                            if transformation is None
                            else transformation.prompt_version
                        ),
                        "output_schema_version": (
                            None
                            if transformation is None
                            else transformation.output_schema_version
                        ),
                        "attempt_count": (
                            None
                            if transformation is None
                            else transformation.attempt_count
                        ),
                        "last_error_code": (
                            None
                            if transformation is None
                            else transformation.last_error_code
                        ),
                        "wall_duration_ms": (
                            None
                            if transformation is None
                            else transformation.wall_duration_ms
                        ),
                        "interpretation": (
                            None
                            if transformation is None
                            or transformation.interpretation is None
                            else transformation.interpretation.model_dump(mode="json")
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            valid = (
                result.succeeded
                and transformation is not None
                and persisted_observation is not None
                and persisted_observation.processing_status.value == "processed"
                and len(processing_run_ids) == 1
            )
            return 0 if valid else 1
        finally:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
