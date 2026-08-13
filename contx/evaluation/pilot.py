"""Load, validate, and score a content-minimized CONTX pilot workspace."""

from __future__ import annotations

import fcntl
import math
import os
import stat
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from contx.errors import ContxError
from contx.evaluation.models import (
    GroundTruthEntry,
    GroundTruthImportance,
    GroundTruthKind,
    PilotCondition,
    PilotDataset,
    PilotManifest,
    PilotScenario,
    PrivacyIncident,
    PrivacyIncidentCategory,
    ResourcePhase,
    ResourceSample,
    Severity,
    TechnicalSnapshot,
    TrialEvaluation,
)
from contx.models.common import format_utc, require_aware_utc

MAX_INPUT_FILE_BYTES = 5 * 1024 * 1024
MAX_JSONL_RECORDS = 10_000
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class PilotEvaluationError(ContxError):
    """Raised when pilot evidence is missing, unsafe, or internally inconsistent."""


class GateStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCOMPLETE = "incomplete"
    REVIEW = "review"


class ReportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GateResult(ReportRecord):
    key: str
    status: GateStatus
    detail: str


class ConditionMetrics(ReportRecord):
    trial_count: int
    coverage: float
    factual_precision: float
    false_claim_rate: float
    irrelevant_claim_rate: float
    mean_relevance: float
    mean_synthesis: float
    mean_clarifications: float
    mean_response_latency_ms: float
    mean_context_bytes: float
    mean_wake_latency_ms: float | None
    quality_score: float


class BehaviorComparison(ReportRecord):
    scenario: PilotScenario
    without_contx: ConditionMetrics
    with_contx: ConditionMetrics
    quality_delta: float
    coverage_delta: float
    false_claim_rate_delta: float
    demonstrated_benefit: bool


class ResourceMetrics(ReportRecord):
    sample_count: int
    p95_total_cpu_percent: float
    p95_total_rss_bytes: int
    p95_detection_latency_ms: float | None
    maximum_raw_disk_bytes: int
    maximum_durable_disk_bytes: int


class TechnicalMetrics(ReportRecord):
    captured_at: datetime
    accepted_memories: int
    provenance_coverage: float
    materially_false_memory_rate: float
    irrelevant_memory_rate: float
    duplicate_memory_rate: float
    manual_correction_rate: float
    sensitive_promotions: int
    synthetic_secret_promotions: int
    invalid_model_output_rate: float
    unknown_model_attempts: int
    raw_records_past_retention: int
    excluded_context_captures: int
    remote_user_content_transports: int
    active_context_bytes: int
    wake_latency_ms: float


class PilotReport(ReportRecord):
    schema_version: int = 1
    pilot_id: UUID
    evaluated_at: datetime
    completed_duration_days: float
    ground_truth_count: int
    important_ground_truth_count: int
    important_event_recall: float
    behavior_comparisons: tuple[BehaviorComparison, ...]
    technical: TechnicalMetrics | None
    resources: ResourceMetrics | None
    incidents_total: int
    unresolved_critical_incidents: int
    gates: tuple[GateResult, ...]
    ready_for_v0_decision: bool


