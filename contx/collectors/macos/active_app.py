"""Read frontmost macOS application metadata without window content."""

from __future__ import annotations

from datetime import timedelta

from contx.collectors.macos.activity import (
    ApplicationProbe,
    Workspace,
    WorkspaceApplicationProbe,
)
from contx.models import Clock, IdentifierSource, Observation, SourceType
from contx.models.common import build_idempotency_key


class ActiveApplicationCollector:
    """Collect one frontmost-app sample only when called explicitly."""

    def __init__(
        self,
        *,
        clock: Clock,
        identifiers: IdentifierSource,
        workspace: Workspace | None = None,
        application_probe: ApplicationProbe | None = None,
        retention: timedelta = timedelta(hours=48),
    ) -> None:
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise ValueError("raw retention must be between zero and 48 hours")
        self._clock = clock
        self._identifiers = identifiers
        if workspace is not None and application_probe is not None:
            raise ValueError("provide either a workspace or an application probe")
        self._application_probe = application_probe or WorkspaceApplicationProbe(
            workspace
        )
        self._retention = retention

    def collect(self) -> tuple[Observation, ...]:
        application = self._application_probe.read()

        captured_at = self._clock.now()
        return (
            Observation(
                id=self._identifiers.new(),
                idempotency_key=build_idempotency_key(
                    "active-app-observation-v1",
                    captured_at,
                    application.app_name,
                    application.app_bundle_id,
                ),
                source_type=SourceType.ACTIVE_APP,
                captured_at=captured_at,
                started_at=captured_at,
                ended_at=captured_at,
                app_name=application.app_name,
                app_bundle_id=application.app_bundle_id,
                window_title=None,
                artifact_path=None,
                expires_at=captured_at + self._retention,
                created_at=captured_at,
            ),
        )
