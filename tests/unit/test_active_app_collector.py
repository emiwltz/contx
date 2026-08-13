"""Active-application metadata privacy and capability tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from contx.collectors.macos.active_app import ActiveApplicationCollector
from contx.errors import CollectorUnavailableError
from contx.models import SourceType
from tests.helpers import FixedClock, SequenceIdentifiers

NOW = datetime(2026, 8, 2, 11, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeApplication:
    def localizedName(self) -> str:
        return "Synthetic Codex"

    def bundleIdentifier(self) -> str:
        return "com.example.synthetic-codex"

    def processIdentifier(self) -> int:
        return 4242


class FakeWorkspace:
    def frontmostApplication(self) -> FakeApplication:
        return FakeApplication()


class EmptyWorkspace:
    def frontmostApplication(self) -> None:
        return None


def test_collects_identity_without_window_or_artifact() -> None:
    collector = ActiveApplicationCollector(
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((OBSERVATION_ID,)),
        workspace=FakeWorkspace(),
    )

    (observation,) = collector.collect()

    assert observation.source_type is SourceType.ACTIVE_APP
    assert observation.app_name == "Synthetic Codex"
    assert observation.app_bundle_id == "com.example.synthetic-codex"
    assert observation.window_title is None
    assert observation.artifact_path is None


def test_missing_frontmost_application_is_actionable() -> None:
    collector = ActiveApplicationCollector(
        clock=FixedClock(NOW),
        identifiers=SequenceIdentifiers((OBSERVATION_ID,)),
        workspace=EmptyWorkspace(),
    )

    with pytest.raises(CollectorUnavailableError, match="frontmost application"):
        collector.collect()