class PilotWorkspace:
    """Private file boundary for intentional pilot evidence, never raw captures."""

    MANIFEST = "manifest.json"
    GROUND_TRUTH = "ground-truth.jsonl"
    TRIALS = "trials.jsonl"
    TECHNICAL = "technical-snapshots.jsonl"
    RESOURCES = "resource-samples.jsonl"
    INCIDENTS = "privacy-incidents.jsonl"
    README = "README.md"
    EXAMPLES = "EXAMPLES.md"
    REPORT = "report.md"
    LOCK = ".workspace.lock"

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise PilotEvaluationError("Pilot workspace path must be absolute")
        self.root = root

    def prepare(self, manifest: PilotManifest) -> None:
        """Create an empty private workspace; this does not activate collection."""
        if self.root.is_symlink():
            raise PilotEvaluationError("Pilot workspace must not be a symlink")
        if self.root.exists():
            if not self.root.is_dir():
                raise PilotEvaluationError("Pilot workspace is not a directory")
            try:
                if any(self.root.iterdir()):
                    raise PilotEvaluationError("Pilot workspace must be empty")
            except OSError as error:
                raise PilotEvaluationError("Cannot inspect pilot workspace") from error
        else:
            try:
                self.root.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True)
            except OSError as error:
                raise PilotEvaluationError("Cannot create pilot workspace") from error
        self._protect_root()
        _write_private_file(self.root / self.LOCK, b"")
        _write_private_file(
            self.root / self.MANIFEST,
            manifest.model_dump_json(indent=2).encode("utf-8") + b"\n",
        )
        for name in (
            self.GROUND_TRUTH,
            self.TRIALS,
            self.TECHNICAL,
            self.RESOURCES,
            self.INCIDENTS,
        ):
            _write_private_file(self.root / name, b"")
        _write_private_file(self.root / self.README, _workspace_readme().encode())
        _write_private_file(
            self.root / self.EXAMPLES,
            _workspace_examples(manifest).encode("utf-8"),
        )

    def load(self) -> PilotDataset:
        self._protect_root()
        with self._lock(exclusive=False):
            return self._load_unlocked()

    def append_resource_sample(self, sample: ResourceSample) -> None:
        """Append one measured aggregate without exposing arbitrary file writes."""
        self._protect_root()
        with self._lock(exclusive=True):
            existing = _load_jsonl(self.root / self.RESOURCES, ResourceSample)
            _replace_private_file(
                self.root / self.RESOURCES,
                _jsonl_bytes((*existing, sample)),
            )

    def append_technical_snapshot(self, snapshot: TechnicalSnapshot) -> None:
        """Atomically append one content-free technical and reviewed snapshot."""
        self._protect_root()
        with self._lock(exclusive=True):
            existing = _load_jsonl(self.root / self.TECHNICAL, TechnicalSnapshot)
            _replace_private_file(
                self.root / self.TECHNICAL,
                _jsonl_bytes((*existing, snapshot)),
            )

    def write_report(self, report: PilotReport) -> Path:
        self._protect_root()
        path = self.root / self.REPORT
        with self._lock(exclusive=True):
            _replace_private_file(path, render_pilot_report(report).encode("utf-8"))
        return path

    def _load_unlocked(self) -> PilotDataset:
        manifest = _load_json_record(self.root / self.MANIFEST, PilotManifest)
        return PilotDataset(
            manifest=manifest,
            ground_truth=_load_jsonl(self.root / self.GROUND_TRUTH, GroundTruthEntry),
            trials=_load_jsonl(self.root / self.TRIALS, TrialEvaluation),
            technical_snapshots=_load_jsonl(
                self.root / self.TECHNICAL, TechnicalSnapshot
            ),
            resource_samples=_load_jsonl(self.root / self.RESOURCES, ResourceSample),
            privacy_incidents=_load_jsonl(self.root / self.INCIDENTS, PrivacyIncident),
        )

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        path = self.root / self.LOCK
        if path.is_symlink():
            raise PilotEvaluationError("Pilot workspace lock must not be a symlink")
        descriptor: int | None = None
        try:
            flags = os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            file_status = os.fstat(descriptor)
            if not stat.S_ISREG(file_status.st_mode):
                raise PilotEvaluationError("Pilot workspace lock is not a regular file")
            if stat.S_IMODE(file_status.st_mode) != PRIVATE_FILE_MODE:
                raise PilotEvaluationError(
                    "Pilot workspace lock permissions are too broad"
                )
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(descriptor, operation)
            yield
        except PilotEvaluationError:
            raise
        except OSError as error:
            raise PilotEvaluationError("Cannot lock pilot workspace") from error
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _protect_root(self) -> None:
        if self.root.is_symlink() or not self.root.is_dir():
            raise PilotEvaluationError("Pilot workspace is not a safe directory")
        try:
            if self.root.resolve(strict=True) != self.root:
                raise PilotEvaluationError("Pilot workspace must not use symlinks")
            self.root.chmod(PRIVATE_DIRECTORY_MODE)
        except OSError as error:
            raise PilotEvaluationError("Cannot protect pilot workspace") from error


def evaluate_pilot(dataset: PilotDataset, *, evaluated_at: datetime) -> PilotReport:
    """Validate evidence and compute deterministic paired and technical metrics."""
    at = require_aware_utc(evaluated_at)
    if at < dataset.manifest.started_at:
        raise PilotEvaluationError("Pilot evaluation cutoff precedes its start")
    dataset = _dataset_at(dataset, at=at)
    _validate_dataset(dataset)
    manifest = dataset.manifest
    complete_trials = _complete_trials(dataset.trials)
    comparisons = tuple(
        _compare_scenario(
            scenario,
            complete_trials,
            minimum_delta=manifest.thresholds.minimum_behavior_quality_delta,
            maximum_false_regression=(
                manifest.thresholds.max_false_claim_rate_regression
            ),
        )
        for scenario in PilotScenario
        if any(trial.scenario is scenario for trial in complete_trials)
    )
    technical = _technical_metrics(dataset.technical_snapshots)
    resources = _resource_metrics(dataset.resource_samples)
    important_recall = _important_event_recall(dataset)
    duration_days = max(
        0.0,
        (min(at, manifest.planned_end_at) - manifest.started_at).total_seconds()
        / 86_400,
    )
    unresolved_critical = sum(
        incident.severity is Severity.CRITICAL and incident.resolved_at is None
        for incident in dataset.privacy_incidents
    )
    gates = _build_gates(
        dataset=dataset,
        evaluated_at=at,
        comparisons=comparisons,
        technical=technical,
        resources=resources,
        important_recall=important_recall,
        unresolved_critical=unresolved_critical,
    )
    return PilotReport(
        pilot_id=manifest.pilot_id,
        evaluated_at=at,
        completed_duration_days=duration_days,
        ground_truth_count=len(dataset.ground_truth),
        important_ground_truth_count=sum(
            item.importance.counts_as_important and item.kind.value != "expected_ignore"
            for item in dataset.ground_truth
        ),
        important_event_recall=important_recall,
        behavior_comparisons=comparisons,
        technical=technical,
        resources=resources,
        incidents_total=len(dataset.privacy_incidents),
        unresolved_critical_incidents=unresolved_critical,
        gates=gates,
        ready_for_v0_decision=all(gate.status is GateStatus.PASS for gate in gates),
    )


