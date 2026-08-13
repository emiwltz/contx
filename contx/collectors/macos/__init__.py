"""Explicit, local-only macOS collectors."""

from contx.collectors.macos.active_app import ActiveApplicationCollector
from contx.collectors.macos.activity import (
    ApplicationMetadata,
    MacOSActivitySampler,
    QuartzIdleSecondsProbe,
    ResolvedSystemStateProbe,
    SystemSignals,
    WorkspaceApplicationProbe,
)
from contx.collectors.macos.capabilities import (
    CapabilityStatus,
    CollectionCapability,
    detect_collection_capabilities,
)
from contx.collectors.macos.notifications import WorkspaceNotificationMonitor
from contx.collectors.macos.permissions import (
    MacOSPermission,
    PermissionRequestResult,
    request_collection_permission,
)
from contx.collectors.macos.screenshots import ScreenCaptureKitScreenshotSource
from contx.collectors.macos.window_titles import FocusedWindowTitleProbe

__all__ = [
    "ActiveApplicationCollector",
    "ApplicationMetadata",
    "CapabilityStatus",
    "CollectionCapability",
    "FocusedWindowTitleProbe",
    "MacOSActivitySampler",
    "MacOSPermission",
    "PermissionRequestResult",
    "QuartzIdleSecondsProbe",
    "ScreenCaptureKitScreenshotSource",
    "ResolvedSystemStateProbe",
    "SystemSignals",
    "WorkspaceApplicationProbe",
    "WorkspaceNotificationMonitor",
    "detect_collection_capabilities",
    "request_collection_permission",
]
