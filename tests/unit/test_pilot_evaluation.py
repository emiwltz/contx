"""Deterministic scoring and private-workspace tests for the real pilot."""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel

from contx.evaluation import (
    GateResult,
    GateStatus,
    GroundTruthEntry,
    GroundTruthImportance,
    GroundTruthKind,
    PilotCondition,
    PilotDataset,
    PilotEvaluationError,
    PilotManifest,
    PilotReport,
    PilotScenario,
    PilotThresholds,
    PilotWorkspace,
    ResourceLimits,
    ResourcePhase,
    ResourceSample,
    TechnicalSnapshot,
    TrialEvaluation,
    evaluate_pilot,
    render_pilot_report,
)

START = datetime(2026, 8, 3, 8, tzinfo=UTC)
END = START + timedelta(days=7)


def test_complete_paired_pilot_passes_all_deterministic_gates() -> None:
    dataset = _complete_dataset()

    report = evaluate_pilot(dataset, evaluated_at=END)

    assert report.ready_for_v0_decision
    assert report.important_event_recall == 1.0
    assert {item.scenario for item in report.behavior_comparisons} == set(
        PilotScenario
    )
    assert all(item.demonstrated_benefit for item in report.behavior_comparisons)
    assert all(gate.status is GateStatus.PASS for gate in report.gates)
    assert report.resources is not None
    assert report.resources.p95_total_cpu_percent == 2.0
    assert report.resources.p95_total_rss_bytes == 6_100_000_000


def test_report_requires_operator_approved_resource_limits() -> None:
    dataset = _complete_dataset(resource_limits=ResourceLimits())

    report = evaluate_pilot(dataset, evaluated_at=END)

    gate = _gate(report, "daily_resource_use")
    assert gate.status is GateStatus.REVIEW
    assert not report.ready_for_v0_decision


def test_report_is_incomplete_before_planned_end() -> None:
    report = evaluate_pilot(
        _complete_dataset(), evaluated_at=START + timedelta(days=6)
    )

    assert _gate(report, "pilot_duration").status is GateStatus.INCOMPLETE
    assert not report.ready_for_v0_decision


def test_false_memory_rate_at_provisional_ceiling_fails() -> None:
    dataset = _complete_dataset(
        technical=_technical(accepted=10, false=1),
    )

    report = evaluate_pilot(dataset, evaluated_at=END)

    assert _gate(report, "false_memory_rate").status is GateStatus.FAIL


def test_mismatched_pair_is_rejected_before_scoring() -> None:
    dataset = _complete_dataset()
    changed = dataset.trials[0].model_copy(update={"prompt_key": "different"})
    invalid = dataset.model_copy(update={"trials": (changed, *dataset.trials[1:])})

    with pytest.raises(PilotEvaluationError, match="does not compare the same task"):
        evaluate_pilot(invalid, evaluated_at=END)


def test_single_in_progress_trial_leaves_comparison_gate_incomplete() -> None:
    dataset = _complete_dataset()
    incomplete = dataset.model_copy(update={"trials": dataset.trials[:1]})

    report = evaluate_pilot(incomplete, evaluated_at=END)

    assert report.behavior_comparisons == ()
    assert _gate(report, "three_behavior_pairs").status is GateStatus.INCOMPLETE


def test_interim_report_ignores_evidence_recorded_after_cutoff() -> None:
    dataset = _complete_dataset()
    future = dataset.trials[0].model_copy(
        update={"id": _uuid(800), "pair_key": "future", "evaluated_at": END}
    )
    with_future = dataset.model_copy(update={"trials": (*dataset.trials, future)})

    report = evaluate_pilot(
        with_future,
        evaluated_at=START + timedelta(days=6),
    )

    assert report.behavior_comparisons == ()
    assert _gate(report, "pilot_duration").status is GateStatus.INCOMPLETE