def render_pilot_report(report: PilotReport) -> str:
    """Render aggregate evidence without copying ground-truth or answer content."""
    lines = [
        "# CONTX v0 real-pilot report",
        "",
        f"- Pilot: `{report.pilot_id}`",
        f"- Evaluated at: `{format_utc(report.evaluated_at)}`",
        f"- Completed duration: {report.completed_duration_days:.2f} days",
        f"- Ground-truth entries: {report.ground_truth_count}",
        f"- Important-event recall: {_percent(report.important_event_recall)}",
        "- v0 decision readiness: "
        + ("**ready**" if report.ready_for_v0_decision else "**not ready**"),
        "",
        "## Behavior comparison",
        "",
        "| Scenario | Quality without | Quality with | Delta | Coverage delta | "
        "False-claim delta | Benefit |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for comparison in report.behavior_comparisons:
        lines.append(
            "| "
            + " | ".join(
                (
                    comparison.scenario.value,
                    f"{comparison.without_contx.quality_score:.3f}",
                    f"{comparison.with_contx.quality_score:.3f}",
                    _signed(comparison.quality_delta),
                    _signed(comparison.coverage_delta),
                    _signed(comparison.false_claim_rate_delta),
                    "yes" if comparison.demonstrated_benefit else "no",
                )
            )
            + " |"
        )
    if not report.behavior_comparisons:
        lines.append("| No complete trial data | - | - | - | - | - | no |")

    lines.extend(["", "## Technical metrics", ""])
    if report.technical is None:
        lines.append("No technical snapshot is available.")
    else:
        technical = report.technical
        lines.extend(
            [
                f"- Accepted memories: {technical.accepted_memories}",
                f"- Provenance coverage: {_percent(technical.provenance_coverage)}",
                "- Materially false memory rate: "
                f"{_percent(technical.materially_false_memory_rate)}",
                "- Irrelevant memory rate: "
                f"{_percent(technical.irrelevant_memory_rate)}",
                f"- Duplicate memory rate: {_percent(technical.duplicate_memory_rate)}",
                "- Manual correction rate: "
                f"{_percent(technical.manual_correction_rate)}",
                "- Invalid local-model output rate: "
                f"{_percent(technical.invalid_model_output_rate)}",
                "- Unknown interrupted model attempts: "
                f"{technical.unknown_model_attempts}",
                f"- Active context: {technical.active_context_bytes} bytes",
                f"- Wake latency: {technical.wake_latency_ms:.1f} ms",
            ]
        )

    lines.extend(["", "## Resource baselines", ""])
    if report.resources is None:
        lines.append("No resource sample is available.")
    else:
        resources = report.resources
        lines.extend(
            [
                f"- Samples: {resources.sample_count}",
                f"- Total measured CPU p95: {resources.p95_total_cpu_percent:.2f}%",
                f"- Total measured RSS p95: {resources.p95_total_rss_bytes} bytes",
                "- Detection latency p95: "
                + (
                    "unavailable"
                    if resources.p95_detection_latency_ms is None
                    else f"{resources.p95_detection_latency_ms:.1f} ms"
                ),
                f"- Maximum raw disk: {resources.maximum_raw_disk_bytes} bytes",
                f"- Maximum durable disk: {resources.maximum_durable_disk_bytes} bytes",
            ]
        )

    lines.extend(
        [
            "",
            "## Acceptance gates",
            "",
            "| Gate | Status | Evidence |",
            "| --- | --- | --- |",
        ]
    )
    for gate in report.gates:
        lines.append(f"| {gate.key} | {gate.status.value} | {gate.detail} |")
    lines.extend(
        [
            "",
            "This report contains aggregate scores and identifiers only. Ground-truth "
            "summaries, agent answers, raw captures, window titles, and secrets are "
            "not "
            "copied into it.",
            "",
        ]
    )
    return "\n".join(lines)


def _validate_dataset(dataset: PilotDataset) -> None:
    _require_unique_ids("ground-truth", (item.id for item in dataset.ground_truth))
    _require_unique_ids("trial", (trial.id for trial in dataset.trials))
    _require_unique_ids(
        "privacy incident", (incident.id for incident in dataset.privacy_incidents)
    )
    truths = {item.id: item for item in dataset.ground_truth}
    for trial in dataset.trials:
        missing = set(trial.relevant_truth_ids) - truths.keys()
        if missing:
            raise PilotEvaluationError(
                f"Trial {trial.id} references missing ground-truth identifiers"
            )
        wrong_scenario = [
            identifier
            for identifier in trial.relevant_truth_ids
            if trial.scenario not in truths[identifier].scenarios
        ]
        if wrong_scenario:
            raise PilotEvaluationError(
                f"Trial {trial.id} uses ground truth outside its scenario"
            )
    pairs: dict[str, list[TrialEvaluation]] = defaultdict(list)
    for trial in dataset.trials:
        pairs[trial.pair_key].append(trial)
    for pair_key, pair in pairs.items():
        conditions = {trial.condition for trial in pair}
        if len(pair) > 2 or len(conditions) != len(pair):
            raise PilotEvaluationError(
                f"Trial pair {pair_key!r} contains duplicate condition evidence"
            )
        if len(pair) == 1:
            continue
        if conditions != set(PilotCondition):
            raise PilotEvaluationError(
                f"Trial pair {pair_key!r} must contain one result per condition"
            )
        first, second = pair
        if (
            first.scenario is not second.scenario
            or first.prompt_key != second.prompt_key
            or set(first.relevant_truth_ids) != set(second.relevant_truth_ids)
        ):
            raise PilotEvaluationError(
                f"Trial pair {pair_key!r} does not compare the same task"
            )
    ordered_snapshots = tuple(
        sorted(dataset.technical_snapshots, key=lambda item: item.captured_at)
    )
    for previous, current in zip(
        ordered_snapshots, ordered_snapshots[1:], strict=False
    ):
        for field in (
            "accepted_memories",
            "accepted_memories_with_provenance",
            "manual_corrections",
            "model_outputs",
            "invalid_model_outputs",
            "unknown_model_attempts",
        ):
            if getattr(current, field) < getattr(previous, field):
                raise PilotEvaluationError(
                    f"Technical counter {field} must be cumulative"
                )


def _dataset_at(dataset: PilotDataset, *, at: datetime) -> PilotDataset:
    return dataset.model_copy(
        update={
            "ground_truth": tuple(
                item for item in dataset.ground_truth if item.occurred_at <= at
            ),
            "trials": tuple(item for item in dataset.trials if item.evaluated_at <= at),
            "technical_snapshots": tuple(
                item for item in dataset.technical_snapshots if item.captured_at <= at
            ),
            "resource_samples": tuple(
                item for item in dataset.resource_samples if item.captured_at <= at
            ),
            "privacy_incidents": tuple(
                item for item in dataset.privacy_incidents if item.occurred_at <= at
            ),
        }
    )


def _complete_trials(
    trials: Sequence[TrialEvaluation],
) -> tuple[TrialEvaluation, ...]:
    pairs: dict[str, list[TrialEvaluation]] = defaultdict(list)
    for trial in trials:
        pairs[trial.pair_key].append(trial)
    return tuple(
        trial
        for pair in pairs.values()
        if len(pair) == 2 and {item.condition for item in pair} == set(PilotCondition)
        for trial in pair
    )


def _compare_scenario(
    scenario: PilotScenario,
    trials: Sequence[TrialEvaluation],
    *,
    minimum_delta: float,
    maximum_false_regression: float,
) -> BehaviorComparison:
    selected = [trial for trial in trials if trial.scenario is scenario]
    without = _condition_metrics(
        [trial for trial in selected if trial.condition is PilotCondition.WITHOUT_CONTX]
    )
    with_contx = _condition_metrics(
        [trial for trial in selected if trial.condition is PilotCondition.WITH_CONTX]
    )
    quality_delta = with_contx.quality_score - without.quality_score
    coverage_delta = with_contx.coverage - without.coverage
    false_delta = with_contx.false_claim_rate - without.false_claim_rate
    return BehaviorComparison(
        scenario=scenario,
        without_contx=without,
        with_contx=with_contx,
        quality_delta=quality_delta,
        coverage_delta=coverage_delta,
        false_claim_rate_delta=false_delta,
        demonstrated_benefit=(
            quality_delta >= minimum_delta and false_delta <= maximum_false_regression
        ),
    )


def _condition_metrics(trials: Sequence[TrialEvaluation]) -> ConditionMetrics:
    if not trials:
        raise PilotEvaluationError("Every compared condition needs a scored trial")
    relevant = sum(len(trial.relevant_truth_ids) for trial in trials)
    retrieved = sum(len(trial.retrieved_truth_ids) for trial in trials)
    total_claims = sum(trial.total_factual_claims for trial in trials)
    supported = sum(trial.supported_factual_claims for trial in trials)
    false = sum(trial.materially_false_claims for trial in trials)
    irrelevant = sum(trial.irrelevant_claims for trial in trials)
    coverage = _ratio(retrieved, relevant)
    factual_precision = _ratio(supported, supported + false, empty=1.0)
    false_rate = _ratio(false, total_claims, empty=0.0)
    irrelevant_rate = _ratio(irrelevant, total_claims, empty=0.0)
    relevance = _mean(trial.relevance_score for trial in trials)
    synthesis = _mean(trial.synthesis_score for trial in trials)
    quality = (
        0.40 * coverage
        + 0.30 * factual_precision
        + 0.15 * (relevance / 5)
        + 0.15 * (synthesis / 5)
    )
    wake_values = [
        trial.wake_latency_ms for trial in trials if trial.wake_latency_ms is not None
    ]
    return ConditionMetrics(
        trial_count=len(trials),
        coverage=coverage,
        factual_precision=factual_precision,
        false_claim_rate=false_rate,
        irrelevant_claim_rate=irrelevant_rate,
        mean_relevance=relevance,
        mean_synthesis=synthesis,
        mean_clarifications=_mean(trial.clarification_questions for trial in trials),
        mean_response_latency_ms=_mean(trial.response_latency_ms for trial in trials),
        mean_context_bytes=_mean(trial.context_bytes for trial in trials),
        mean_wake_latency_ms=None if not wake_values else _mean(wake_values),
        quality_score=quality,
    )


def _technical_metrics(
    snapshots: Sequence[TechnicalSnapshot],
) -> TechnicalMetrics | None:
    if not snapshots:
        return None
    snapshot = max(snapshots, key=lambda item: item.captured_at)
    accepted = snapshot.accepted_memories
    return TechnicalMetrics(
        captured_at=snapshot.captured_at,
        accepted_memories=accepted,
        provenance_coverage=_ratio(
            snapshot.accepted_memories_with_provenance, accepted, empty=0.0
        ),
        materially_false_memory_rate=_ratio(
            snapshot.materially_false_memories, accepted, empty=0.0
        ),
        irrelevant_memory_rate=_ratio(
            snapshot.irrelevant_memories, accepted, empty=0.0
        ),
        duplicate_memory_rate=_ratio(snapshot.duplicate_memories, accepted, empty=0.0),
        manual_correction_rate=_ratio(snapshot.manual_corrections, accepted, empty=0.0),
        sensitive_promotions=snapshot.sensitive_promotions,
        synthetic_secret_promotions=snapshot.synthetic_secret_promotions,
        invalid_model_output_rate=_ratio(
            snapshot.invalid_model_outputs, snapshot.model_outputs, empty=0.0
        ),
        unknown_model_attempts=snapshot.unknown_model_attempts,
        raw_records_past_retention=snapshot.raw_records_past_retention,
        excluded_context_captures=snapshot.excluded_context_captures,
        remote_user_content_transports=snapshot.remote_user_content_transports,
        active_context_bytes=snapshot.active_context_bytes,
        wake_latency_ms=snapshot.wake_latency_ms,
    )


def _resource_metrics(samples: Sequence[ResourceSample]) -> ResourceMetrics | None:
    if not samples:
        return None
    return ResourceMetrics(
        sample_count=len(samples),
        p95_total_cpu_percent=_percentile(
            [sample.total_cpu_percent for sample in samples], 0.95
        ),
        p95_total_rss_bytes=math.ceil(
            _percentile([sample.total_rss_bytes for sample in samples], 0.95)
        ),
        p95_detection_latency_ms=(
            None
            if not any(sample.detection_latency_ms is not None for sample in samples)
            else _percentile(
                [
                    sample.detection_latency_ms
                    for sample in samples
                    if sample.detection_latency_ms is not None
                ],
                0.95,
            )
        ),
        maximum_raw_disk_bytes=max(sample.raw_disk_bytes for sample in samples),
        maximum_durable_disk_bytes=max(sample.durable_disk_bytes for sample in samples),
    )


def _important_event_recall(dataset: PilotDataset) -> float:
    important_ids = {
        item.id
        for item in dataset.ground_truth
        if item.importance.counts_as_important
        and item.kind.value != "expected_ignore"
        and any(
            trial.condition is PilotCondition.WITH_CONTX
            and item.id in trial.relevant_truth_ids
            for trial in dataset.trials
        )
    }
    retrieved = {
        identifier
        for trial in dataset.trials
        if trial.condition is PilotCondition.WITH_CONTX
        for identifier in trial.retrieved_truth_ids
        if identifier in important_ids
    }
    return _ratio(len(retrieved), len(important_ids), empty=0.0)


def _build_gates(
    *,
    dataset: PilotDataset,
    evaluated_at: datetime,
    comparisons: Sequence[BehaviorComparison],
    technical: TechnicalMetrics | None,
    resources: ResourceMetrics | None,
    important_recall: float,
    unresolved_critical: int,
) -> tuple[GateResult, ...]:
    manifest = dataset.manifest
    thresholds = manifest.thresholds
    scenarios = {comparison.scenario for comparison in comparisons}
    gates = [
        _boolean_gate(
            "pilot_duration",
            evaluated_at >= manifest.planned_end_at,
            "planned 7-14 day interval completed"
            if evaluated_at >= manifest.planned_end_at
            else "planned interval has not completed",
            incomplete_when_false=True,
        ),
        _boolean_gate(
            "three_behavior_pairs",
            scenarios == set(PilotScenario),
            f"{len(scenarios)}/3 target behaviors have paired evidence",
            incomplete_when_false=True,
        ),
        _boolean_gate(
            "three_behavior_benefit",
            len(comparisons) == 3
            and all(comparison.demonstrated_benefit for comparison in comparisons),
            f"{sum(item.demonstrated_benefit for item in comparisons)}/3 "
            "behaviors improve",
        ),
        _boolean_gate(
            "important_event_recall",
            important_recall > thresholds.important_event_recall_min_exclusive,
            f"{_percent(important_recall)}; must exceed "
            f"{_percent(thresholds.important_event_recall_min_exclusive)}",
        ),
    ]
    if technical is None:
        gates.extend(
            GateResult(key=key, status=GateStatus.INCOMPLETE, detail="missing snapshot")
            for key in (
                "memory_provenance",
                "false_memory_rate",
                "raw_retention",
                "excluded_capture",
                "local_only_transport",
                "synthetic_secret",
                "context_budget",
                "model_attempt_audit",
            )
        )
    else:
        gates.extend(
            [
                _boolean_gate(
                    "memory_provenance",
                    technical.provenance_coverage >= thresholds.provenance_coverage_min,
                    _percent(technical.provenance_coverage),
                ),
                _boolean_gate(
                    "false_memory_rate",
                    technical.materially_false_memory_rate
                    < thresholds.materially_false_memory_rate_max,
                    f"{_percent(technical.materially_false_memory_rate)}; must be "
                    "below "
                    f"{_percent(thresholds.materially_false_memory_rate_max)}",
                ),
                _zero_gate("raw_retention", technical.raw_records_past_retention),
                _zero_gate("excluded_capture", technical.excluded_context_captures),
                _zero_gate(
                    "local_only_transport", technical.remote_user_content_transports
                ),
                _zero_gate("synthetic_secret", technical.synthetic_secret_promotions),
                _boolean_gate(
                    "context_budget",
                    technical.active_context_bytes <= thresholds.wake_budget_bytes,
                    f"{technical.active_context_bytes}/"
                    f"{thresholds.wake_budget_bytes} bytes",
                ),
                _zero_gate("model_attempt_audit", technical.unknown_model_attempts),
            ]
        )
    limits = thresholds.resource_limits
    if resources is None:
        gates.append(
            GateResult(
                key="daily_resource_use",
                status=GateStatus.INCOMPLETE,
                detail="missing resource samples",
            )
        )
    elif {sample.phase for sample in dataset.resource_samples} != set(ResourcePhase):
        missing = ", ".join(
            sorted(
                phase.value
                for phase in set(ResourcePhase)
                - {sample.phase for sample in dataset.resource_samples}
            )
        )
        gates.append(
            GateResult(
                key="daily_resource_use",
                status=GateStatus.INCOMPLETE,
                detail=f"missing representative resource phases: {missing}",
            )
        )
    elif not limits.fully_defined:
        gates.append(
            GateResult(
                key="daily_resource_use",
                status=GateStatus.REVIEW,
                detail="CPU, RAM, and detection limits require operator approval",
            )
        )
    else:
        assert limits.p95_total_cpu_percent_max is not None
        assert limits.p95_total_rss_bytes_max is not None
        assert limits.p95_detection_latency_ms_max is not None
        passed = (
            resources.p95_total_cpu_percent <= limits.p95_total_cpu_percent_max
            and resources.p95_total_rss_bytes <= limits.p95_total_rss_bytes_max
            and resources.p95_detection_latency_ms is not None
            and resources.p95_detection_latency_ms
            <= limits.p95_detection_latency_ms_max
            and resources.maximum_raw_disk_bytes <= limits.raw_disk_bytes_max
        )
        gates.append(
            _boolean_gate(
                "daily_resource_use",
                passed,
                "measured p95 CPU/RAM/detection and raw disk are within "
                "approved limits",
            )
        )
    gates.append(
        _boolean_gate(
            "critical_privacy_incidents",
            unresolved_critical == 0,
            f"{unresolved_critical} unresolved critical incident(s)",
        )
    )
    return tuple(gates)


def _boolean_gate(
    key: str,
    passed: bool,
    detail: str,
    *,
    incomplete_when_false: bool = False,
) -> GateResult:
    return GateResult(
        key=key,
        status=(
            GateStatus.PASS
            if passed
            else GateStatus.INCOMPLETE
            if incomplete_when_false
            else GateStatus.FAIL
        ),
        detail=detail,
    )


def _zero_gate(key: str, count: int) -> GateResult:
    return _boolean_gate(key, count == 0, f"{count} violation(s)")


def _load_json_record[RecordT: BaseModel](
    path: Path, record_type: type[RecordT]
) -> RecordT:
    payload = _read_private_file(path)
    try:
        return record_type.model_validate_json(payload)
    except ValidationError as error:
        raise PilotEvaluationError(
            f"Invalid {path.name}: {_validation_summary(error)}"
        ) from error


def _load_jsonl[RecordT: BaseModel](
    path: Path, record_type: type[RecordT]
) -> tuple[RecordT, ...]:
    payload = _read_private_file(path)
    records: list[RecordT] = []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PilotEvaluationError(f"Invalid UTF-8 in {path.name}") from error
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if len(records) >= MAX_JSONL_RECORDS:
            raise PilotEvaluationError(f"Too many records in {path.name}")
        try:
            records.append(record_type.model_validate_json(line))
        except ValidationError as error:
            detail = _validation_summary(error)
            raise PilotEvaluationError(
                f"Invalid {path.name} line {line_number}: {detail}"
            ) from error
    return tuple(records)


def _read_private_file(path: Path) -> bytes:
    if path.is_symlink():
        raise PilotEvaluationError(f"Pilot file must not be a symlink: {path.name}")
    try:
        file_status = path.stat()
    except OSError as error:
        raise PilotEvaluationError(f"Cannot inspect pilot file: {path.name}") from error
    if not stat.S_ISREG(file_status.st_mode):
        raise PilotEvaluationError(f"Pilot path is not a regular file: {path.name}")
    if file_status.st_size > MAX_INPUT_FILE_BYTES:
        raise PilotEvaluationError(f"Pilot file is too large: {path.name}")
    if stat.S_IMODE(file_status.st_mode) & 0o077:
        raise PilotEvaluationError(f"Pilot file permissions are too broad: {path.name}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise PilotEvaluationError(f"Cannot read pilot file: {path.name}") from error


def _write_private_file(path: Path, content: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            PRIVATE_FILE_MODE,
        )
        _write_all(descriptor, content)
        os.fsync(descriptor)
    except OSError as error:
        raise PilotEvaluationError(f"Cannot create pilot file: {path.name}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _replace_private_file(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise PilotEvaluationError(f"Pilot file must not be a symlink: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_private_file(temporary, content)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as error:
        raise PilotEvaluationError("Cannot publish pilot report") from error
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _write_all(descriptor: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError("zero-byte write")
        remaining = remaining[written:]


def _jsonl_bytes(records: Sequence[BaseModel]) -> bytes:
    return ("".join(record.model_dump_json() + "\n" for record in records)).encode(
        "utf-8"
    )


def _require_unique_ids(label: str, identifiers: Iterable[UUID]) -> None:
    seen: set[UUID] = set()
    for identifier in identifiers:
        if identifier in seen:
            raise PilotEvaluationError(f"Duplicate {label} identifier: {identifier}")
        seen.add(identifier)


def _validation_summary(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return empty if denominator == 0 else numerator / denominator


def _mean(values: Iterable[float | int]) -> float:
    materialized = tuple(values)
    if not materialized:
        raise PilotEvaluationError("Cannot average an empty metric")
    return sum(materialized) / len(materialized)


def _percentile(values: Sequence[float | int], percentile: float) -> float:
    if not values:
        raise PilotEvaluationError("Cannot calculate an empty percentile")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _signed(value: float) -> str:
    return f"{value:+.3f}"


def _workspace_readme() -> str:
    return """# CONTX pilot evidence workspace

This private directory stores only intentional evaluation evidence. It does not
activate collection and must never contain screenshots, window titles, secrets,
or full agent answers.

- `ground-truth.jsonl`: one `GroundTruthEntry` JSON object per line.
- `trials.jsonl`: paired, content-minimized `TrialEvaluation` scores.
- `technical-snapshots.jsonl`: cumulative technical counters.
- `resource-samples.jsonl`: measured collector and disk baselines.
- `privacy-incidents.jsonl`: redacted incident metadata.
- `report.md`: aggregate report generated after validation.

Run the two conditions from the same prompt and cutoff. Score their answers
against the same ground-truth identifiers before interpreting the condition
labels. Never infer missing evidence: leave the gate incomplete instead.
"""


def _workspace_examples(manifest: PilotManifest) -> str:
    truth_id = UUID("00000000-0000-0000-0000-000000000001")
    records: tuple[tuple[str, BaseModel], ...] = (
        (
            PilotWorkspace.GROUND_TRUTH,
            GroundTruthEntry(
                id=truth_id,
                occurred_at=manifest.started_at,
                kind=GroundTruthKind.DECISION,
                importance=GroundTruthImportance.IMPORTANT,
                scenarios=(PilotScenario.PROJECT_RESUMPTION,),
                summary="Synthetic decision used only as a format example.",
                project="Synthetic project",
            ),
        ),
        (
            PilotWorkspace.TRIALS + " (without CONTX)",
            TrialEvaluation(
                id=UUID("00000000-0000-0000-0000-000000000002"),
                condition=PilotCondition.WITHOUT_CONTX,
                retrieved_truth_ids=(),
                context_bytes=0,
                wake_latency_ms=None,
                pair_key="resume-01",
                prompt_key="resume-project-v1",
                scenario=PilotScenario.PROJECT_RESUMPTION,
                evaluated_at=manifest.planned_end_at,
                relevant_truth_ids=(truth_id,),
                supported_factual_claims=1,
                total_factual_claims=1,
                materially_false_claims=0,
                irrelevant_claims=0,
                duplicate_claims=0,
                clarification_questions=0,
                relevance_score=4,
                synthesis_score=4,
                response_latency_ms=1000.0,
            ),
        ),
        (
            PilotWorkspace.TRIALS + " (with CONTX)",
            TrialEvaluation(
                id=UUID("00000000-0000-0000-0000-000000000003"),
                condition=PilotCondition.WITH_CONTX,
                retrieved_truth_ids=(truth_id,),
                context_bytes=1000,
                wake_latency_ms=50.0,
                pair_key="resume-01",
                prompt_key="resume-project-v1",
                scenario=PilotScenario.PROJECT_RESUMPTION,
                evaluated_at=manifest.planned_end_at,
                relevant_truth_ids=(truth_id,),
                supported_factual_claims=1,
                total_factual_claims=1,
                materially_false_claims=0,
                irrelevant_claims=0,
                duplicate_claims=0,
                clarification_questions=0,
                relevance_score=4,
                synthesis_score=4,
                response_latency_ms=1000.0,
            ),
        ),
        (
            PilotWorkspace.TECHNICAL,
            TechnicalSnapshot(
                captured_at=manifest.planned_end_at,
                accepted_memories=1,
                accepted_memories_with_provenance=1,
                materially_false_memories=0,
                irrelevant_memories=0,
                duplicate_memories=0,
                manual_corrections=0,
                sensitive_promotions=0,
                synthetic_secret_promotions=0,
                model_outputs=1,
                invalid_model_outputs=0,
                unknown_model_attempts=0,
                raw_records_past_retention=0,
                excluded_context_captures=0,
                remote_user_content_transports=0,
                active_context_bytes=1000,
                wake_latency_ms=50.0,
            ),
        ),
        (
            PilotWorkspace.RESOURCES,
            ResourceSample(
                captured_at=manifest.started_at,
                phase=ResourcePhase.ACTIVE_COLLECTION,
                collector_cpu_percent=1.0,
                collector_rss_bytes=100_000_000,
                detection_latency_ms=1000.0,
                raw_disk_bytes=0,
                durable_disk_bytes=0,
            ),
        ),
        (
            PilotWorkspace.INCIDENTS,
            PrivacyIncident(
                id=UUID("00000000-0000-0000-0000-000000000004"),
                occurred_at=manifest.started_at,
                category=PrivacyIncidentCategory.OTHER,
                severity=Severity.INFO,
                summary="Synthetic redacted incident format example.",
                resolved_at=manifest.started_at,
            ),
        ),
    )
    lines = [
        "# Pilot JSONL examples",
        "",
        "These synthetic objects show the exact current schema. Each object must be "
        "written as one complete line in the named JSONL file. Do not copy the "
        "examples as real evidence.",
        "",
    ]
    for label, record in records:
        lines.extend(
            (
                f"## `{label}`",
                "",
                "```json",
                record.model_dump_json(),
                "```",
                "",
            )
        )
    return "\n".join(lines)
