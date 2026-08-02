"""Menu-bar state and controls use the durable collection service contract."""

from datetime import UTC, datetime, timedelta

from contx.controller import MenuBarModel
from contx.models import CollectionControl
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 15, 0, tzinfo=UTC)


class RecordingControls:
    def __init__(self) -> None:
        self.value = CollectionControl(updated_at=NOW)
        self.pauses: list[timedelta | None] = []
        self.resumes = 0

    def control(self) -> CollectionControl:
        return self.value

    def pause(self, *, duration: timedelta | None = None) -> CollectionControl:
        self.pauses.append(duration)
        self.value = self.value.pause(
            at=NOW,
            until=None if duration is None else NOW + duration,
        )
        return self.value

    def resume(self) -> CollectionControl:
        self.resumes += 1
        self.value = self.value.resume(at=NOW)
        return self.value


def test_enabled_model_exposes_active_pause_and_resume_states() -> None:
    controls = RecordingControls()
    model = MenuBarModel(
        controls=controls,
        clock=FixedClock(NOW),
        collection_enabled=True,
    )

    active = model.snapshot()
    model.pause_for_fifteen_minutes()
    paused = model.snapshot()
    model.resume()
    resumed = model.snapshot()

    assert active.status_text == "Collection active"
    assert active.pause_enabled and not active.resume_enabled
    assert controls.pauses == [timedelta(minutes=15)]
    assert paused.status_text == "Collection paused"
    assert not paused.pause_enabled and paused.resume_enabled
    assert controls.resumes == 1
    assert resumed.status_text == "Collection active"


def test_indefinite_pause_has_no_duration() -> None:
    controls = RecordingControls()
    model = MenuBarModel(
        controls=controls,
        clock=FixedClock(NOW),
        collection_enabled=True,
    )

    model.pause_indefinitely()

    assert controls.pauses == [None]
    assert model.snapshot().status_text == "Collection paused"


def test_disabled_model_does_not_mutate_control_state() -> None:
    controls = RecordingControls()
    model = MenuBarModel(
        controls=controls,
        clock=FixedClock(NOW),
        collection_enabled=False,
    )

    model.pause_for_fifteen_minutes()
    model.pause_indefinitely()
    model.resume()
    snapshot = model.snapshot()

    assert snapshot.status_text == "Collection disabled"
    assert not snapshot.pause_enabled
    assert not snapshot.resume_enabled
    assert controls.pauses == []
    assert controls.resumes == 0
