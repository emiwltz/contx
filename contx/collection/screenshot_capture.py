"""Synthetic-ready selective screenshot capture behind a strict source contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from contx.collection.continuous import ActivitySample
from contx.collection.screenshots import (
    ScreenshotDecision,
    SelectiveScreenshotPlanner,
)
from contx.errors import CollectorUnavailableError, ScreenshotCaptureSkipped
from contx.models import (
    CollectionControl,
    ExclusionRule,
    Observation,
    SourceType,
)
from contx.models.common import build_idempotency_key
from contx.raw_store import RawStore

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ScreenshotSource(Protocol):
    """Read pixels only after a planner has authorized one capture."""

    def capture_png(self, sample: ActivitySample) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ScreenshotCaptureResult:
    decision: ScreenshotDecision
    observation: Observation | None = None
    discard_reason: str | None = None


class SelectiveScreenshotService:
    """Evaluate metadata first, then persist one bounded PNG artifact if allowed."""

    def __init__(
        self,
        *,
        planner: SelectiveScreenshotPlanner,
        source: ScreenshotSource,
        raw_store: RawStore,
        retention: timedelta,
    ) -> None:
        if not timedelta(0) < retention <= timedelta(hours=48):
            raise ValueError("raw retention must be between zero and 48 hours")
        self._planner = planner
        self._source = source
        self._raw_store = raw_store
        self._retention = retention
        self._previous_sample: ActivitySample | None = None
        self._last_capture_at: datetime | None = None
        self._last_content_hash: str | None = None
        self._last_observation: Observation | None = None

    def consider(
        self,
        sample: ActivitySample,
        *,
        control: CollectionControl,
        rules: tuple[ExclusionRule, ...],
        manual_requested: bool = False,
    ) -> ScreenshotCaptureResult:
        decision = self._planner.evaluate(
            sample,
            previous_sample=self._previous_sample,
            last_capture_at=self._last_capture_at,
            control=control,
            rules=rules,
            manual_requested=manual_requested,
        )
        if not decision.capture:
            self._remember_safe_sample(sample, decision)
            return ScreenshotCaptureResult(decision=decision)

        try:
            payload = self._source.capture_png(sample)
        except ScreenshotCaptureSkipped as skipped:
            self._previous_sample = sample
            self._last_capture_at = sample.observed_at
            return ScreenshotCaptureResult(
                decision=decision,
                discard_reason=skipped.reason_code,
            )
        except CollectorUnavailableError:
            raise
        except Exception as error:
            raise CollectorUnavailableError(
                "Cannot capture the macOS screen"
            ) from error
        if not payload.startswith(PNG_SIGNATURE):
            raise CollectorUnavailableError(
                "The screenshot source did not return a PNG artifact"
            )
        content_hash = hashlib.sha256(payload).hexdigest()
        idempotency_key = build_idempotency_key(
            "selective-screenshot-v1",
            sample.observed_at,
            sample.activity_state,
            sample.app_name,
            sample.app_bundle_id,
            sample.window_title,
            decision.trigger,
            content_hash,
        )
        if (
            self._last_observation is not None
            and self._last_observation.idempotency_key == idempotency_key
        ):
            self._previous_sample = sample
            self._last_capture_at = sample.observed_at
            return ScreenshotCaptureResult(
                decision=decision,
                observation=self._last_observation,
            )
        if self._last_content_hash == content_hash:
            self._previous_sample = sample
            self._last_capture_at = sample.observed_at
            return ScreenshotCaptureResult(
                decision=decision,
                discard_reason="duplicate_content",
            )
        observation_id = uuid5(
            NAMESPACE_URL,
            f"contx:screenshot:{idempotency_key}",
        )
        artifact = self._raw_store.write(
            payload,
            artifact_id=observation_id,
            suffix=".png",
            captured_at=sample.observed_at,
            retention=self._retention,
        )
        observation = Observation(
            id=observation_id,
            idempotency_key=idempotency_key,
            source_type=SourceType.SCREENSHOT,
            activity_state=sample.activity_state,
            captured_at=sample.observed_at,
            started_at=sample.observed_at,
            ended_at=sample.observed_at,
            app_name=sample.app_name,
            app_bundle_id=sample.app_bundle_id,
            window_title=sample.window_title,
            artifact_path=str(artifact.path),
            content_hash=artifact.content_hash,
            expires_at=artifact.expires_at,
            created_at=sample.observed_at,
        )
        self._previous_sample = sample
        self._last_capture_at = sample.observed_at
        self._last_content_hash = content_hash
        self._last_observation = observation
        return ScreenshotCaptureResult(
            decision=decision,
            observation=observation,
        )

    def _remember_safe_sample(
        self,
        sample: ActivitySample,
        decision: ScreenshotDecision,
    ) -> None:
        if decision.reason_code in {
            "collection_paused",
            "excluded_by_policy",
            "excluded_by_rule",
        }:
            self._previous_sample = None
            return
        self._previous_sample = sample
