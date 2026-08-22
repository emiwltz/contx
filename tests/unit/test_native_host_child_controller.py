"""The collector child keeps AppKit alive without creating a second menu."""

from types import SimpleNamespace

import pytest

from contx.controller import NativeHostChildController
from contx.errors import CollectorUnavailableError


class _Application:
    def __init__(self) -> None:
        self.policy = 0
        self.accept_policy = True

    def setActivationPolicy_(self, policy: int) -> None:
        if self.accept_policy:
            self.policy = policy

    def activationPolicy(self) -> int:
        return self.policy


def _appkit(application: _Application) -> SimpleNamespace:
    return SimpleNamespace(
        NSApplication=SimpleNamespace(
            sharedApplication=lambda: application,
        ),
        NSApplicationActivationPolicyAccessory=1,
    )


def test_native_host_child_is_accessory_only_and_has_no_status_item() -> None:
    application = _Application()
    appkit = _appkit(application)
    controller = NativeHostChildController(appkit=appkit)

    controller.start()
    assert application.policy == 1
    assert not hasattr(appkit, "NSStatusBar")
    assert controller.refresh() is None

    controller.stop()
    with pytest.raises(RuntimeError, match="not started"):
        controller.refresh()


def test_native_host_child_refuses_a_rejected_accessory_policy() -> None:
    application = _Application()
    application.accept_policy = False
    controller = NativeHostChildController(appkit=_appkit(application))

    with pytest.raises(CollectorUnavailableError, match="accessory"):
        controller.start()
