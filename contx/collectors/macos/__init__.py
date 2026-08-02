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
from contx.collectors.macos.screenshots import QuartzScreenshotSource

__all__ = [
    "ActiveApplicationCollector",
    "ApplicationMetadata",
    "CapabilityStatus",
    "CollectionCapability",
    "MacOSActivitySampler",
    "QuartzIdleSecondsProbe",
    "QuartzScreenshotSource",
    "ResolvedSystemStateProbe",
    "SystemSignals",
    "WorkspaceApplicationProbe",
    "WorkspaceNotificationMonitor",
    "detect_collection_capabilities",
]
