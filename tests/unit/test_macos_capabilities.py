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
        elif name == "Quartz":
            _add_quartz_api(module, preflight_calls)
        else:
            _add_accessibility_api(module, preflight_calls)
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
        elif name == "Quartz":
            _add_quartz_api(module, preflight_calls)
        else:
            _add_accessibility_api(module, preflight_calls)
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
        if name == "AppKit":
            _add_cocoa_api(module)
        else:
            _add_accessibility_api(module, [])
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
    assert capabilities["window_titles"].status is CapabilityStatus.PERMISSION_REQUIRED
    assert capabilities["screenshots"].status is CapabilityStatus.UNAVAILABLE


def test_legacy_quartz_capture_api_is_not_reported_as_available() -> None:
    preflight_calls: list[str] = []

    def load_module(name: str) -> ModuleType:
        module = _module(name)
        if name == "AppKit":
            _add_cocoa_api(module)
        elif name == "Quartz":
            _add_quartz_api(module, preflight_calls)
            del module.SCScreenshotManager  # type: ignore[attr-defined]
        else:
            _add_accessibility_api(module, preflight_calls)
        return module

    capabilities = _by_name(
        detect_collection_capabilities(
            CollectionSettings(screenshots_enabled=True),
            platform="darwin",
            module_loader=load_module,
        )
    )

    assert capabilities["screenshots"].status is CapabilityStatus.UNAVAILABLE
    assert capabilities["screenshots"].reason_code == "screen_capture_api_unavailable"
    assert "screen_capture" not in preflight_calls


def _module(name: str) -> ModuleType:
    return ModuleType(name)


def _add_cocoa_api(module: ModuleType) -> None:
    module.NSWorkspace = object()  # type: ignore[attr-defined]
    module.NSWorkspaceDidActivateApplicationNotification = object()  # type: ignore[attr-defined]
    module.NSWorkspaceSessionDidBecomeActiveNotification = object()  # type: ignore[attr-defined]
    module.NSWorkspaceSessionDidResignActiveNotification = object()  # type: ignore[attr-defined]
    module.NSWorkspaceWillSleepNotification = object()  # type: ignore[attr-defined]
    module.NSWorkspaceDidWakeNotification = object()  # type: ignore[attr-defined]


def _add_quartz_api(module: ModuleType, calls: list[str]) -> None:
    module.CGEventSourceSecondsSinceLastEventType = lambda *_: 0.0  # type: ignore[attr-defined]
    module.kCGEventSourceStateCombinedSessionState = 0  # type: ignore[attr-defined]
    module.kCGAnyInputEventType = 0  # type: ignore[attr-defined]

    def screen_capture() -> bool:
        calls.append("screen_capture")
        return False

    module.CGPreflightScreenCaptureAccess = screen_capture  # type: ignore[attr-defined]
    module.kCGWindowListOptionOnScreenOnly = 1  # type: ignore[attr-defined]
    module.kCGWindowListExcludeDesktopElements = 16  # type: ignore[attr-defined]
    module.kCGNullWindowID = 0  # type: ignore[attr-defined]
    module.kCGWindowNumber = "number"  # type: ignore[attr-defined]
    module.kCGWindowLayer = "layer"  # type: ignore[attr-defined]
    module.kCGWindowAlpha = "alpha"  # type: ignore[attr-defined]
    module.kCGWindowOwnerPID = "owner-pid"  # type: ignore[attr-defined]
    module.kCGWindowBounds = "bounds"  # type: ignore[attr-defined]
    module.CGWindowListCopyWindowInfo = lambda *_: ()  # type: ignore[attr-defined]
    module.SCShareableContent = _native_type(  # type: ignore[attr-defined]
        "getShareableContentExcludingDesktopWindows_"
        "onScreenWindowsOnly_completionHandler_"
    )
    module.SCContentFilter = _native_type(  # type: ignore[attr-defined]
        "initWithDesktopIndependentWindow_",
        "contentRect",
        "pointPixelScale",
    )
    module.SCStreamConfiguration = _native_type(  # type: ignore[attr-defined]
        "alloc",
        "setWidth_",
        "setHeight_",
        "setIgnoreShadowsSingleWindow_",
    )
    module.SCScreenshotManager = _native_type(  # type: ignore[attr-defined]
        "captureImageWithFilter_configuration_completionHandler_"
    )
    module.CFDataCreateMutable = lambda *_: bytearray()  # type: ignore[attr-defined]
    module.CGImageDestinationCreateWithData = lambda *_: object()  # type: ignore[attr-defined]
    module.CGImageDestinationAddImage = lambda *_: None  # type: ignore[attr-defined]
    module.CGImageDestinationFinalize = lambda *_: True  # type: ignore[attr-defined]


def _native_type(*selectors: str) -> type[object]:
    return type(
        "SyntheticNativeType", (), {selector: object() for selector in selectors}
    )


def _add_accessibility_api(module: ModuleType, calls: list[str]) -> None:
    def accessibility() -> bool:
        calls.append("accessibility")
        return False

    module.AXIsProcessTrusted = accessibility  # type: ignore[attr-defined]
    module.AXUIElementCreateSystemWide = lambda: object()  # type: ignore[attr-defined]
    module.AXUIElementCopyAttributeValue = lambda *_: (0, None)  # type: ignore[attr-defined]
    module.kAXFocusedApplicationAttribute = "app"  # type: ignore[attr-defined]
    module.kAXFocusedWindowAttribute = "window"  # type: ignore[attr-defined]
    module.kAXTitleAttribute = "title"  # type: ignore[attr-defined]
    module.kAXErrorSuccess = 0  # type: ignore[attr-defined]
    module.kAXErrorNoValue = -1  # type: ignore[attr-defined]
    module.kAXErrorAttributeUnsupported = -2  # type: ignore[attr-defined]


def _by_name(
    capabilities: tuple[CollectionCapability, ...],
) -> dict[str, CollectionCapability]:
    return {capability.name: capability for capability in capabilities}
