"""Composable macOS activity probes and privacy ordering tests."""

from datetime import UTC, datetime

import pytest

from contx.collectors.macos import (
    ApplicationMetadata,
    MacOSActivitySampler,
    QuartzIdleSecondsProbe,
    ResolvedSystemStateProbe,
    SystemSignals,
    WorkspaceApplicationProbe,
)
from contx.errors import CollectorUnavailableError
from contx.models import ActivityState
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 14, 0, tzinfo=UTC)


class FakeApplication:
    def localizedName(self) -> str:
        return "  Synthetic Editor  "

    def bundleIdentifier(self) -> str:
        return " com.example.editor "


class FakeWorkspace:
    def frontmostApplication(self) -> FakeApplication:
        return FakeApplication()


class FixedIdle:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.calls = 0

    def seconds_since_input(self) -> float:
        self.calls += 1
        return self.seconds


class FixedState:
    def __init__(self, state: ActivityState) -> None:
        self.state = state

    def current_state(self) -> ActivityState:
        return self.state


class RecordingApplicationProbe:
    def __init__(self) -> None:
        self.calls = 0

    def read(self) -> ApplicationMetadata:
        self.calls += 1
        return ApplicationMetadata("Synthetic Editor", "com.example.editor")


class FakeQuartz:
    kCGEventSourceStateCombinedSessionState = 1
    kCGAnyInputEventType = 2

    def __init__(self, result: float = 12.5) -> None:
        self.result = result
        self.arguments: tuple[int, int] | None = None

    def CGEventSourceSecondsSinceLastEventType(
        self, state_id: int, event_type: int
    ) -> float:
        self.arguments = (state_id, event_type)
        return self.result


def test_workspace_probe_normalizes_public_application_identity() -> None:
    metadata = WorkspaceApplicationProbe(FakeWorkspace()).read()

    assert metadata.app_name == "Synthetic Editor"
    assert metadata.app_bundle_id == "com.example.editor"


def test_system_state_precedence_avoids_idle_probe_when_not_active() -> None:
    signals = SystemSignals()
    idle = FixedIdle(0)
    probe = ResolvedSystemStateProbe(
        signals=signals,
        idle=idle,
        idle_threshold_seconds=300,
    )

    signals.session_resigned_active()
    assert probe.current_state() is ActivityState.LOCKED
    signals.will_sleep()
    assert probe.current_state() is ActivityState.ASLEEP
    signals.did_wake()
    assert probe.current_state() is ActivityState.LOCKED
    signals.session_became_active()
    assert probe.current_state() is ActivityState.ACTIVE
    assert idle.calls == 1


def test_idle_threshold_is_inclusive() -> None:
    signals = SystemSignals()
    probe = ResolvedSystemStateProbe(
        signals=signals,
        idle=FixedIdle(300),
        idle_threshold_seconds=300,
    )

    assert probe.current_state() is ActivityState.IDLE


@pytest.mark.parametrize("invalid", [-1.0, float("nan"), float("inf")])
def test_invalid_idle_duration_is_actionable(invalid: float) -> None:
    probe = ResolvedSystemStateProbe(
        signals=SystemSignals(),
        idle=FixedIdle(invalid),
        idle_threshold_seconds=300,
    )

    with pytest.raises(CollectorUnavailableError, match="invalid idle duration"):
        probe.current_state()


def test_sampler_never_reads_application_in_non_active_state() -> None:
    application = RecordingApplicationProbe()
    sampler = MacOSActivitySampler(
        clock=FixedClock(NOW),
        state=FixedState(ActivityState.IDLE),
        application=application,
    )

    sample = sampler.sample()

    assert sample.activity_state is ActivityState.IDLE
    assert sample.app_name is None
    assert sample.app_bundle_id is None
    assert application.calls == 0


def test_sampler_reads_application_identity_only_when_active() -> None:
    application = RecordingApplicationProbe()
    sampler = MacOSActivitySampler(
        clock=FixedClock(NOW),
        state=FixedState(ActivityState.ACTIVE),
        application=application,
    )

    sample = sampler.sample()

    assert sample.activity_state is ActivityState.ACTIVE
    assert sample.app_name == "Synthetic Editor"
    assert sample.app_bundle_id == "com.example.editor"
    assert application.calls == 1


def test_quartz_probe_uses_combined_session_and_any_input() -> None:
    quartz = FakeQuartz()

    result = QuartzIdleSecondsProbe(quartz).seconds_since_input()

    assert result == 12.5
    assert quartz.arguments == (1, 2)