def test_missing_ground_truth_reference_is_rejected() -> None:
    dataset = _complete_dataset()
    changed = dataset.trials[0].model_copy(
        update={"relevant_truth_ids": (_uuid(999),)}
    )
    invalid = dataset.model_copy(update={"trials": (changed, *dataset.trials[1:])})

    with pytest.raises(PilotEvaluationError, match="missing ground-truth"):
        evaluate_pilot(invalid, evaluated_at=END)


def test_workspace_round_trips_strict_jsonl_and_private_permissions(
    tmp_path: Path,
) -> None:
    source = _complete_dataset()
    workspace = PilotWorkspace((tmp_path / "pilot").resolve())
    workspace.prepare(source.manifest)
    _write_records(workspace.root / workspace.GROUND_TRUTH, source.ground_truth)
    _write_records(workspace.root / workspace.TRIALS, source.trials)
    _write_records(
        workspace.root / workspace.TECHNICAL, source.technical_snapshots
    )
    _write_records(workspace.root / workspace.RESOURCES, source.resource_samples)
    _write_records(workspace.root / workspace.INCIDENTS, source.privacy_incidents)

    loaded = workspace.load()
    report = evaluate_pilot(loaded, evaluated_at=END)
    report_path = workspace.write_report(report)

    assert loaded == source
    assert stat.S_IMODE(workspace.root.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in workspace.root.iterdir()
    )
    rendered = report_path.read_text(encoding="utf-8")
    assert "v0 decision readiness: **ready**" in rendered
    assert "Decision content that stays private" not in rendered


def test_workspace_rejects_broad_file_permissions(tmp_path: Path) -> None:
    workspace = PilotWorkspace((tmp_path / "pilot").resolve())
    workspace.prepare(_manifest())
    target = workspace.root / workspace.GROUND_TRUTH
    target.chmod(0o644)

    with pytest.raises(PilotEvaluationError, match="permissions are too broad"):
        workspace.load()


def test_workspace_rejects_symlinked_input(tmp_path: Path) -> None:
    workspace = PilotWorkspace((tmp_path / "pilot").resolve())
    workspace.prepare(_manifest())
    target = workspace.root / workspace.TRIALS
    target.unlink()
    target.symlink_to(workspace.root / workspace.GROUND_TRUTH)

    with pytest.raises(PilotEvaluationError, match="must not be a symlink"):
        workspace.load()


def test_rendered_report_never_contains_scored_content() -> None:
    dataset = _complete_dataset()

    rendered = render_pilot_report(evaluate_pilot(dataset, evaluated_at=END))

    assert "Decision content that stays private" not in rendered
    assert "resume-project" not in rendered
    assert "project_resumption" in rendered
    assert "aggregate scores" in rendered


def _complete_dataset(
    *,
    resource_limits: ResourceLimits | None = None,
    technical: TechnicalSnapshot | None = None,
) -> PilotDataset:
    limits = resource_limits or ResourceLimits(
        p95_total_cpu_percent_max=5.0,
        p95_total_rss_bytes_max=8_000_000_000,
        p95_detection_latency_ms_max=2_000.0,
    )
    manifest = _manifest(resource_limits=limits)
    truths = tuple(
        GroundTruthEntry(
            id=_uuid(index),
            occurred_at=START + timedelta(days=index),
            kind=(
                GroundTruthKind.DECISION
                if scenario is PilotScenario.PROJECT_RESUMPTION
                else GroundTruthKind.PROJECT_WORK
            ),
            importance=GroundTruthImportance.IMPORTANT,
            scenarios=(scenario,),
            summary="Decision content that stays private",
            project="CONTX",
        )
        for index, scenario in enumerate(PilotScenario, start=1)
    )
    trials: list[TrialEvaluation] = []
    for index, scenario in enumerate(PilotScenario, start=1):
        truth_id = truths[index - 1].id
        trials.extend(
            (
                _trial(
                    identifier=_uuid(100 + index * 2),
                    pair_key=f"pair-{index}",
                    prompt_key=f"prompt-{index}",
                    scenario=scenario,
                    condition=PilotCondition.WITHOUT_CONTX,
                    truth_id=truth_id,
                    retrieved=False,
                ),
                _trial(
                    identifier=_uuid(101 + index * 2),
                    pair_key=f"pair-{index}",
                    prompt_key=f"prompt-{index}",
                    scenario=scenario,
                    condition=PilotCondition.WITH_CONTX,
                    truth_id=truth_id,
                    retrieved=True,
                ),
            )
        )
    return PilotDataset(
        manifest=manifest,
        ground_truth=truths,
        trials=tuple(trials),
        technical_snapshots=(technical or _technical(),),
        resource_samples=tuple(
            ResourceSample(
                captured_at=START + timedelta(days=index),
                phase=phase,
                collector_cpu_percent=2.0 if index == 4 else 1.0,
                collector_rss_bytes=(
                    200_000_000 if index == 4 else 100_000_000
                ),
                local_model_rss_bytes=(
                    6_000_000_000
                    if phase is ResourcePhase.MODEL_PROCESSING
                    else 0
                ),
                web_rss_bytes=(
                    100_000_000
                    if phase is ResourcePhase.WEB_INTERACTION
                    else 0
                ),
                detection_latency_ms=1000 if index == 4 else 500,
                raw_disk_bytes=2000 if index == 4 else 1000,
                durable_disk_bytes=4000 if index == 4 else 2000,
            )
            for index, phase in enumerate(ResourcePhase, start=1)
        ),
        privacy_incidents=(),
    )


