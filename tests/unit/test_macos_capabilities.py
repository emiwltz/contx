"""macOS capability detection is truthful and never prompts for access."""

from types import ModuleType

from contx.collectors.macos import (
    CapabilityStatus,
    CollectionCapability,
    detect_collection_capabilities,
)
from contx.settings import CollectionSettings


def test_non_macos_reports_every_capability_unavailable() -> None:
    capabilities = detect_collection_capabilities(
        CollectionSettings(), platform="linux"
    )

    assert {capability.status for capability in capabilities} == {
        CapabilityStatus.UNAVAILABLE
    }
    assert {capability.reason_code for capability in capabilities} == {"macos_required"}


def test_disabled_sensitive_features_do_not_run_permission_preflights() -> None:
    preflight_calls: list[str] = []

    def load_module(name: str) -> ModuleType:
        module = _module(name)
        if name == "AppKit":
            _add_cocoa_api(module)
        else:
            _add_quartz_api(module, preflight_calls)
        return module

    capabilities = _by_name(
        detect_collection_capabilities(
            CollectionSettings(), platform="darwin", module_loader=load_module
        )
    )

    assert capabilities["active_application"].status is CapabilityStatus.AVAILABLE
    assert capabilities["system_notifications"].status is CapabilityStatus.AVAILABLE
    assert capabilities["idle_detection"].status is CapabilityStatus.AVAILABLE
    assert capabilities["window_titles"].status is CapabilityStatus.DISABLED
    assert capabilities["screenshots"].status is CapabilityStatus.DISABLED
    assert preflight_calls == []


def test_enabled_features_report_missing_permissions_without_requesting_them() -> None:
    preflight_calls: list[str] = []

    def load_module(name: str) -> ModuleType:
        module = _module(name)
        if name == "AppKit":
            _add_cocoa_api(module)
        else:
            _add_quartz_api(module, preflight_calls)
        return module

    capabilities = _by_name(
        detect_collection_capabilities(
            CollectionSettings(window_titles_enabled=True, screenshots_enabled=True),
            platform="darwin",
            module_loader=load_module,
        )
    )

    assert capabilities["window_titles"].status is CapabilityStatus.PERMISSION_REQUIRED
    assert capabilities["screenshots"].status is CapabilityStatus.PERMISSION_REQUIRED
    assert capabilities["window_titles"].settings_path is not None
    assert capabilities["screenshots"].settings_path is not None
    assert preflight_calls == ["accessibility", "screen_capture"]


def test_missing_quartz_keeps_safe_cocoa_features_available() -> None:
    def load_module(name: str) -> ModuleType:
        if name == "Quartz":
            raise ImportError
        module = _module(name)
        _add_cocoa_api(module)
        return module

    capabilities = _by_name(
        detect_collection_capabilities(
            CollectionSettings(window_titles_enabled=True, screenshots_enabled=True),
            platform="darwin",
            module_loader=load_module,
        )
    )

    assert capabilities["active_application"].status is CapabilityStatus.AVAILABLE
    assert capabilities["idle_detection"].reason_code == "quartz_bridge_missing"
    assert capabilities["window_titles"].status is CapabilityStatus.UNAVAILABLE
    assert capabilities["screenshots"].status is CapabilityStatus.UNAVAILABLE


def _module(name: str) -> ModuleType:
    return ModuleType(name)


def _add_cocoa_api(module: ModuleType) -> None:
    module.NSWorkspace = object()  # type: ignore[attr-defined]
    module.NSWorkspaceDidActivateApplicationNotification = object()  # type: ignore[attr-defined]
    module.NSWorkspaceWillSleepNotification = object()  # type: ignore[attr-defined]
    module.NSWorkspaceDidWakeNotification = object()  # type: ignore[attr-defined]


def _add_quartz_api(module: ModuleType, calls: list[str]) -> None:
    module.CGEventSourceSecondsSinceLastEventType = lambda *_: 0.0  # type: ignore[attr-defined]
    module.kCGEventSourceStateCombinedSessionState = 0  # type: ignore[attr-defined]
    module.kCGAnyInputEventType = 0  # type: ignore[attr-defined]

    def accessibility() -> bool:
        calls.append("accessibility")
        return False

    def screen_capture() -> bool:
        calls.append("screen_capture")
        return False

    module.AXIsProcessTrusted = accessibility  # type: ignore[attr-defined]
    module.CGPreflightScreenCaptureAccess = screen_capture  # type: ignore[attr-defined]


def _by_name(
    capabilities: tuple[CollectionCapability, ...],
) -> dict[str, CollectionCapability]:
    return {capability.name: capability for capability in capabilities}
