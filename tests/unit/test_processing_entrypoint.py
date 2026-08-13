"""Sanitized periodic processor entrypoint behavior."""

from types import SimpleNamespace
from typing import cast

from contx.errors import ConfigurationError
from contx.processing.entrypoint import run_context_processor
from contx.processing.factory import ConfiguredContextProcessor


class FakeProcessor:
    def __init__(
        self, *, result: object | None = None, failure: Exception | None = None
    ):
        self.result = result
        self.failure = failure
        self.runs = 0
        self.closes = 0

    def run(self) -> object:
        self.runs += 1
        if self.failure is not None:
            raise self.failure
        return self.result

    def close(self) -> None:
        self.closes += 1


def test_entrypoint_reports_a_complete_content_free_refresh() -> None:
    processor = FakeProcessor(result=_result(completed=True, active_memories=3))
    output: list[str] = []

    status = run_context_processor(
        build=lambda: cast(ConfiguredContextProcessor, processor),
        write_output=output.append,
    )

    assert status == 0
    assert output == ["CONTX processor complete: active_memories=3\n"]
    assert processor.runs == processor.closes == 1


def test_entrypoint_treats_a_bounded_backlog_as_expected_deferral() -> None:
    processor = FakeProcessor(
        result=_result(deferred=True, model_backlog=4, event_backlog=2)
    )
    output: list[str] = []

    status = run_context_processor(
        build=lambda: cast(ConfiguredContextProcessor, processor),
        write_output=output.append,
    )

    assert status == 0
    assert output == ["CONTX processor deferred: model_backlog=4 event_backlog=2\n"]
    assert processor.runs == processor.closes == 1


def test_expected_failure_is_actionable_and_still_closes() -> None:
    processor = FakeProcessor(failure=ConfigurationError("synthetic disabled state"))
    errors: list[str] = []

    status = run_context_processor(
        build=lambda: cast(ConfiguredContextProcessor, processor),
        write_error=errors.append,
    )

    assert status == 2
    assert errors == ["CONTX processor stopped: synthetic disabled state\n"]
    assert processor.closes == 1


def test_unexpected_failure_does_not_expose_exception_content() -> None:
    processor = FakeProcessor(failure=RuntimeError("synthetic private title"))
    errors: list[str] = []

    status = run_context_processor(
        build=lambda: cast(ConfiguredContextProcessor, processor),
        write_error=errors.append,
    )

    assert status == 3
    assert errors == ["CONTX processor stopped after an unexpected local failure.\n"]
    assert "private title" not in errors[0]
    assert processor.closes == 1


def _result(
    *,
    blocked: bool = False,
    deferred: bool = False,
    completed: bool = False,
    model_backlog: int = 0,
    event_backlog: int = 0,
    active_memories: int | None = None,
) -> object:
    return SimpleNamespace(
        blocked=blocked,
        deferred=deferred,
        completed=completed,
        model_processing=SimpleNamespace(backlog_count=model_backlog),
        model_events=SimpleNamespace(backlog_count=event_backlog),
        projection=(
            None
            if active_memories is None
            else SimpleNamespace(active_memory_count=active_memories)
        ),
    )
