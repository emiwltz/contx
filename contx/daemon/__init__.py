"""Local collection daemon primitives."""

from contx.daemon.appkit_loop import AppKitDaemonRunner, AppKitTimerScheduler
from contx.daemon.factory import (
    ConfiguredMacOSCollectionDaemon,
    build_macos_collection_daemon,
)
from contx.daemon.launch_agent import (
    LAUNCH_AGENT_LABEL,
    launch_agent_program_arguments,
    render_launch_agent,
)
from contx.daemon.lease import DaemonLease, DaemonLeaseStatus, probe_daemon_lease
from contx.daemon.lifecycle import CollectionDaemonLifecycle
from contx.daemon.signals import GracefulStopSignalBridge

__all__ = [
    "CollectionDaemonLifecycle",
    "ConfiguredMacOSCollectionDaemon",
    "AppKitDaemonRunner",
    "AppKitTimerScheduler",
    "DaemonLease",
    "DaemonLeaseStatus",
    "GracefulStopSignalBridge",
    "LAUNCH_AGENT_LABEL",
    "launch_agent_program_arguments",
    "probe_daemon_lease",
    "render_launch_agent",
    "build_macos_collection_daemon",
]
