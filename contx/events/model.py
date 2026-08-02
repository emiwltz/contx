"""Deterministic event mapping from validated local-model interpretations."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from contx.model_provider import ModelTransformation, ModelTransformationStatus
from contx.models import EpistemicStatus, Event, Observation, ObservationStatus
from contx.models.common import build_idempotency_key
from contx.models.sources import Clock

MODEL_EVENT_PROCESSING_VERSION = "model-events-v1"


class ModelTransformationEventBuilder:
    """Map model semantics into one provenance-backed event without reinterpreting."""

    def __init__(self, *, clock: Clock) -> None:
        self._clock = clock

    def build(
        self,
        transformation: ModelTransformation,
        observations: tuple[Observation, ...],
    ) -> Event:
        if (
            transformation.status is not ModelTransformationStatus.SUCCEEDED
            or transformation.interpretation is None
            or transformation.model_digest is None
        ):
            raise ValueError(
                "only a successful model transformation can build an event"
            )
        by_id = {observation.id: observation for observation in observations}
        if set(by_id) != set(transformation.source_observation_ids):
            raise ValueError("event observations do not match model provenance")
        ordered = tuple(by_id[value] for value in transformation.source_observation_ids)
        if any(
            observation.excluded
            or observation.processing_status is ObservationStatus.REJECTED
            for observation in ordered
        ):
            raise ValueError("an unavailable observation cannot build an event")

        interpretation = transformation.interpretation
        started_at = min(
            observation.started_at or observation.captured_at for observation in ordered
        )
        ended_at = max(
            observation.ended_at or observation.captured_at for observation in ordered
        )
        key = build_idempotency_key(
            "model-event-v1",
            MODEL_EVENT_PROCESSING_VERSION,
            transformation.idempotency_key,
        )
        timestamp = self._clock.now()
        return Event(
            id=uuid5(NAMESPACE_URL, f"contx:model-event:{key}"),
            idempotency_key=key,
            type=interpretation.activity_type,
            summary=interpretation.summary,
            facts={
                "observed_facts": list(interpretation.observed_facts),
                "inferred_context": list(interpretation.inferred_context),
                "sensitive_categories": [
                    category.value for category in interpretation.sensitive_categories
                ],
                "memory_relevance": interpretation.memory_relevance,
                "model_transformation": {
                    "id": str(transformation.id),
                    "provider": transformation.provider,
                    "endpoint": transformation.endpoint,
                    "runtime_version": transformation.runtime_version,
                    "model": transformation.resolved_model,
                    "model_digest": transformation.model_digest,
                    "prompt_version": transformation.prompt_version,
                    "output_schema_version": transformation.output_schema_version,
                    "image_sha256": transformation.image_sha256,
                },
            },
            started_at=started_at,
            ended_at=ended_at,
            epistemic_status=(
                EpistemicStatus.INFERRED
                if interpretation.inferred_context
                else EpistemicStatus.OBSERVED
            ),
            confidence=interpretation.confidence,
            sensitivity=interpretation.sensitivity,
            projects=interpretation.projects,
            entities=interpretation.entities,
            source_observation_ids=transformation.source_observation_ids,
            processing_version=MODEL_EVENT_PROCESSING_VERSION,
            created_at=timestamp,
            updated_at=timestamp,
        )
