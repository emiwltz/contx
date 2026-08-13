"""Explicit macOS permission requests remain narrow and user-selected."""

from types import ModuleType

import pytest

from contx.collectors.macos import (
    MacOSPermission,
    request_collection_permission,
)
from contx.errors import CollectorUnavailableError


def test_accessibility_request_uses_the_native_prompt_option_once() -> None:
    module = ModuleType("ApplicationServices")
    module.kAXTrustedCheckOptionPrompt = "prompt"  # type: ignore[attr-defined]
    calls: list[dict[str, bool]] = []

    def request(options: dict[str, bool]) -> bool:
        calls.append(options)
        return False

    module.AXIsProcessTrustedWithOptions = request  # type: ignore[attr-defined]

    result = request_collection_permission(
        MacOSPermission.ACCESSIBILITY,
        platform="darwin",
        module_loader=lambda _name: module,
    )

    assert not result.granted
    assert calls == [{"prompt": True}]
    assert result.settings_path.endswith("Accessibility")


def test_screen_recording_request_calls_only_the_screen_api() -> None:
    module = ModuleType("Quartz")
    calls = 0

    def request() -> bool:
        nonlocal calls
        calls += 1
        return True

    module.CGRequestScreenCaptureAccess = request  # type: ignore[attr-defined]

    result = request_collection_permission(
        MacOSPermission.SCREEN_RECORDING,
        platform="darwin",
        module_loader=lambda _name: module,
    )

    assert result.granted
    assert calls == 1
    assert result.settings_path.endswith("Screen & System Audio Recording")


def test_permission_request_rejects_other_platforms_before_loading_a_module() -> None:
    with pytest.raises(CollectorUnavailableError, match="only on macOS"):
        request_collection_permission(
            MacOSPermission.ACCESSIBILITY,
            platform="linux",
            module_loader=lambda _name: pytest.fail("must not load a native module"),
        )


@pytest.mark.parametrize(
    "permission",
    (MacOSPermission.ACCESSIBILITY, MacOSPermission.SCREEN_RECORDING),
)
def test_missing_native_request_api_is_actionable(permission: MacOSPermission) -> None:
    with pytest.raises(
        CollectorUnavailableError, match="permission API is unavailable"
    ):
        request_collection_permission(
            permission,
            platform="darwin",
            module_loader=lambda name: ModuleType(name),
        )
