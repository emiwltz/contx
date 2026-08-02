"""Deterministic synthetic observation collection."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from contx.models import Clock, IdentifierSource, Observation, SourceType
from contx.models.common import build_idempotency_key, parse_utc


class _FixtureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    captured_at: str
    started_at: str
    ended_at: str
    app_name: str = Field(min_length=1, max_length=255)
    app_bundle_id: str = Field(min_length=1, max_length=255)


class _Fixture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fixture_version: int
    observations: tuple[_FixtureObservation, ...] = Field(min_length=1)


class SyntheticCollector:
    """Replay a validated fixture without reading real user activity."""

    def __init__(
        self,
        fixture_path: Path,
        *,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> None:
        self._fixture_path = fixture_path
        self._clock = clock
        self._identifiers = identifiers

    @classmethod
    def default(
        cls,
        *,
        clock: Clock,
        identifiers: IdentifierSource,
    ) -> SyntheticCollector:
        fixture = files("contx.collectors").joinpath(
            "fixtures/synthetic_project_activity.json"
        )
        return cls(Path(str(fixture)), clock=clock, identifiers=identifiers)

    def collect(self) -> tuple[Observation, ...]:
        fixture = _Fixture.model_validate_json(
            self._fixture_path.read_text(encoding="utf-8")
        )
        observations: list[Observation] = []
        created_at = self._clock.now()
        for item in fixture.observations:
            captured_at = parse_utc(item.captured_at)
            started_at = parse_utc(item.started_at)
            ended_at = parse_utc(item.ended_at)
            observations.append(
                Observation(
                    id=self._identifiers.new(),
                    idempotency_key=build_idempotency_key(
                        "synthetic-observation-v1",
                        fixture.fixture_version,
                        captured_at,
                        started_at,
                        ended_at,
                        item.app_name,
                        item.app_bundle_id,
                    ),
                    source_type=SourceType.SYNTHETIC,
                    captured_at=captured_at,
                    started_at=started_at,
                    ended_at=ended_at,
                    app_name=item.app_name,
                    app_bundle_id=item.app_bundle_id,
                    created_at=created_at,
                )
            )
        return tuple(observations)
