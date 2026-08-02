"""Apply durable pause and exclusion policy to metadata collection."""

from __future__ import annotations

from datetime import datetime, timedelta

from contx.collection.policy import CollectionContext, CollectionPolicy
from contx.collection.service import CollectionControlService
from contx.collectors import Collector
from contx.models import (
    ActivityState,
    Clock,
    IdentifierSource,
    Observation,
    SourceType,
)
from contx.models.common import build_idempotency_key


class ControlledMetadataCollector:
    """Prevent excluded metadata from entering the pipeline.

    This decorator only wraps metadata collectors. A future screenshot
    collector must call the same policy before reading screen pixels, not after
    constructing an artifact.
    """

    def __init__(
        self,
        collector: Collector,
        *,
        controls: CollectionControlService,
        policy: CollectionPolicy,
        clock: Clock,
        identifiers: IdentifierSource,
        retention: timedelta,
        retain_excluded_activity: bool,
    ) -> None:
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise ValueError("raw retention must be between zero and 48 hours")
        self._collector = collector
        self._controls = controls
        self._policy = policy
        self._clock = clock
        self._identifiers = identifiers
        self._retention = retention
        self._retain_excluded_activity = retain_excluded_activity

    def collect(self) -> tuple[Observation, ...]:
        now = self._clock.now()
        control = self._controls.control()
        rules = self._controls.rules(enabled_only=True)
        paused = self._policy.evaluate(
            CollectionContext(activity_state=ActivityState.ACTIVE),
            control=control,
            rules=rules,
            at=now,
        )
        if paused.excluded:
            return self._excluded_without_source(
                at=now,
                reason=paused.reason_code or "collection_paused",
            )

        accepted: list[Observation] = []
        for observation in self._collector.collect():
            decision = self._policy.evaluate(
                CollectionContext(
                    activity_state=observation.activity_state,
                    app_name=observation.app_name,
                    app_bundle_id=observation.app_bundle_id,
                    window_title=observation.window_title,
                ),
                control=control,
                rules=rules,
                at=observation.captured_at,
            )
            if decision.excluded:
                if self._retain_excluded_activity:
                    accepted.append(
                        _minimal_excluded_observation(
                            observation,
                            reason=_decision_reason(
                                decision.reason_code, decision.rule_id
                            ),
                        )
                    )
            else:
                accepted.append(observation)
        return tuple(accepted)

    def _excluded_without_source(
        self, *, at: datetime, reason: str
    ) -> tuple[Observation, ...]:
        if not self._retain_excluded_activity:
            return ()
        return (
            Observation(
                id=self._identifiers.new(),
                idempotency_key=build_idempotency_key(
                    "excluded-observation-v1", at, reason
                ),
                source_type=SourceType.EXCLUDED_ACTIVITY,
                captured_at=at,
                started_at=at,
                ended_at=at,
                excluded=True,
                exclusion_reason=reason,
                expires_at=at + self._retention,
                created_at=at,
            ),
        )


def _minimal_excluded_observation(source: Observation, *, reason: str) -> Observation:
    return Observation(
        id=source.id,
        idempotency_key=build_idempotency_key(
            "excluded-observation-v1", source.idempotency_key, reason
        ),
        source_type=SourceType.EXCLUDED_ACTIVITY,
        activity_state=source.activity_state,
        captured_at=source.captured_at,
        started_at=source.started_at,
        ended_at=source.ended_at,
        excluded=True,
        exclusion_reason=reason,
        expires_at=source.expires_at,
        created_at=source.created_at,
    )


def _decision_reason(reason_code: str | None, rule_id: str | None) -> str:
    if reason_code == "excluded_by_rule" and rule_id is not None:
        return f"excluded_by_rule:{rule_id}"
    return reason_code or "excluded_by_policy"
