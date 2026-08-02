"""CLI initialization integration tests."""

import re
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

import contx.cli.app as cli_module
from contx.cli.app import app
from contx.db import create_database_engine, session_scope
from contx.db.models import EventModel, MemoryLinkModel
from contx.db.repositories import EventCorrectionRepository, PipelineRepository
from contx.memory_store import AgentProposalEvaluation, RecordingMemoryStore
from contx.model_provider import (
    DEFAULT_MODEL,
    LocalModelExecution,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    ModelInterpretation,
)
from contx.models import (
    EpistemicStatus,
    Event,
    EventType,
    Observation,
    Sensitivity,
    SourceType,
)
from contx.raw_store import FilesystemRawStore
from contx.settings import RUNTIME_ROOT_ENV, resolve_runtime_paths

runner = CliRunner()
MODEL = DEFAULT_MODEL


class RecordingCorrectionComposer:
    def __init__(self, correction: str) -> None:
        self._correction = correction
        self.calls: list[tuple[str, str, int]] = []

    provider = "ollama"
    endpoint = "http://127.0.0.1:11434"
    model = "synthetic-local-model"
    model_digest = "synthetic-digest"
    prompt_version = "memory-correction-v1"
    output_schema_version = "memory-correction-output-v1"

    def compose(
        self,
        *,
        original: str,
        replacement: str,
        max_bytes: int,
    ) -> str:
        self.calls.append((original, replacement, max_bytes))
        return self._correction


class RecordingProposalEvaluator:
    provider: Literal["ollama"] = "ollama"
    endpoint = "http://127.0.0.1:11434"
    model = "synthetic-local-model"
    model_digest = "synthetic-digest"
    prompt_version = "agent-proposal-adoption-v2"
    output_schema_version = "agent-proposal-adoption-output-v2"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate(
        self,
        *,
        proposal: str,
        reference: str,
        active_memories: tuple[str, ...],
        minimum_confidence: float,
    ) -> AgentProposalEvaluation:
        self.calls.append(proposal)
        return AgentProposalEvaluation(
            decision="accepted",
            reason_code="supported_novel",
            confidence=0.9,
        )


