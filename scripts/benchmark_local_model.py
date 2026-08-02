"""Run the configured local multimodal model against a synthetic screen only."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from contx.model_provider import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    LocalModelRequest,
    OllamaModelProvider,
)
from contx.models import ActivityState, SystemClock
from scripts.synthetic_screen import render_synthetic_screen


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark CONTX local vision with synthetic pixels only."
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument(
        "--metadata-profile",
        choices=("benchmark", "pipeline"),
        default="benchmark",
        help="Select one hard-coded synthetic metadata profile.",
    )
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.iterations <= 10:
        parser.error("--iterations must be between 1 and 10")

    clock = SystemClock()
    provider = OllamaModelProvider(
        endpoint=arguments.endpoint,
        model=arguments.model,
        clock=clock,
    )
    status = provider.status()
    print(json.dumps({"status": status.model_dump(mode="json")}, sort_keys=True))
    if not status.model_available:
        return 1

    image = build_synthetic_activity_png()
    digest = hashlib.sha256(image).hexdigest()
    metadata = (
        {
            "app_name": "Synthetic Code Editor",
            "app_bundle_id": "io.contx.synthetic-pipeline",
            "window_title": "CONTX persisted local-model validation",
        }
        if arguments.metadata_profile == "pipeline"
        else {
            "app_name": "Synthetic Code Editor",
            "app_bundle_id": "io.contx.synthetic-benchmark",
            "window_title": "CONTX local model benchmark",
        }
    )
    for iteration in range(1, arguments.iterations + 1):
        captured_at = clock.now()
        execution = provider.interpret(
            LocalModelRequest(
                id=uuid5(NAMESPACE_URL, f"contx:model-benchmark:{iteration}"),
                source_observation_ids=(
                    uuid5(NAMESPACE_URL, "contx:model-benchmark:synthetic-screen"),
                ),
                captured_at=captured_at,
                started_at=captured_at,
                ended_at=captured_at,
                activity_state=ActivityState.ACTIVE,
                app_name=metadata["app_name"],
                app_bundle_id=metadata["app_bundle_id"],
                window_title=metadata["window_title"],
                image_sha256=digest,
                image_bytes=image,
            )
        )
        print(
            json.dumps(
                {
                    "iteration": iteration,
                    "model": execution.model,
                    "model_digest": execution.model_digest,
                    "prompt_version": execution.prompt_version,
                    "output_schema_version": execution.output_schema_version,
                    "wall_duration_ms": execution.wall_duration_ms,
                    "runtime_duration_ms": execution.runtime_duration_ms,
                    "load_duration_ms": execution.load_duration_ms,
                    "prompt_eval_count": execution.prompt_eval_count,
                    "eval_count": execution.eval_count,
                    "interpretation": execution.interpretation.model_dump(mode="json"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


def build_synthetic_activity_png() -> bytes:
    """Render a deterministic screen-like fixture without reading the display."""
    return render_synthetic_screen(
        title="CONTX — Synthetic local-model benchmark",
        body=(
            "Project: CONTX\n"
            "File: contx/model_provider/ollama.py\n"
            "Task: Implement strict local-only multimodal interpretation\n\n"
            "✓ Loopback-only transport\n"
            "✓ JSON Schema result validation\n"
            "✓ Model and source provenance\n"
            "✓ No remote user-content API\n\n"
            "This screen contains synthetic test data only."
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
