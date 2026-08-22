"""Validated composition root for the disabled-by-default macOS daemon."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta

from sqlalchemy import Engine

from contx.application import (
    ContinuousCollectionResult,
    ContinuousCollectionSession,
    RawPurgeService,
)
from contx.collection import (
    ActivitySampler,
    CollectionControlService,
    CollectionPolicy,
    ContinuousActivityCollector,
    ContinuousObservationCollector,
    ScreenshotSource,
    SelectiveScreenshotPlanner,
    SelectiveScreenshotService,
)
from contx.collectors.macos import (
    CoreGraphicsFocusedWindowProbe,
    FocusedWindowTitleProbe,
    MacOSActivitySampler,
    QuartzIdleSecondsProbe,
    ResolvedSystemStateProbe,
    ScreenCaptureKitScreenshotSource,
    SystemSignals,
    WorkspaceApplicationProbe,
    WorkspaceNotificationMonitor,
)
from contx.controller import MenuBarModel, NativeMenuBarController
from contx.daemon.appkit_loop import (
    AppKitDaemonRunner,
    ApplicationLoop,
    CallbackScheduler,
)
from contx.daemon.lease import DaemonLease
from contx.daemon.lifecycle import (
    CollectionDaemonLifecycle,
    NativeMenu,
    NativeMonitor,
    ProcessLease,
)
from contx.daemon.signals import GracefulStopSignalBridge, SignalApi
from contx.db import create_database_engine, upgrade_database
from contx.errors import ConfigurationError
from contx.models import Clock, SystemClock, UuidIdentifierSource
from contx.raw_store import FilesystemRawStore
from contx.settings import (
    RuntimePaths,
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
)


class ConfiguredMacOSCollectionDaemon:
    """Own the runner and database resources created by the composition root."""

    def __init__(
        self,
        *,
        runner: AppKitDaemonRunner,
        engine: Engine,
        signal_api: SignalApi | None = None,
    ) -> None:
        self._runner = runner
        self._engine = engine
        self._signals = (
            GracefulStopSignalBridge(self.request_stop)
            if signal_api is None
            else GracefulStopSignalBridge(
                self.request_stop,
                signal_api=signal_api,
            )
        )
        self._closed = False

    def request_stop(self) -> None:
        self._runner.request_stop()

    def run(self) -> ContinuousCollectionResult | None:
        if self._closed:
            raise RuntimeError("configured collection daemon is closed")
        try:
            with self._signals:
                return self._runner.run()
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._engine.dispose()

    def __enter__(self) -> ConfiguredMacOSCollectionDaemon:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


def build_macos_collection_daemon(
    *,
    paths: RuntimePaths | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    sampler: ActivitySampler | None = None,
    screenshot_source: ScreenshotSource | None = None,
    notifications: NativeMonitor | None = None,
    menu: NativeMenu | None = None,
    lease: ProcessLease | None = None,
    application: ApplicationLoop | None = None,
    scheduler: CallbackScheduler | None = None,
    stop_event_factory: Callable[[], object] | None = None,
    signal_api: SignalApi | None = None,
) -> ConfiguredMacOSCollectionDaemon:
    """Build but do not start one daemon after an explicit configuration gate."""
    runtime_paths = paths or resolve_runtime_paths(environ)
    initialize_runtime_paths(runtime_paths)
    settings = load_settings(runtime_paths, environ)
    collection = settings.collection
    if not collection.background_collection_enabled:
        raise ConfigurationError(
            "Background collection is disabled in CONTX configuration"
        )
    engine: Engine | None = None
    try:
        upgrade_database(runtime_paths.database_file)
        engine = create_database_engine(runtime_paths.database_file)
        daemon_clock = clock or SystemClock()
        identifiers = UuidIdentifierSource()
        controls = CollectionControlService(engine=engine, clock=daemon_clock)
        controls.initialize()
        retention = timedelta(hours=collection.raw_retention_hours)
        raw_store = FilesystemRawStore(
            runtime_paths.raw,
            disk_budget_bytes=collection.raw_disk_budget_mb * 1024 * 1024,
        )
        signals = SystemSignals()
        application_probe = WorkspaceApplicationProbe()
        activity_sampler = sampler or MacOSActivitySampler(
            clock=daemon_clock,
            state=ResolvedSystemStateProbe(
                signals=signals,
                idle=QuartzIdleSecondsProbe(),
                idle_threshold_seconds=collection.idle_threshold_seconds,
            ),
            application=application_probe,
            window=(
                CoreGraphicsFocusedWindowProbe()
                if collection.screenshots_enabled
                else None
            ),
        )
        policy = CollectionPolicy()
        activity = ContinuousActivityCollector(
            activity_sampler,
            controls=controls,
            policy=policy,
            clock=daemon_clock,
            identifiers=identifiers,
            retention=retention,
            maximum_segment_duration=timedelta(
                seconds=collection.segment_max_duration_seconds
            ),
            retain_excluded_activity=collection.retain_excluded_activity,
        )
        window_title_probe = (
            FocusedWindowTitleProbe() if collection.window_titles_enabled else None
        )
        screenshots = (
            None
            if not collection.screenshots_enabled
            else SelectiveScreenshotService(
                planner=SelectiveScreenshotPlanner(
                    policy=policy,
                    enabled=True,
                    minimum_interval=timedelta(
                        seconds=collection.screenshot_min_interval_seconds
                    ),
                    maximum_interval=timedelta(
                        seconds=collection.screenshot_max_interval_seconds
                    ),
                ),
                source=screenshot_source
                or ScreenCaptureKitScreenshotSource(
                    application=application_probe,
                    window_title=window_title_probe,
                ),
                raw_store=raw_store,
                retention=retention,
            )
        )
        collector = ContinuousObservationCollector(
            sampler=activity_sampler,
            controls=controls,
            activity=activity,
            screenshots=screenshots,
            window_titles=window_title_probe,
            policy=policy,
            clock=daemon_clock,
        )
        session = ContinuousCollectionSession(
            engine=engine,
            collector=collector,
            purge=RawPurgeService(
                engine=engine,
                raw_store=raw_store,
                clock=daemon_clock,
                identifiers=identifiers,
            ),
            clock=daemon_clock,
            identifiers=identifiers,
            purge_interval=timedelta(seconds=collection.purge_interval_seconds),
        )
        lifecycle = CollectionDaemonLifecycle(
            lease=lease or DaemonLease(runtime_paths.daemon_lock),
            notifications=notifications or WorkspaceNotificationMonitor(signals),
            menu=menu
            or NativeMenuBarController(
                MenuBarModel(
                    controls=controls,
                    clock=daemon_clock,
                    collection_enabled=True,
                )
            ),
            session=session,
        )
        runner = AppKitDaemonRunner(
            lifecycle=lifecycle,
            poll_interval=timedelta(seconds=collection.poll_interval_seconds),
            application=application,
            scheduler=scheduler,
            stop_event_factory=stop_event_factory,
        )
    except Exception:
        if engine is not None:
            engine.dispose()
        raise
    return ConfiguredMacOSCollectionDaemon(
        runner=runner,
        engine=engine,
        signal_api=signal_api,
    )
