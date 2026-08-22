"""Transactional composition of config, preflights, and both user jobs."""

from __future__ import annotations

from pathlib import Path

import pytest

from contx.application.background_collection import (
    BackgroundCollectionLifecycleService,
    BackgroundCollectionStatus,
)
from contx.collectors.macos import CapabilityStatus, CollectionCapability
from contx.daemon.launch_agent_lifecycle import LaunchAgentStatus, ManifestState
from contx.errors import ConfigurationError, DatabaseError, MemoryStoreUnavailableError
from contx.model_provider import LocalModelRuntimeStatus
from contx.settings import (
    RuntimePaths,
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
    set_background_collection_features,
)

LABELS = ("io.contx.collector", "io.contx.processor")


class FakeModel:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def status(self) -> LocalModelRuntimeStatus:
        return LocalModelRuntimeStatus(
            endpoint="http://127.0.0.1:11434",
            runtime_available=self.available,
            runtime_version="1.0" if self.available else None,
            model="gemma4:e4b-it-qat",
            model_available=self.available,
            model_digest="sha256:synthetic" if self.available else None,
            reason_code=None if self.available else "runtime_unavailable",
        )


class FakeOptMem:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls = 0

    def validate_executable(self) -> None:
        self.calls += 1
        if not self.available:
            raise MemoryStoreUnavailableError("synthetic missing OptMem")


class FakeAgents:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.states = {label: ManifestState.MISSING for label in LABELS}
        self.loaded: set[str] = set()
        self.events: list[str] = []
        self.fail_load = False
        self.fail_unload = False
        self.fail_activation_inspection = False
        self.paused = False
        self.collector_running = False
        self.start_collector_on_load = True
        self.unavailable_executables: set[str] = set()

    def inspect(self) -> tuple[LaunchAgentStatus, ...]:
        return tuple(
            LaunchAgentStatus(
                label=label,
                manifest_state=self.states[label],
                loaded=(
                    label in self.loaded
                    and not (self.fail_activation_inspection and label == LABELS[-1])
                ),
                executable_available=label not in self.unavailable_executables,
            )
            for label in LABELS
        )

    def stage(self) -> tuple[str, ...]:
        self.events.append("stage")
        created = tuple(
            label for label in LABELS if self.states[label] is ManifestState.MISSING
        )
        for label in created:
            self.states[label] = ManifestState.MATCHING
        return created

    def load(self) -> tuple[str, ...]:
        self.events.append("load")
        assert self.paused
        settings = load_settings(self.paths, environ={})
        assert settings.collection.background_collection_enabled
        assert settings.collection.window_titles_enabled
        assert settings.collection.screenshots_enabled
        if self.fail_load:
            raise ConfigurationError("synthetic load failure")
        loaded = tuple(label for label in LABELS if label not in self.loaded)
        self.loaded.update(loaded)
        if self.start_collector_on_load:
            self.collector_running = True
        return loaded

    def unload(self) -> tuple[str, ...]:
        self.events.append("unload")
        assert self.paused
        assert load_settings(
            self.paths, environ={}
        ).collection.background_collection_enabled
        if self.fail_unload:
            raise ConfigurationError("synthetic unload failure")
        unloaded = tuple(label for label in LABELS if label in self.loaded)
        self.loaded.clear()
        self.collector_running = False
        return unloaded

    def unload_staged(self, labels: tuple[str, ...]) -> tuple[str, ...]:
        self.events.append("rollback-unload")
        assert self.paused
        for label in labels:
            self.loaded.discard(label)
        if LABELS[0] in labels:
            self.collector_running = False
        return labels

    def remove(self) -> tuple[str, ...]:
        self.events.append("remove")
        assert not load_settings(
            self.paths, environ={}
        ).collection.background_collection_enabled
        removed = tuple(
            label for label in LABELS if self.states[label] is ManifestState.MATCHING
        )
        for label in removed:
            self.states[label] = ManifestState.MISSING
        return removed

    def remove_staged(self, labels: tuple[str, ...]) -> tuple[str, ...]:
        self.events.append("rollback-remove")
        for label in labels:
            self.states[label] = ManifestState.MISSING
        return labels


