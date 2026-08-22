"""Preflight and transactional lifecycle for authorized background collection."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from contx.collectors.macos import (
    CapabilityStatus,
    CollectionCapability,
    detect_collection_capabilities,
)
from contx.daemon.launch_agent import (
    LAUNCH_AGENT_LABEL,
    PROCESSOR_LAUNCH_AGENT_LABEL,
    launch_agent_program_arguments,
    processor_launch_agent_program_arguments,
    render_launch_agent,
    render_processor_launch_agent,
)
from contx.daemon.launch_agent_lifecycle import (
    LaunchAgentSpec,
    LaunchAgentStatus,
    ManifestState,
    UserLaunchAgentLifecycle,
    resolve_launch_agents_directory,
)
from contx.db import current_database_revision, head_database_revision
from contx.errors import ConfigurationError, ContxError
from contx.memory_store import OptMemAdapter, resolve_optmem_executable
from contx.memory_store.optmem import OPTMEM_EXECUTABLE_ENV
from contx.model_provider import LocalModelRuntimeStatus, OllamaModelProvider
from contx.settings import (
    RUNTIME_ROOT_ENV,
    AppSettings,
    RuntimePaths,
    load_settings,
    rollback_config_mutation,
    set_background_collection_features,
)

COLLECTOR_EXECUTABLE_NAME = "contx-collector"
PROCESSOR_EXECUTABLE_NAME = "contx-processor"


class ModelReadinessProbe(Protocol):
    def status(self) -> LocalModelRuntimeStatus: ...


class OptMemReadinessProbe(Protocol):
    def validate_executable(self) -> None: ...


class LaunchAgentLifecyclePort(Protocol):
    def inspect(self) -> tuple[LaunchAgentStatus, ...]: ...

    def stage(self) -> tuple[str, ...]: ...

    def load(self) -> tuple[str, ...]: ...

    def unload(self) -> tuple[str, ...]: ...

    def unload_staged(self, labels: tuple[str, ...]) -> tuple[str, ...]: ...

    def remove(self) -> tuple[str, ...]: ...

    def remove_staged(self, labels: tuple[str, ...]) -> tuple[str, ...]: ...


CapabilityDetector = Callable[
    [object],
    tuple[CollectionCapability, ...],
]


@dataclass(frozen=True, slots=True)
class BackgroundCollectionStatus:
    """Content-free readiness and lifecycle status."""

    background_enabled: bool
    window_titles_enabled: bool
    screenshots_enabled: bool
    collection_paused: bool
    agents: tuple[LaunchAgentStatus, ...]
    problems: tuple[str, ...]

    @property
    def ready_to_activate(self) -> bool:
        return not self.problems

    @property
    def active(self) -> bool:
        expected_labels = {LAUNCH_AGENT_LABEL, PROCESSOR_LAUNCH_AGENT_LABEL}
        observed_labels = tuple(agent.label for agent in self.agents)
        return (
            self.background_enabled
            and self.window_titles_enabled
            and self.screenshots_enabled
            and not self.collection_paused
            and len(observed_labels) == len(expected_labels)
            and set(observed_labels) == expected_labels
            and all(
                agent.loaded and agent.manifest_state is ManifestState.MATCHING
                for agent in self.agents
            )
        )


@dataclass(frozen=True, slots=True)
class BackgroundActivationResult:
    created_manifests: tuple[str, ...]
    loaded_agents: tuple[str, ...]
    status: BackgroundCollectionStatus


@dataclass(frozen=True, slots=True)
class BackgroundDeactivationResult:
    unloaded_agents: tuple[str, ...]
    removed_manifests: tuple[str, ...]
    status: BackgroundCollectionStatus


class BackgroundCollectionLifecycleService:
    """Converge configuration and both user jobs without partial silent success."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        agents: LaunchAgentLifecyclePort,
        model: ModelReadinessProbe,
        optmem: OptMemReadinessProbe,
        pause_collection: Callable[[], object],
        resume_collection: Callable[[], object],
        collection_is_paused: Callable[[], bool],
        platform: str = sys.platform,
        capability_detector: Callable[..., tuple[CollectionCapability, ...]] = (
            detect_collection_capabilities
        ),
        schema_is_current: Callable[[], bool] | None = None,
    ) -> None:
        self._paths = paths
        self._agents = agents
        self._model = model
        self._optmem = optmem
        self._pause_collection = pause_collection
        self._resume_collection = resume_collection
        self._collection_is_paused = collection_is_paused
        self._platform = platform
        self._capability_detector = capability_detector
        self._schema_is_current = schema_is_current or self._default_schema_is_current

    def inspect(self) -> BackgroundCollectionStatus:
        """Run content-free preflights without prompting or changing state."""
        settings = load_settings(self._paths, environ={})
        desired_collection = settings.collection.model_copy(
            update={
                "background_collection_enabled": True,
                "window_titles_enabled": True,
                "screenshots_enabled": True,
            }
        )
        problems: list[str] = []
        if self._platform != "darwin":
            problems.append("macos_required")
        capabilities = self._capability_detector(
            desired_collection,
            platform=self._platform,
        )
        problems.extend(
            f"{capability.name}:{capability.reason_code or capability.status.value}"
            for capability in capabilities
            if capability.status is not CapabilityStatus.AVAILABLE
        )
        model_status = self._model.status()
        if not model_status.runtime_available:
            problems.append(model_status.reason_code or "model_runtime_unavailable")
        elif not model_status.model_available:
            problems.append(model_status.reason_code or "model_not_installed")
        try:
            self._optmem.validate_executable()
        except ContxError:
            problems.append("optmem_unavailable")
        try:
            if not self._schema_is_current():
                problems.append("database_schema_not_current")
        except ContxError:
            problems.append("database_schema_unavailable")
        agent_statuses = self._agents.inspect()
        problems.extend(
            f"{status.label}:manifest_{status.manifest_state.value}"
            for status in agent_statuses
            if status.manifest_state
            not in {ManifestState.MISSING, ManifestState.MATCHING}
        )
        problems.extend(
            f"{status.label}:loaded_without_managed_manifest"
            for status in agent_statuses
            if status.loaded and status.manifest_state is not ManifestState.MATCHING
        )
        expected_labels = {LAUNCH_AGENT_LABEL, PROCESSOR_LAUNCH_AGENT_LABEL}
        observed_labels = tuple(status.label for status in agent_statuses)
        if len(observed_labels) != len(expected_labels) or set(observed_labels) != (
            expected_labels
        ):
            problems.append("launch_agent_status_incomplete")
        try:
            collection_paused = self._collection_is_paused()
        except ContxError:
            collection_paused = True
            problems.append("collection_control_unavailable")
        return _status(
            settings,
            agent_statuses,
            tuple(dict.fromkeys(problems)),
            collection_paused=collection_paused,
        )

    def activate(self) -> BackgroundActivationResult:
        """Enable exact v0 features and load both jobs, rolling back on failure."""
        readiness = self.inspect()
        if not readiness.ready_to_activate:
            raise ConfigurationError(
                "Background activation preflight failed: "
                + ", ".join(readiness.problems)
            )
        if readiness.active:
            return BackgroundActivationResult(
                created_manifests=(),
                loaded_agents=(),
                status=readiness,
            )
        created: tuple[str, ...] = ()
        loaded: tuple[str, ...] = ()
        mutation = None
        paused_for_activation = False
        resume_attempted = False
        try:
            if not readiness.collection_paused:
                self._pause_collection()
                paused_for_activation = True
            created = self._agents.stage()
            mutation = set_background_collection_features(self._paths, enabled=True)
            loaded = self._agents.load()
            resume_attempted = True
            self._resume_collection()
            status = self._inspect_activation_state()
            if not status.active:
                raise ConfigurationError(
                    "Background activation did not reach a consistent active state"
                )
        except BaseException as error:
            rollback_failures: list[BaseException] = []
            if resume_attempted:
                try:
                    self._pause_collection()
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            if loaded:
                try:
                    self._agents.unload_staged(loaded)
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            if mutation is not None:
                try:
                    rollback_config_mutation(mutation)
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            if created:
                try:
                    self._agents.remove_staged(created)
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            if paused_for_activation and not rollback_failures:
                try:
                    self._resume_collection()
                except BaseException as rollback_error:
                    rollback_failures.append(rollback_error)
            if rollback_failures:
                raise ConfigurationError(
                    "Background activation failed and rollback is incomplete"
                ) from error
            raise
        return BackgroundActivationResult(
            created_manifests=created,
            loaded_agents=loaded,
            status=status,
        )

    def deactivate(self) -> BackgroundDeactivationResult:
        """Pause first, then stop both jobs, disable features, and remove plists."""
        self._pause_collection()
        unloaded = self._agents.unload()
        set_background_collection_features(self._paths, enabled=False)
        removed = self._agents.remove()
        status = self._inspect_activation_state()
        if status.active or any(agent.loaded for agent in status.agents):
            raise ConfigurationError(
                "Background deactivation did not reach a stopped state"
            )
        return BackgroundDeactivationResult(
            unloaded_agents=unloaded,
            removed_manifests=removed,
            status=status,
        )

    def _inspect_activation_state(self) -> BackgroundCollectionStatus:
        settings = load_settings(self._paths, environ={})
        return _status(
            settings,
            self._agents.inspect(),
            (),
            collection_paused=self._collection_is_paused(),
        )

    def _default_schema_is_current(self) -> bool:
        return (
            current_database_revision(self._paths.database_file)
            == head_database_revision()
        )