def test_init_is_idempotent_and_status_is_truthful(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    first = runner.invoke(app, ["init"], env=environment)
    second = runner.invoke(app, ["init"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert status.exit_code == 0
    assert "configuration: present" in status.stdout
    assert "database: present" in status.stdout
    assert "memory: not initialized" in status.stdout
    assert "schema: current" in status.stdout
    assert "background collection: disabled" in status.stdout
    assert "collector daemon: stopped" in status.stdout

    paths = resolve_runtime_paths(environment)
    assert stat.S_IMODE(paths.config_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.database_file.stat().st_mode) == 0o600


def test_run_once_synthetic_uses_the_initialized_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    memory = RecordingMemoryStore()
    monkeypatch.setattr(
        cli_module,
        "_build_memory_store",
        lambda _path, **_kwargs: memory,
    )

    result = runner.invoke(app, ["run-once", "--source", "synthetic"], env=environment)
    wake = runner.invoke(app, ["wake"], env=environment)

    assert result.exit_code == 0
    assert "run: succeeded" in result.stdout
    assert "observations: 5" in result.stdout
    assert "events: 2" in result.stdout
    assert "accepted candidates: 1" in result.stdout
    assert "rejected candidates: 1" in result.stdout
    assert "stored memories: 1" in result.stdout
    assert wake.exit_code == 0
    assert "Resume CONTX" in wake.stdout
    assert wake.stdout.endswith("multi-day gap.\n")


def test_memory_correction_cli_appends_explicit_historical_supersession(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    memory = RecordingMemoryStore()
    composer = RecordingCorrectionComposer(
        "Correction: CONTX now resumes locally; the earlier target is obsolete."
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_store",
        lambda _path, **_kwargs: memory,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_memory_correction_composer",
        lambda _settings: composer,
    )
    seeded = runner.invoke(
        app,
        ["run-once", "--source", "synthetic"],
        env=environment,
    )
    paths = resolve_runtime_paths(environment)
    engine = create_database_engine(paths.database_file)
    try:
        with session_scope(engine) as database_session:
            original_id = UUID(
                database_session.scalars(select(MemoryLinkModel.id)).one()
            )
    finally:
        engine.dispose()

    corrected = runner.invoke(
        app,
        ["correct", str(original_id), "CONTX now resumes locally."],
        env=environment,
    )
    wake = runner.invoke(app, ["wake"], env=environment)

    assert seeded.exit_code == 0
    assert corrected.exit_code == 0
    assert f"supersedes: {original_id}" in corrected.stdout
    assert "replayed: no" in corrected.stdout
    assert "memory maintenance: not required" in corrected.stdout
    assert wake.exit_code == 0
    assert "Correction: CONTX now resumes locally" in wake.stdout
    assert len(composer.calls) == 1


def test_pause_blocks_live_source_before_macos_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    memory = RecordingMemoryStore()
    monkeypatch.setattr(
        cli_module,
        "_build_memory_store",
        lambda _path, **_kwargs: memory,
    )

    paused = runner.invoke(app, ["pause", "--for", "15m"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)
    run = runner.invoke(app, ["run-once", "--source", "active-app"], env=environment)
    resumed = runner.invoke(app, ["resume"], env=environment)

    assert paused.exit_code == 0
    assert "collection: paused until" in paused.stdout
    assert "collection: paused" in status.stdout
    assert run.exit_code == 0
    assert "observations: 0" in run.stdout
    assert resumed.stdout == "collection: active\n"


def test_user_exclusion_can_be_added_and_listed(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    added = runner.invoke(
        app,
        [
            "exclusions",
            "add",
            "com.example.private",
            "--type",
            "app_bundle_id",
        ],
        env=environment,
    )
    listed = runner.invoke(app, ["exclusions", "list"], env=environment)

    assert added.exit_code == 0
    assert listed.exit_code == 0
    assert "app_bundle_id enabled user com.example.private" in listed.stdout


def test_empty_raw_purge_is_successful_and_audited(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    result = runner.invoke(app, ["purge"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)

    assert result.exit_code == 0
    assert "purged observations: 0" in result.stdout
    assert "reclaimed bytes: 0" in result.stdout
    assert "orphan artifacts deleted: 0" in result.stdout
    assert "raw usage: 0 bytes" in status.stdout


def test_capabilities_do_not_enable_or_request_sensitive_access(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    result = runner.invoke(app, ["capabilities"], env=environment)

    assert result.exit_code == 0
    assert "active_application:" in result.stdout
    assert "window_titles: disabled (disabled_by_configuration)" in result.stdout
    assert "screenshots: disabled (disabled_by_configuration)" in result.stdout


def test_model_status_preflights_without_sending_user_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AvailableProvider:
        def status(self) -> LocalModelRuntimeStatus:
            return LocalModelRuntimeStatus(
                endpoint="http://127.0.0.1:11434",
                runtime_available=True,
                runtime_version="0.32.5",
                model=MODEL,
                model_available=True,
                model_digest="a" * 64,
            )

    monkeypatch.setattr(
        cli_module,
        "_build_local_model_provider",
        lambda _settings: AvailableProvider(),
    )

    result = runner.invoke(
        app,
        ["model", "status"],
        env={RUNTIME_ROOT_ENV: str(tmp_path)},
    )

    assert result.exit_code == 0
    assert "runtime: available" in result.stdout
    assert f"model: installed ({MODEL})" in result.stdout
    assert f"model digest: {'a' * 64}" in result.stdout


def test_timeline_cli_builds_and_reads_an_empty_frozen_window(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    built = runner.invoke(
        app,
        [
            "timeline",
            "build",
            "--from",
            "2026-08-02T00:00:00Z",
            "--until",
            "2026-08-03T00:00:00Z",
        ],
        env=environment,
    )

    assert built.exit_code == 0
    assert "run: succeeded" in built.stdout
    assert "processing version: session-events-v1" in built.stdout
    assert "transformations: 0" in built.stdout
    assert "events: 0" in built.stdout
    match = re.search(r"timeline run: ([0-9a-f-]{36})", built.stdout)
    assert match is not None

    shown = runner.invoke(
        app,
        ["timeline", "show", match.group(1)],
        env=environment,
    )

    assert shown.exit_code == 0
    assert f"timeline run: {match.group(1)}" in shown.stdout
    assert "events: 0" in shown.stdout


def test_agent_proposal_without_provenance_is_deferred_without_memory_write(
    tmp_path: Path,
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}

    result = runner.invoke(
        app,
        ["propose", "Synthetic unsupported memory proposal."],
        env=environment,
    )

    assert result.exit_code == 0
    assert "status: deferred" in result.stdout
    assert "reason: provenance_reference_required" in result.stdout
    assert "final memory writes: 0" in result.stdout
    paths = resolve_runtime_paths(environment)
    assert not (paths.memory / "LOG.txt").exists()


def test_proposal_review_commands_require_explicit_adoption_and_are_replayable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    memory = RecordingMemoryStore()
    evaluator = RecordingProposalEvaluator()
    monkeypatch.setattr(
        cli_module,
        "_build_memory_store",
        lambda _path, **_kwargs: memory,
    )
    monkeypatch.setattr(
        cli_module,
        "_build_agent_proposal_evaluator",
        lambda _settings: evaluator,
    )
    seeded = runner.invoke(
        app,
        ["run-once", "--source", "synthetic"],
        env=environment,
    )
    assert seeded.exit_code == 0
    paths = resolve_runtime_paths(environment)
    engine = create_database_engine(paths.database_file)
    try:
        with session_scope(engine) as database_session:
            event_id = UUID(database_session.scalars(select(EventModel.id)).first())
    finally:
        engine.dispose()

    submitted = runner.invoke(
        app,
        [
            "propose",
            "CONTX work should resume from its verified local state.",
            "--reference-type",
            "event",
            "--reference",
            str(event_id),
        ],
        env=environment,
    )
    assert submitted.exit_code == 0
    proposal_match = re.search(r"proposal: ([0-9a-f-]{36})", submitted.stdout)
    assert proposal_match is not None
    proposal_id = proposal_match.group(1)

    listed = runner.invoke(app, ["proposals", "list"], env=environment)
    shown = runner.invoke(
        app,
        ["proposals", "show", proposal_id],
        env=environment,
    )
    adopted = runner.invoke(
        app,
        ["proposals", "adopt", proposal_id],
        env=environment,
    )
    replay = runner.invoke(
        app,
        ["proposals", "adopt", proposal_id],
        env=environment,
    )

    assert listed.exit_code == 0
    assert f"{proposal_id}\tpending\tevent:{event_id}" in listed.stdout
    assert shown.exit_code == 0
    assert "status: pending" in shown.stdout
    assert adopted.exit_code == 0
    assert "status: adopted" in adopted.stdout
    assert "decision: accepted" in adopted.stdout
    assert "reason: supported_novel" in adopted.stdout
    assert "replayed: no" in adopted.stdout
    assert replay.exit_code == 0
    assert "replayed: yes" in replay.stdout
    assert evaluator.calls == [
        "CONTX work should resume from its verified local state."
    ]
    assert memory.entries[-1] == (
        "CONTX work should resume from its verified local state."
    )

    second = runner.invoke(
        app,
        [
            "propose",
            "This second proposal should remain outside final memory.",
            "--reference-type",
            "event",
            "--reference",
            str(event_id),
        ],
        env=environment,
    )
    second_match = re.search(r"proposal: ([0-9a-f-]{36})", second.stdout)
    assert second_match is not None
    rejected = runner.invoke(
        app,
        ["proposals", "reject", second_match.group(1)],
        env=environment,
    )
    assert rejected.exit_code == 0
    assert "status: rejected" in rejected.stdout
    assert "reason: user_rejected" in rejected.stdout
    assert "final memory writes: 0" in rejected.stdout


def test_timeline_cli_rejects_naive_boundaries_without_creating_a_run(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "timeline",
            "build",
            "--from",
            "2026-08-02T00:00:00",
            "--until",
            "2026-08-03T00:00:00",
        ],
        env={RUNTIME_ROOT_ENV: str(tmp_path)},
    )

    assert result.exit_code == 2
    assert "timezone-aware ISO 8601" in result.stderr


def test_timeline_cli_appends_a_summary_correction(tmp_path: Path) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    assert runner.invoke(app, ["init"], env=environment).exit_code == 0
    paths = resolve_runtime_paths(environment)
    now = datetime.now(UTC)
    observation_id = UUID("00000000-0000-0000-0000-000000000551")
    event_id = UUID("00000000-0000-0000-0000-000000000552")
    engine = create_database_engine(paths.database_file)
    try:
        with session_scope(engine) as session:
            repository = PipelineRepository(session)
            repository.save_observation(
                Observation(
                    id=observation_id,
                    idempotency_key="1" * 64,
                    source_type=SourceType.SYNTHETIC,
                    captured_at=now,
                    started_at=now,
                    ended_at=now + timedelta(minutes=10),
                    expires_at=now + timedelta(hours=48),
                    created_at=now,
                )
            )
            repository.save_event(
                Event(
                    id=event_id,
                    idempotency_key="2" * 64,
                    lineage_key="3" * 64,
                    type=EventType.PROJECT_WORK,
                    summary="Initial synthetic event.",
                    facts={},
                    started_at=now,
                    ended_at=now + timedelta(minutes=10),
                    valid_from=now,
                    valid_until=now + timedelta(minutes=10),
                    epistemic_status=EpistemicStatus.INFERRED,
                    confidence=0.8,
                    sensitivity=Sensitivity.PERSONAL,
                    projects=("CONTX",),
                    source_observation_ids=(observation_id,),
                    processing_version="session-events-v1",
                    created_at=now,
                    updated_at=now,
                )
            )
    finally:
        engine.dispose()

    corrected = runner.invoke(
        app,
        [
            "timeline",
            "correct",
            str(event_id),
            "--summary",
            "Corrected synthetic event.",
            "--reason",
            "The initial summary was intentionally wrong.",
        ],
        env=environment,
    )

    assert corrected.exit_code == 0
    assert "correction appended:" in corrected.stdout
    engine = create_database_engine(paths.database_file)
    try:
        with session_scope(engine) as session:
            latest = EventCorrectionRepository(session).latest_for_lineages(
                ("3" * 64,)
            )["3" * 64]
            assert latest.replacement.summary == "Corrected synthetic event."
            assert latest.target_event_id == event_id
    finally:
        engine.dispose()


def test_process_command_runs_synthetic_screenshot_backlog_without_collection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = {RUNTIME_ROOT_ENV: str(tmp_path)}
    assert runner.invoke(app, ["init"], env=environment).exit_code == 0
    paths = resolve_runtime_paths(environment)
    now = datetime.now(UTC)
    observation_id = UUID("00000000-0000-0000-0000-000000000501")
    png = b"\x89PNG\r\n\x1a\nsynthetic-cli-model-fixture"
    store = FilesystemRawStore(paths.raw, disk_budget_bytes=1024 * 1024)
    artifact = store.write(
        png,
        artifact_id=observation_id,
        suffix=".png",
        captured_at=now,
        retention=timedelta(hours=48),
    )
    observation = Observation(
        id=observation_id,
        idempotency_key="d" * 64,
        source_type=SourceType.SCREENSHOT,
        captured_at=now,
        started_at=now,
        ended_at=now,
        app_name="Synthetic Editor",
        app_bundle_id="com.example.editor",
        window_title="Synthetic CLI model test",
        artifact_path=str(artifact.path),
        content_hash=artifact.content_hash,
        expires_at=artifact.expires_at,
        created_at=now,
    )
    engine = create_database_engine(paths.database_file)
    try:
        with session_scope(engine) as session:
            PipelineRepository(session).save_observation(observation)
    finally:
        engine.dispose()

    class AvailableProvider:
        def status(self) -> LocalModelRuntimeStatus:
            return LocalModelRuntimeStatus(
                endpoint="http://127.0.0.1:11434",
                runtime_available=True,
                runtime_version="0.32.5",
                model=MODEL,
                model_available=True,
                model_digest="a" * 64,
            )

        def interpret(self, request: LocalModelRequest) -> LocalModelExecution:
            called_at = datetime.now(UTC)
            return LocalModelExecution(
                request_id=request.id,
                source_observation_ids=request.source_observation_ids,
                endpoint="http://127.0.0.1:11434",
                runtime_version="0.32.5",
                model=MODEL,
                model_digest="a" * 64,
                image_sha256=request.image_sha256,
                interpretation=ModelInterpretation(
                    summary="Testing the CONTX process command.",
                    activity_type="testing",
                    observed_facts=("A synthetic editor is visible.",),
                    inferred_context=("The local pipeline is under test.",),
                    projects=("CONTX",),
                    entities=("Ollama",),
                    sensitivity=Sensitivity.PERSONAL,
                    sensitive_categories=(),
                    confidence=0.9,
                    memory_relevance=0.7,
                ),
                started_at=called_at,
                ended_at=called_at,
                wall_duration_ms=0,
            )

    monkeypatch.setattr(
        cli_module,
        "_build_local_model_provider",
        lambda _settings: AvailableProvider(),
    )

    processed = runner.invoke(app, ["process"], env=environment)
    status = runner.invoke(app, ["status"], env=environment)

    assert processed.exit_code == 0
    assert "run: succeeded" in processed.stdout
    assert "queued transformations: 1" in processed.stdout
    assert "succeeded transformations: 1" in processed.stdout
    assert "events built: 1" in processed.stdout
    assert "model backlog: 0" in processed.stdout
    assert "model backlog: 0" in status.stdout
    assert "model abandoned: 0" in status.stdout
    assert "model event backlog: 0" in status.stdout


def test_process_command_reports_required_model_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableProvider:
        def status(self) -> LocalModelRuntimeStatus:
            return LocalModelRuntimeStatus(
                endpoint="http://127.0.0.1:11434",
                runtime_available=False,
                model=MODEL,
                model_available=False,
                reason_code="runtime_unavailable",
            )

        def interpret(self, _request: LocalModelRequest) -> LocalModelExecution:
            raise AssertionError("unavailable model must not receive content")

    monkeypatch.setattr(
        cli_module,
        "_build_local_model_provider",
        lambda _settings: UnavailableProvider(),
    )

    result = runner.invoke(
        app,
        ["process"],
        env={RUNTIME_ROOT_ENV: str(tmp_path)},
    )

    assert result.exit_code == 2
    assert "run: failed" in result.stdout
    assert "runtime: unavailable" in result.stdout
    assert "model backlog: 0" in result.stdout