def test_readiness_checks_desired_features_without_mutating_config(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    observed: list[tuple[bool, bool, bool]] = []

    def capabilities(
        settings: object, *, platform: str
    ) -> tuple[CollectionCapability, ...]:
        collection = settings
        observed.append(
            (
                collection.background_collection_enabled,  # type: ignore[attr-defined]
                collection.window_titles_enabled,  # type: ignore[attr-defined]
                collection.screenshots_enabled,  # type: ignore[attr-defined]
            )
        )
        assert platform == "darwin"
        return _available_capabilities()

    service = _service(paths, agents, capability_detector=capabilities)

    status = service.inspect()

    assert status.ready_to_activate
    assert not status.active
    assert observed == [(True, True, True)]
    settings = load_settings(paths, environ={})
    assert not settings.collection.background_collection_enabled
    assert agents.states == {label: ManifestState.MISSING for label in LABELS}


def test_readiness_reports_a_missing_native_host_without_mutation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.unavailable_executables.add(LABELS[0])

    status = _service(paths, agents).inspect()

    assert not status.ready_to_activate
    assert status.problems == (f"{LABELS[0]}:executable_unavailable",)
    assert agents.states == {label: ManifestState.MISSING for label in LABELS}


def test_unmanaged_running_collector_blocks_activation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.collector_running = True
    service = _service(paths, agents)

    status = service.inspect()

    assert status.problems == ("collector_running_without_managed_host",)
    with pytest.raises(ConfigurationError, match="preflight failed"):
        service.activate()
    assert agents.events == []


def test_active_status_requires_both_exact_managed_jobs() -> None:
    status = BackgroundCollectionStatus(
        background_enabled=True,
        window_titles_enabled=True,
        screenshots_enabled=True,
        collection_paused=False,
        collector_running=True,
        agents=(
            LaunchAgentStatus(
                label=LABELS[0],
                manifest_state=ManifestState.MATCHING,
                loaded=True,
            ),
        ),
        problems=(),
    )

    assert not status.active


def test_active_status_requires_a_live_collector() -> None:
    status = BackgroundCollectionStatus(
        background_enabled=True,
        window_titles_enabled=True,
        screenshots_enabled=True,
        collection_paused=False,
        collector_running=False,
        agents=tuple(
            LaunchAgentStatus(
                label=label,
                manifest_state=ManifestState.MATCHING,
                loaded=True,
            )
            for label in LABELS
        ),
        problems=(),
    )

    assert not status.active


def test_activation_converges_config_and_both_jobs(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    service = _service(paths, agents)

    result = service.activate()

    assert result.created_manifests == LABELS
    assert result.loaded_agents == LABELS
    assert result.status.active
    assert agents.events == ["pause", "stage", "load", "resume"]


def test_activation_resumes_an_existing_pause_only_after_both_jobs_load(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.paused = True
    service = _service(paths, agents)

    result = service.activate()

    assert result.status.active
    assert agents.events == ["stage", "load", "resume"]
    assert not agents.paused


def test_activation_is_idempotent_when_everything_is_already_active(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    set_background_collection_features(paths, enabled=True)
    agents = FakeAgents(paths)
    agents.states = {label: ManifestState.MATCHING for label in LABELS}
    agents.loaded.update(LABELS)
    agents.collector_running = True
    service = _service(paths, agents)

    result = service.activate()

    assert result.created_manifests == result.loaded_agents == ()
    assert result.status.active
    assert agents.events == []


def test_activation_keeps_pause_until_collector_is_live_and_rolls_back_timeout(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.start_collector_on_load = False
    wait_clock = _FakeWaitClock()
    service = _service(
        paths,
        agents,
        collector_start_timeout_seconds=0.5,
        monotonic=wait_clock.monotonic,
        sleep=wait_clock.sleep,
    )
    original = paths.config_file.read_bytes()

    with pytest.raises(ConfigurationError, match="while paused"):
        service.activate()

    assert paths.config_file.read_bytes() == original
    assert agents.loaded == set()
    assert not agents.collector_running
    assert not agents.paused
    assert agents.events == [
        "pause",
        "stage",
        "load",
        "rollback-unload",
        "rollback-remove",
        "resume",
    ]


def test_activation_failure_restores_exact_disabled_state(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.fail_load = True
    service = _service(paths, agents)
    original = paths.config_file.read_bytes()

    with pytest.raises(ConfigurationError, match="synthetic load failure"):
        service.activate()

    assert paths.config_file.read_bytes() == original
    assert agents.states == {label: ManifestState.MISSING for label in LABELS}
    assert agents.loaded == set()
    assert not agents.paused
    assert agents.events == [
        "pause",
        "stage",
        "load",
        "rollback-remove",
        "resume",
    ]


def test_activation_failure_after_resume_pauses_before_rollback(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.fail_activation_inspection = True
    service = _service(paths, agents)
    original = paths.config_file.read_bytes()

    with pytest.raises(ConfigurationError, match="consistent active state"):
        service.activate()

    assert paths.config_file.read_bytes() == original
    assert agents.states == {label: ManifestState.MISSING for label in LABELS}
    assert agents.loaded == set()
    assert not agents.paused
    assert agents.events == [
        "pause",
        "stage",
        "load",
        "resume",
        "pause",
        "rollback-unload",
        "rollback-remove",
        "resume",
    ]


@pytest.mark.parametrize(
    "failure_type",
    (ConfigurationError, KeyboardInterrupt),
)
def test_activation_treats_a_failing_resume_as_potentially_committed(
    tmp_path: Path,
    failure_type: type[BaseException],
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    resume_calls = 0

    def resume_once_fails() -> None:
        nonlocal resume_calls
        resume_calls += 1
        agents.events.append("resume")
        agents.paused = False
        if resume_calls == 1:
            raise failure_type("synthetic committed resume failure")

    service = _service(paths, agents, resume=resume_once_fails)
    original = paths.config_file.read_bytes()

    with pytest.raises(failure_type, match="committed resume failure"):
        service.activate()

    assert paths.config_file.read_bytes() == original
    assert agents.states == {label: ManifestState.MISSING for label in LABELS}
    assert agents.loaded == set()
    assert not agents.paused
    assert agents.events == [
        "pause",
        "stage",
        "load",
        "resume",
        "pause",
        "rollback-unload",
        "rollback-remove",
        "resume",
    ]


def test_deactivation_pauses_before_unload_then_disables_and_removes(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    set_background_collection_features(paths, enabled=True)
    agents = FakeAgents(paths)
    agents.states = {label: ManifestState.MATCHING for label in LABELS}
    agents.loaded.update(LABELS)

    def pause() -> None:
        agents.events.append("pause")
        agents.paused = True

    service = _service(paths, agents, pause=pause)

    result = service.deactivate()

    assert result.unloaded_agents == LABELS
    assert result.removed_manifests == LABELS
    assert not result.status.active
    assert agents.events == ["pause", "unload", "remove"]
    assert not load_settings(paths, environ={}).collection.background_collection_enabled


def test_deactivation_failure_leaves_config_enabled_but_collection_paused(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    set_background_collection_features(paths, enabled=True)
    agents = FakeAgents(paths)
    agents.states = {label: ManifestState.MATCHING for label in LABELS}
    agents.loaded.update(LABELS)
    agents.fail_unload = True

    def pause() -> None:
        agents.events.append("pause")
        agents.paused = True

    service = _service(paths, agents, pause=pause)

    with pytest.raises(ConfigurationError, match="synthetic unload failure"):
        service.deactivate()

    assert agents.paused
    assert agents.loaded == set(LABELS)
    assert load_settings(paths, environ={}).collection.background_collection_enabled
    assert agents.events == ["pause", "unload"]


def test_readiness_reports_model_optmem_schema_and_manifest_failures(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)
    agents.states[LABELS[0]] = ManifestState.CONFLICTING
    service = _service(
        paths,
        agents,
        model=FakeModel(available=False),
        optmem=FakeOptMem(available=False),
        schema_is_current=lambda: False,
    )

    status = service.inspect()

    assert not status.ready_to_activate
    assert status.problems == (
        "runtime_unavailable",
        "optmem_unavailable",
        "database_schema_not_current",
        f"{LABELS[0]}:manifest_conflicting",
    )


def test_readiness_fails_closed_when_pause_state_cannot_be_read(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    agents = FakeAgents(paths)

    def unavailable() -> bool:
        raise DatabaseError("synthetic unreadable collection control")

    service = _service(paths, agents, collection_is_paused=unavailable)

    status = service.inspect()

    assert status.collection_paused
    assert not status.ready_to_activate
    assert status.problems == ("collection_control_unavailable",)


def _service(
    paths: RuntimePaths,
    agents: FakeAgents,
    *,
    model: FakeModel | None = None,
    optmem: FakeOptMem | None = None,
    pause: object | None = None,
    resume: object | None = None,
    collection_is_paused: object | None = None,
    capability_detector: object | None = None,
    schema_is_current: object | None = None,
    collector_start_timeout_seconds: float = 10.0,
    monotonic: object | None = None,
    sleep: object | None = None,
) -> BackgroundCollectionLifecycleService:
    def default_pause() -> None:
        agents.events.append("pause")
        agents.paused = True

    def default_resume() -> None:
        agents.events.append("resume")
        agents.paused = False

    pause_callable = pause if callable(pause) else default_pause
    resume_callable = resume if callable(resume) else default_resume
    paused_callable = (
        collection_is_paused
        if callable(collection_is_paused)
        else lambda: agents.paused
    )
    detector = (
        capability_detector
        if callable(capability_detector)
        else lambda _settings, *, platform: _available_capabilities()
    )
    schema = schema_is_current if callable(schema_is_current) else lambda: True
    return BackgroundCollectionLifecycleService(
        paths=paths,
        agents=agents,
        model=model or FakeModel(),
        optmem=optmem or FakeOptMem(),
        pause_collection=pause_callable,
        resume_collection=resume_callable,
        collection_is_paused=paused_callable,
        collector_is_running=lambda: agents.collector_running,
        platform="darwin",
        capability_detector=detector,
        schema_is_current=schema,
        collector_start_timeout_seconds=collector_start_timeout_seconds,
        monotonic=(monotonic if callable(monotonic) else lambda: 0.0),
        sleep=(sleep if callable(sleep) else lambda _seconds: None),
    )


class _FakeWaitClock:
    def __init__(self) -> None:
        self.current = 0.0

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += seconds


def _paths(root: Path) -> RuntimePaths:
    paths = resolve_runtime_paths(environ={"CONTX_RUNTIME_ROOT": str(root)})
    initialize_runtime_paths(paths)
    return paths


def _available_capabilities() -> tuple[CollectionCapability, ...]:
    return tuple(
        CollectionCapability(name=name, status=CapabilityStatus.AVAILABLE)
        for name in (
            "active_application",
            "system_notifications",
            "idle_detection",
            "window_titles",
            "screenshots",
        )
    )
