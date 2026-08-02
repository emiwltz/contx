"""Read frontmost macOS application metadata without window content."""

from __future__ import annotations

import sys
from datetime import timedelta
from importlib import import_module
from typing import Protocol, cast

from contx.errors import CollectorUnavailableError
from contx.models import Clock, IdentifierSource, Observation, SourceType
from contx.models.common import build_idempotency_key


class RunningApplication(Protocol):
    def localizedName(self) -> str | None: ...

    def bundleIdentifier(self) -> str | None: ...


class Workspace(Protocol):
    def frontmostApplication(self) -> RunningApplication | None: ...


class ActiveApplicationCollector:
    """Collect one frontmost-app sample only when called explicitly."""

    def __init__(
        self,
        *,
        clock: Clock,
        identifiers: IdentifierSource,
        workspace: Workspace | None = None,
        retention: timedelta = timedelta(hours=48),
    ) -> None:
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise ValueError("raw retention must be between zero and 48 hours")
        self._clock = clock
        self._identifiers = identifiers
        self._workspace = workspace
        self._retention = retention

    def collect(self) -> tuple[Observation, ...]:
        workspace = self._workspace or _load_workspace()
        try:
            application = workspace.frontmostApplication()
            if application is None:
                raise CollectorUnavailableError(
                    "macOS did not report a frontmost application"
                )
            name_value = application.localizedName()
            bundle_value = application.bundleIdentifier()
        except CollectorUnavailableError:
            raise
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot read frontmost macOS application metadata"
            ) from error

        app_name = None if name_value is None else str(name_value)
        bundle_id = None if bundle_value is None else str(bundle_value)
        if not app_name and not bundle_id:
            raise CollectorUnavailableError(
                "The frontmost macOS application has no usable identity metadata"
            )

        captured_at = self._clock.now()
        return (
            Observation(
                id=self._identifiers.new(),
                idempotency_key=build_idempotency_key(
                    "active-app-observation-v1",
                    captured_at,
                    app_name,
                    bundle_id,
                ),
                source_type=SourceType.ACTIVE_APP,
                captured_at=captured_at,
                started_at=captured_at,
                ended_at=captured_at,
                app_name=app_name,
                app_bundle_id=bundle_id,
                window_title=None,
                artifact_path=None,
                expires_at=captured_at + self._retention,
                created_at=captured_at,
            ),
        )


def _load_workspace() -> Workspace:
    if sys.platform != "darwin":
        raise CollectorUnavailableError(
            "Active-application collection is available only on macOS"
        )
    try:
        appkit = import_module("AppKit")
        workspace = appkit.NSWorkspace.sharedWorkspace()
    except (ImportError, AttributeError) as error:
        raise CollectorUnavailableError(
            "The macOS Cocoa bridge is not installed or unavailable"
        ) from error
    return cast(Workspace, workspace)
