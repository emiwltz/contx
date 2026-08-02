"""Menu-bar state and controls use the durable collection service contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest

from contx.controller import MenuBarModel
from contx.controller.menu_bar import NativeMenuBarController
from contx.errors import CollectorUnavailableError
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


def test_partial_native_menu_start_removes_the_status_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    appkit.menu.fail_add = True
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = NativeMenuBarController(
        MenuBarModel(
            controls=RecordingControls(),
            clock=FixedClock(NOW),
            collection_enabled=True,
        ),
        appkit=appkit.module,
    )

    with pytest.raises(CollectorUnavailableError, match="Cannot create"):
        controller.start()

    assert appkit.status_bar.removed == [appkit.status_item]
    controller.stop()


class _FakeButton:
    pass


class _FakeStatusItem:
    def __init__(self) -> None:
        self._button = _FakeButton()

    def button(self) -> _FakeButton:
        return self._button

    def setMenu_(self, _menu: object) -> None:
        pass


class _FakeStatusBar:
    def __init__(self, item: _FakeStatusItem) -> None:
        self._item = item
        self.removed: list[object] = []

    def statusItemWithLength_(self, _length: object) -> _FakeStatusItem:
        return self._item

    def removeStatusItem_(self, item: object) -> None:
        self.removed.append(item)


class _FakeApplication:
    def setActivationPolicy_(self, _policy: object) -> None:
        pass


class _FakeMenu:
    def __init__(self) -> None:
        self.fail_add = False

    def alloc(self) -> _FakeMenu:
        return self

    def init(self) -> _FakeMenu:
        return self

    def addItem_(self, _item: object) -> None:
        if self.fail_add:
            raise RuntimeError("synthetic menu assembly failure")


class _FakeMenuItem:
    def alloc(self) -> _FakeMenuItem:
        return self

    def initWithTitle_action_keyEquivalent_(
        self,
        _title: str,
        _action: str | None,
        _key: str,
    ) -> _FakeMenuItem:
        return self

    def setEnabled_(self, _enabled: bool) -> None:
        pass

    def setTarget_(self, _target: object) -> None:
        pass

    def separatorItem(self) -> _FakeMenuItem:
        return self


class _FakeAppKit:
    def __init__(self) -> None:
        self.status_item = _FakeStatusItem()
        self.status_bar = _FakeStatusBar(self.status_item)
        self.application = _FakeApplication()
        self.menu = _FakeMenu()
        menu_item = _FakeMenuItem()
        module = ModuleType("FakeAppKit")
        module.NSApplication = _shared(self.application)  # type: ignore[attr-defined]
        module.NSApplicationActivationPolicyAccessory = object()  # type: ignore[attr-defined]
        module.NSStatusBar = _system(self.status_bar)  # type: ignore[attr-defined]
        module.NSVariableStatusItemLength = object()  # type: ignore[attr-defined]
        module.NSMenu = self.menu  # type: ignore[attr-defined]
        module.NSMenuItem = menu_item  # type: ignore[attr-defined]
        self.module = module


def _shared(application: _FakeApplication) -> Any:
    class ApplicationType:
        @staticmethod
        def sharedApplication() -> _FakeApplication:
            return application

    return ApplicationType


def _system(status_bar: _FakeStatusBar) -> Any:
    class StatusBarType:
        @staticmethod
        def systemStatusBar() -> _FakeStatusBar:
            return status_bar

    return StatusBarType