def _manifest(*, resource_limits: ResourceLimits | None = None) -> PilotManifest:
    return PilotManifest(
        pilot_id=_uuid(900),
        started_at=START,
        planned_end_at=END,
        timezone="Europe/Paris",
        target_machine="Mac M4 16 GB",
        thresholds=PilotThresholds(
            resource_limits=resource_limits or ResourceLimits()
        ),
    )


def _trial(
    *,
    identifier: UUID,
    pair_key: str,
    prompt_key: str,
    scenario: PilotScenario,
    condition: PilotCondition,
    truth_id: UUID,
    retrieved: bool,
) -> TrialEvaluation:
    enabled = condition is PilotCondition.WITH_CONTX
    return TrialEvaluation(
        id=identifier,
        pair_key=pair_key,
        prompt_key=prompt_key,
        scenario=scenario,
        condition=condition,
        evaluated_at=END,
        relevant_truth_ids=(truth_id,),
        retrieved_truth_ids=(truth_id,) if retrieved else (),
        supported_factual_claims=2 if enabled else 1,
        total_factual_claims=2,
        materially_false_claims=0 if enabled else 1,
        irrelevant_claims=0 if enabled else 1,
        duplicate_claims=0,
        clarification_questions=0 if enabled else 1,
        relevance_score=5 if enabled else 3,
        synthesis_score=5 if enabled else 3,
        response_latency_ms=1000 if enabled else 800,
        context_bytes=1000 if enabled else 0,
        wake_latency_ms=50 if enabled else None,
    )


def _technical(*, accepted: int = 10, false: int = 0) -> TechnicalSnapshot:
    return TechnicalSnapshot(
        captured_at=END,
        accepted_memories=accepted,
        accepted_memories_with_provenance=accepted,
        materially_false_memories=false,
        irrelevant_memories=0,
        duplicate_memories=0,
        manual_corrections=0,
        sensitive_promotions=0,
        synthetic_secret_promotions=0,
        model_outputs=10,
        invalid_model_outputs=0,
        unknown_model_attempts=0,
        raw_records_past_retention=0,
        excluded_context_captures=0,
        remote_user_content_transports=0,
        active_context_bytes=10_000,
        wake_latency_ms=50,
    )


def _gate(report: PilotReport, key: str) -> GateResult:
    return next(gate for gate in report.gates if gate.key == key)


def _write_records(path: Path, records: tuple[BaseModel, ...]) -> None:
    payload = "".join(
        json.dumps(record.model_dump(mode="json"), separators=(",", ":")) + "\n"
        for record in records
    )
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o600)


def _uuid(value: int) -> UUID:
    return UUID(int=value)
