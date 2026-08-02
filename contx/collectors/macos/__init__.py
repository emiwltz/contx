"""Explicit, local-only macOS collectors."""

from contx.collectors.macos.active_app import ActiveApplicationCollector
from contx.collectors.macos.capabilities import (
    CapabilityStatus,
    CollectionCapability,
    detect_collection_capabilities,
)

__all__ = [
    "ActiveApplicationCollector",
    "CapabilityStatus",
    "CollectionCapability",
    "detect_collection_capabilities",
]
