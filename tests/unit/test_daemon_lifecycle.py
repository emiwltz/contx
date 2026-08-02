"""Single-process daemon lifecycle tests."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest

from contx.application import ContinuousCollectionResult
from contx.daemon import CollectionDaemonLifecycle
from contx.errors import CollectorUnavailableError, PipelineError
from contx.models import ProcessingRun

NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)


class RecordingLease:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def acquire(self) -> None:
        self._events.append("lease.acquire")

    def release(self) -> None:
        self._events.append("lease.release")


class RecordingMonitor:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def start(self) -> None:
        self._events.append("monitor.start")

    def stop(self) -> None:
        self._events.append("monitor.stop")


class RecordingMenu:
    def __init__(
        self,
        events: list[str],
        *,
        fail_start: bool = False,
        fail_refresh: bool = False,
    ) -> None:
        self._events = events
        self._fail_start = fail_start
        self._fail_refresh = fail_refresh

    def start(self) -> None:
        self._events.append("menu.start")
        if self._fail_start:
            raise CollectorUnavailableError("synthetic menu start failure")

    def refresh(self) -> object:
        self._events.append("menu.refresh")
        if self._fail_refresh:
            raise CollectorUnavailableError("synthetic menu refresh failure")
        return object()

    def stop(self) -> None:
        self._events.append("menu.stop")


@dataclass
class RecordingSession:
    events: list[str]
    running: bool = False
    aborted_with: Exception | None = None

    @property
    def is_running(self) -> bool:
        return self.running

    def start(self) -> None:
        self.events.append("session.start")
        self.running = True

    def tick(self) -> int:
        self.events.append("session.tick")
        return 2

    def stop(self) -> ContinuousCollectionResult:
        self.events.append("session.stop")
        self.running = False
        run = ProcessingRun(
            id=UUID(int=1),
            pipeline="test",
            version="test-v1",
            started_at=NOW,
        ).succeed(
            ended_at=NOW,
            input_count=1,
            output_count=2,
        )
        return ContinuousCollectionResult(run=run, cycles=1, observations=2)

    def abort(self, error: Exception) -> None:
        self.events.append("session.abort")
        self.running = False
        self.aborted_with = error
        raise PipelineError("synthetic audited abort") from error


def test_lifecycle_orders_start_tick_and_reverse_cleanup() -> None:
    events: list[str] = []
    session = RecordingSession(events)
    lifecycle = _lifecycle(events, session=session)

    lifecycle.start()
    assert lifecycle.tick() == 2
    result = lifecycle.stop()

    assert result is not None
    assert result.observations == 2
    assert events == [
        "lease.acquire",
        "monitor.start",
        "menu.start",
        "session.start",
        "session.tick",
        "menu.refresh",
        "session.stop",
        "menu.stop",
        "monitor.stop",
        "lease.release",
    ]


def test_startup_failure_releases_every_acquired_native_resource() -> None:
    events: list[str] = []
    lifecycle = _lifecycle(
        events,
        session=RecordingSession(events),
        menu=RecordingMenu(events, fail_start=True),
    )

    with pytest.raises(CollectorUnavailableError, match="menu start failure"):
        lifecycle.start()

    assert not lifecycle.is_started
    assert events == [
        "lease.acquire",
        "monitor.start",
        "menu.start",
        "monitor.stop",
        "lease.release",
    ]


def test_native_tick_failure_aborts_audited_session() -> None:
    events: list[str] = []
    session = RecordingSession(events)
    lifecycle = _lifecycle(
        events,
        session=session,
        menu=RecordingMenu(events, fail_refresh=True),
    )
    lifecycle.start()

    with pytest.raises(PipelineError, match="audited abort"):
        lifecycle.tick()

    assert isinstance(session.aborted_with, CollectorUnavailableError)
    assert lifecycle.stop() is None
    assert events[-3:] == ["menu.stop", "monitor.stop", "lease.release"]


def _lifecycle(
    events: list[str],
    *,
    session: RecordingSession,
    menu: RecordingMenu | None = None,
) -> CollectionDaemonLifecycle:
    return CollectionDaemonLifecycle(
        lease=RecordingLease(events),
        notifications=RecordingMonitor(events),
        menu=menu or RecordingMenu(events),
        session=session,
    )
