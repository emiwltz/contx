"""Internal one-shot entrypoint for periodic local semantic processing."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol

from contx.errors import ContxError
from contx.processing.factory import (
    ConfiguredContextProcessor,
    build_context_processor,
)


class ProcessorBuilder(Protocol):
    def __call__(self) -> ConfiguredContextProcessor: ...


def run_context_processor(
    *,
    build: ProcessorBuilder = build_context_processor,
    write_output: Callable[[str], object] | None = None,
    write_error: Callable[[str], object] | None = None,
) -> int:
    """Run one bounded attempt with content-free supervised output."""
    output_sink = write_output or _write_standard_output
    error_sink = write_error or _write_standard_error
    processor: ConfiguredContextProcessor | None = None
    try:
        processor = build()
        result = processor.run()
    except ContxError as error:
        error_sink(f"CONTX processor stopped: {error}\n")
        return 2
    except Exception:
        error_sink("CONTX processor stopped after an unexpected local failure.\n")
        return 3
    finally:
        if processor is not None:
            processor.close()

    if result.blocked:
        error_sink("CONTX processor is blocked by a mandatory local stage.\n")
        return 2
    if result.deferred:
        output_sink(
            "CONTX processor deferred: "
            f"model_backlog={result.model_processing.backlog_count} "
            f"event_backlog={result.model_events.backlog_count}\n"
        )
        return 0
    if not result.completed or result.projection is None:
        error_sink("CONTX processor ended without publishing a complete result.\n")
        return 3
    output_sink(
        "CONTX processor complete: "
        f"active_memories={result.projection.active_memory_count}\n"
    )
    return 0


def _write_standard_output(message: str) -> int:
    return sys.stdout.write(message)


def _write_standard_error(message: str) -> int:
    return sys.stderr.write(message)


def main() -> None:
    raise SystemExit(run_context_processor())


if __name__ == "__main__":
    main()
