"""Deterministic sessionization of validated local-model evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from contx.model_provider import ModelTransformation, ModelTransformationStatus
from contx.models import (
    EpistemicStatus,
    Event,
    EventType,
    Observation,
    ObservationStatus,
    Sensitivity,
)
from contx.models.common import build_idempotency_key
from contx.models.sources import Clock

SESSION_EVENT_PROCESSING_VERSION = "session-events-v1"
DEFAULT_SESSION_GAP = timedelta(minutes=10)
DEFAULT_MAX_SESSION_DURATION = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class ModelEventEvidence:
    """One successful transformation and its exact source observations."""

    transformation: ModelTransformation
    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        transformation = self.transformation
        if (
            transformation.status is not ModelTransformationStatus.SUCCEEDED
            or transformation.interpretation is None
        ):
            raise ValueError("session evidence requires a successful transformation")
        by_id = {observation.id: observation for observation in self.observations}
        if len(by_id) != len(self.observations) or set(by_id) != set(
            transformation.source_observation_ids
        ):
            raise ValueError("session observations do not match model provenance")
        if any(
            observation.excluded
            or observation.processing_status is ObservationStatus.REJECTED
            for observation in self.observations
        ):
            raise ValueError("unavailable observations cannot form a session")

    @property
    def started_at(self) -> datetime:
        return min(
            observation.started_at or observation.captured_at
            for observation in self.observations
        )

    @property
    def ended_at(self) -> datetime:
        return max(
            observation.ended_at or observation.captured_at
            for observation in self.observations
        )

    @property
    def projects(self) -> frozenset[str]:
        interpretation = self.transformation.interpretation
        assert interpretation is not None
        return frozenset(project.casefold() for project in interpretation.projects)

    @property
    def applications(self) -> frozenset[str]:
        return frozenset(
            observation.app_bundle_id.casefold()
            for observation in self.observations
            if observation.app_bundle_id
        )

    @property
    def event_type(self) -> EventType:
        interpretation = self.transformation.interpretation
        assert interpretation is not None
        return normalize_event_type(interpretation.activity_type)


@dataclass(frozen=True, slots=True)
class ModelActivitySession:
    """One bounded, ordered group of semantically compatible evidence."""

    evidence: tuple[ModelEventEvidence, ...]

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError("an activity session requires evidence")
        if tuple(sorted(self.evidence, key=_evidence_order)) != self.evidence:
            raise ValueError("activity session evidence must be ordered")

    @property
    def started_at(self) -> datetime:
        return self.evidence[0].started_at

    @property
    def ended_at(self) -> datetime:
        return max(item.ended_at for item in self.evidence)

    @property
    def transformation_ids(self) -> tuple[UUID, ...]:
        return tuple(item.transformation.id for item in self.evidence)


class ModelActivitySessionizer:
    """Group evidence by time, application, project, and bounded semantics."""

    def __init__(
        self,
        *,
        session_gap: timedelta = DEFAULT_SESSION_GAP,
        max_session_duration: timedelta = DEFAULT_MAX_SESSION_DURATION,
    ) -> None:
        if not timedelta(seconds=30) <= session_gap <= timedelta(hours=1):
            raise ValueError("session gap must be between 30 seconds and 1 hour")
        if not timedelta(minutes=5) <= max_session_duration <= timedelta(hours=4):
            raise ValueError(
                "maximum session duration must be between 5 minutes and 4 hours"
            )
        if max_session_duration <= session_gap:
            raise ValueError("maximum session duration must exceed the session gap")
        self.session_gap = session_gap
        self.max_session_duration = max_session_duration

    def group(
        self,
        evidence: tuple[ModelEventEvidence, ...],
    ) -> tuple[ModelActivitySession, ...]:
        ordered = tuple(sorted(evidence, key=_evidence_order))
        groups: list[list[ModelEventEvidence]] = []
        for item in ordered:
            if not groups or not self._can_append(groups[-1], item):
                groups.append([item])
            else:
                groups[-1].append(item)
        return tuple(ModelActivitySession(tuple(group)) for group in groups)

    def _can_append(
        self,
        current: list[ModelEventEvidence],
        candidate: ModelEventEvidence,
    ) -> bool:
        first = current[0]
        previous = current[-1]
        if candidate.started_at - previous.ended_at > self.session_gap:
            return False
        if candidate.ended_at - first.started_at > self.max_session_duration:
            return False

        current_projects = frozenset(
            project for item in current for project in item.projects
        )
        if current_projects and candidate.projects:
            shared_project = bool(current_projects & candidate.projects)
            if not shared_project:
                return False
        else:
            shared_project = False

        current_apps = frozenset(app for item in current for app in item.applications)
        if (
            current_apps
            and candidate.applications
            and not (current_apps & candidate.applications)
            and not shared_project
        ):
            return False

        current_types = frozenset(item.event_type for item in current)
        if candidate.event_type in current_types:
            return True
        project_types = {
            EventType.PROJECT_WORK,
            EventType.RESEARCH,
            EventType.PLANNING,
        }
        return (
            shared_project
            and current_types <= project_types
            and (candidate.event_type in project_types)
        )


class SessionizedModelEventBuilder:
    """Aggregate one session without a second semantic model call."""

    def __init__(
        self,
        *,
        clock: Clock,
        processing_version: str = SESSION_EVENT_PROCESSING_VERSION,
    ) -> None:
        normalized = processing_version.strip()
        if (
            not normalized
            or len(normalized) > 64
            or any(character in normalized for character in "\r\n")
        ):
            raise ValueError("event processing version is invalid")
        self._clock = clock
        self.processing_version = normalized

    def build(self, session: ModelActivitySession) -> Event:
        evidence = session.evidence
        observations = _ordered_unique_observations(evidence)
        transformations = tuple(item.transformation for item in evidence)
        interpretations = tuple(
            transformation.interpretation for transformation in transformations
        )
        if any(interpretation is None for interpretation in interpretations):
            raise ValueError("session interpretation unexpectedly missing")
        present_interpretations = tuple(
            interpretation
            for interpretation in interpretations
            if interpretation is not None
        )

        lineage_key = build_idempotency_key(
            "event-lineage-v1",
            tuple(observation.idempotency_key for observation in observations),
        )
        idempotency_key = build_idempotency_key(
            "session-event-v1",
            self.processing_version,
            lineage_key,
        )
        started_at = session.started_at
        ended_at = session.ended_at
        timestamp = self._clock.now()
        inferred_context = _unique_text(
            statement
            for interpretation in present_interpretations
            for statement in interpretation.inferred_context
        )
        sensitivities = tuple(
            interpretation.sensitivity for interpretation in present_interpretations
        )
        source_types = tuple(item.event_type for item in evidence)
        return Event(
            id=uuid5(NAMESPACE_URL, f"contx:session-event:{idempotency_key}"),
            idempotency_key=idempotency_key,
            lineage_key=lineage_key,
            type=_aggregate_event_type(source_types),
            summary=_aggregate_summary(
                tuple(
                    interpretation.summary for interpretation in present_interpretations
                )
            ),
            facts={
                "observed_facts": list(
                    _unique_text(
                        statement
                        for interpretation in present_interpretations
                        for statement in interpretation.observed_facts
                    )
                ),
                "inferred_context": list(inferred_context),
                "source_activity_types": list(
                    _unique_text(
                        interpretation.activity_type
                        for interpretation in present_interpretations
                    )
                ),
                "sensitive_categories": list(
                    _unique_text(
                        category.value
                        for interpretation in present_interpretations
                        for category in interpretation.sensitive_categories
                    )
                ),
                "memory_relevance": max(
                    interpretation.memory_relevance
                    for interpretation in present_interpretations
                ),
                "session": {
                    "transformation_count": len(transformations),
                    "observation_count": len(observations),
                },
            },
            started_at=started_at,
            ended_at=ended_at,
            valid_from=started_at,
            valid_until=ended_at,
            epistemic_status=(
                EpistemicStatus.INFERRED
                if inferred_context
                else EpistemicStatus.OBSERVED
            ),
            confidence=min(
                interpretation.confidence for interpretation in present_interpretations
            ),
            sensitivity=max(sensitivities, key=_sensitivity_rank),
            projects=_unique_text(
                project
                for interpretation in present_interpretations
                for project in interpretation.projects
            ),
            entities=_unique_text(
                entity
                for interpretation in present_interpretations
                for entity in interpretation.entities
            ),
            source_observation_ids=tuple(
                observation.id for observation in observations
            ),
            processing_version=self.processing_version,
            created_at=timestamp,
            updated_at=timestamp,
        )


def normalize_event_type(activity_type: str) -> EventType:
    """Map the model's bounded activity label into the stable event vocabulary."""
    normalized = activity_type.casefold()
    if normalized in {"coding", "testing", "document_editing", "project_work"}:
        return EventType.PROJECT_WORK
    try:
        return EventType(normalized)
    except ValueError:
        return EventType.OTHER


