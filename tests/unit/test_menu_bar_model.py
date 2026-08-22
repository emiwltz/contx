"""Menu-bar state and controls use the durable collection service contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Any

import pytest

from contx.controller import MenuBarModel
from contx.controller.menu_bar import (
    ACTIVE_STATUS_SYMBOL,
    DISABLED_STATUS_SYMBOL,
    PAUSED_STATUS_SYMBOL,
    NativeMenuBarController,
)
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
    assert active.button_symbol_name == ACTIVE_STATUS_SYMBOL
    assert active.pause_enabled and not active.resume_enabled
    assert controls.pauses == [timedelta(minutes=15)]
    assert paused.status_text == "Collection paused"
    assert paused.button_symbol_name == PAUSED_STATUS_SYMBOL
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
    assert snapshot.button_symbol_name == DISABLED_STATUS_SYMBOL
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


def test_native_menu_refuses_a_rejected_accessory_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    appkit.application.policy_changed = False
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = _controller(appkit)

    with pytest.raises(CollectorUnavailableError, match="accessory"):
        controller.start()

    assert appkit.status_bar.removed == []


def test_native_menu_accepts_an_existing_accessory_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    appkit.application.policy_changed = False
    appkit.application.activation_policy = appkit.accessory_policy
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = _controller(appkit)

    controller.start()

    controller.stop()
    assert appkit.status_bar.removed == [appkit.status_item]


def test_native_menu_refuses_an_invisible_status_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    appkit.status_item.visible = False
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = _controller(appkit)

    with pytest.raises(CollectorUnavailableError, match="visible"):
        controller.start()

    assert appkit.status_bar.removed == [appkit.status_item]


def test_native_menu_refuses_a_missing_compact_status_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    appkit.images.missing.add(ACTIVE_STATUS_SYMBOL)
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = _controller(appkit)

    with pytest.raises(CollectorUnavailableError, match="status symbol"):
        controller.start()

    assert appkit.status_bar.requested_length is None


def test_native_menu_applies_initial_state_before_reporting_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = _controller(appkit)

    controller.start()

    assert appkit.status_bar.requested_length is appkit.square_length
    assert appkit.status_item.button().title == ""
    assert appkit.status_item.button().image is not None
    assert appkit.status_item.button().image.symbol_name == ACTIVE_STATUS_SYMBOL
    assert appkit.status_item.button().tooltip == "CONTX — Collection active"
    controller.stop()
    assert appkit.status_bar.removed == [appkit.status_item]


def test_native_menu_switches_the_compact_symbol_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    appkit = _FakeAppKit()
    controls = RecordingControls()
    monkeypatch.setattr(
        "contx.controller.menu_bar._build_action_target",
        lambda _owner: object(),
    )
    controller = NativeMenuBarController(
        MenuBarModel(
            controls=controls,
            clock=FixedClock(NOW),
            collection_enabled=True,
        ),
        appkit=appkit.module,
    )
    controller.start()

    controls.pause()
    snapshot = controller.refresh()

    assert snapshot.button_symbol_name == PAUSED_STATUS_SYMBOL
    assert appkit.status_item.button().image is not None
    assert appkit.status_item.button().image.symbol_name == PAUSED_STATUS_SYMBOL
    controller.stop()


def _controller(appkit: _FakeAppKit) -> NativeMenuBarController:
    return NativeMenuBarController(
        MenuBarModel(
            controls=RecordingControls(),
            clock=FixedClock(NOW),
            collection_enabled=True,
        ),
        appkit=appkit.module,
    )


class _FakeButton:
    def __init__(self) -> None:
        self.title: str | None = None
        self.tooltip: str | None = None
        self.image: _FakeImage | None = None

    def setTitle_(self, title: str) -> None:
        self.title = title

    def setToolTip_(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setImage_(self, image: _FakeImage) -> None:
        self.image = image


class _FakeImage:
    def __init__(self, symbol_name: str, description: str) -> None:
        self.symbol_name = symbol_name
        self.description = description
        self.is_template = False

    def setTemplate_(self, is_template: bool) -> None:
        self.is_template = is_template


class _FakeImages:
    def __init__(self) -> None:
        self.missing: set[str] = set()

    def imageWithSystemSymbolName_accessibilityDescription_(
        self,
        symbol_name: str,
        description: str,
    ) -> _FakeImage | None:
        if symbol_name in self.missing:
            return None
        return _FakeImage(symbol_name, description)


class _FakeStatusItem:
    def __init__(self) -> None:
        self._button = _FakeButton()
        self.visible = True

    def button(self) -> _FakeButton:
        return self._button

    def setMenu_(self, _menu: object) -> None:
        pass

    def isVisible(self) -> bool:
        return self.visible


class _FakeStatusBar:
    def __init__(self, item: _FakeStatusItem) -> None:
        self._item = item
        self.removed: list[object] = []
        self.requested_length: object | None = None

    def statusItemWithLength_(self, length: object) -> _FakeStatusItem:
        self.requested_length = length
        return self._item

    def removeStatusItem_(self, item: object) -> None:
        self.removed.append(item)


class _FakeApplication:
    def __init__(self) -> None:
        self.policy_changed = True
        self.activation_policy: object | None = None

    def setActivationPolicy_(self, _policy: object) -> bool:
        if self.policy_changed:
            self.activation_policy = _policy
        return self.policy_changed

    def activationPolicy(self) -> object | None:
        return self.activation_policy


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
    def __init__(self) -> None:
        self.title: str | None = None

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

    def setTitle_(self, title: str) -> None:
        self.title = title

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
        self.images = _FakeImages()
        self.accessory_policy = object()
        self.square_length = object()
        menu_item = _FakeMenuItem()
        module = ModuleType("FakeAppKit")
        module.NSApplication = _shared(self.application)  # type: ignore[attr-defined]
        module.NSApplicationActivationPolicyAccessory = self.accessory_policy  # type: ignore[attr-defined]
        module.NSStatusBar = _system(self.status_bar)  # type: ignore[attr-defined]
        module.NSSquareStatusItemLength = self.square_length  # type: ignore[attr-defined]
        module.NSImage = self.images  # type: ignore[attr-defined]
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