def build_background_collection_lifecycle(
    *,
    paths: RuntimePaths,
    settings: AppSettings,
    pause_collection: Callable[[], object],
    resume_collection: Callable[[], object],
    collection_is_paused: Callable[[], bool],
    environ: Mapping[str, str] | None = None,
    executable_directory: Path | None = None,
    home: Path | None = None,
    user_id: int | None = None,
) -> BackgroundCollectionLifecycleService:
    """Compose real preflights and exact user LaunchAgent manifests."""
    environment = os.environ if environ is None else environ
    executable_root = (
        Path(sys.executable).parent
        if executable_directory is None
        else executable_directory
    )
    collector_executable = executable_root / COLLECTOR_EXECUTABLE_NAME
    processor_executable = executable_root / PROCESSOR_EXECUTABLE_NAME
    launch_agents_directory = resolve_launch_agents_directory(home=home)
    optmem_executable = resolve_optmem_executable(environment, home=home)
    manifest_environment = _managed_launch_agent_environment(
        environment,
        optmem_executable=optmem_executable,
    )
    specs = (
        LaunchAgentSpec(
            label=LAUNCH_AGENT_LABEL,
            path=launch_agents_directory / f"{LAUNCH_AGENT_LABEL}.plist",
            manifest=render_launch_agent(
                program_arguments=launch_agent_program_arguments(collector_executable),
                paths=paths,
                environment_variables=manifest_environment,
            ),
        ),
        LaunchAgentSpec(
            label=PROCESSOR_LAUNCH_AGENT_LABEL,
            path=(launch_agents_directory / f"{PROCESSOR_LAUNCH_AGENT_LABEL}.plist"),
            manifest=render_processor_launch_agent(
                program_arguments=processor_launch_agent_program_arguments(
                    processor_executable
                ),
                paths=paths,
                interval_seconds=settings.processing.model_interval_seconds,
                environment_variables=manifest_environment,
            ),
        ),
    )
    model = OllamaModelProvider(
        model=settings.model.model_name,
        endpoint=settings.model.endpoint,
        timeout_seconds=min(2.0, settings.model.timeout_seconds),
        keep_alive=settings.model.keep_alive,
        context_tokens=settings.model.context_tokens,
        max_output_tokens=settings.model.max_output_tokens,
        max_image_bytes=settings.model.max_image_mb * 1024 * 1024,
        max_response_bytes=settings.model.max_response_kb * 1024,
    )
    optmem = OptMemAdapter(
        executable=optmem_executable,
        memory_directory=paths.memory,
        wake_budget_bytes=settings.memory.wake_budget_bytes,
    )
    return BackgroundCollectionLifecycleService(
        paths=paths,
        agents=UserLaunchAgentLifecycle(
            specs=specs,
            launch_agents_directory=launch_agents_directory,
            user_id=os.getuid() if user_id is None else user_id,
        ),
        model=model,
        optmem=optmem,
        pause_collection=pause_collection,
        resume_collection=resume_collection,
        collection_is_paused=collection_is_paused,
    )


def _managed_launch_agent_environment(
    environment: Mapping[str, str],
    *,
    optmem_executable: Path,
) -> dict[str, str]:
    managed = {OPTMEM_EXECUTABLE_ENV: str(optmem_executable.resolve(strict=False))}
    runtime_root = environment.get(RUNTIME_ROOT_ENV)
    if runtime_root is not None:
        path = Path(runtime_root).expanduser()
        if not path.is_absolute():
            raise ConfigurationError(f"{RUNTIME_ROOT_ENV} must be an absolute path")
        managed[RUNTIME_ROOT_ENV] = str(path.resolve(strict=False))
    return managed


def _status(
    settings: AppSettings,
    agents: tuple[LaunchAgentStatus, ...],
    problems: tuple[str, ...],
    *,
    collection_paused: bool,
) -> BackgroundCollectionStatus:
    collection = settings.collection
    return BackgroundCollectionStatus(
        background_enabled=collection.background_collection_enabled,
        window_titles_enabled=collection.window_titles_enabled,
        screenshots_enabled=collection.screenshots_enabled,
        collection_paused=collection_paused,
        agents=agents,
        problems=problems,
    )