def _aggregate_event_type(types: tuple[EventType, ...]) -> EventType:
    distinct = set(types)
    if len(distinct) == 1:
        return types[0]
    project_types = {
        EventType.PROJECT_WORK,
        EventType.RESEARCH,
        EventType.PLANNING,
    }
    return (
        EventType.PROJECT_WORK
        if distinct <= project_types
        else EventType.MIXED_ACTIVITY
    )


def _aggregate_summary(summaries: tuple[str, ...]) -> str:
    combined = " ".join(_unique_text(summaries))
    if len(combined) <= 2000:
        return combined
    return f"{combined[:1997].rstrip()}..."


def _ordered_unique_observations(
    evidence: tuple[ModelEventEvidence, ...],
) -> tuple[Observation, ...]:
    by_id: dict[UUID, Observation] = {}
    for item in evidence:
        for observation in item.observations:
            by_id.setdefault(observation.id, observation)
    return tuple(sorted(by_id.values(), key=_observation_order))


def _unique_text(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.casefold()
        if normalized not in seen:
            seen.add(normalized)
            result.append(value)
    return tuple(result)


def _sensitivity_rank(value: Sensitivity) -> int:
    return {
        Sensitivity.PUBLIC: 0,
        Sensitivity.PERSONAL: 1,
        Sensitivity.SENSITIVE: 2,
        Sensitivity.FORBIDDEN: 3,
    }[value]


def _evidence_order(item: ModelEventEvidence) -> tuple[datetime, datetime, str]:
    return item.started_at, item.ended_at, str(item.transformation.id)


def _observation_order(item: Observation) -> tuple[datetime, datetime, str]:
    return (
        item.started_at or item.captured_at,
        item.ended_at or item.captured_at,
        str(item.id),
    )
