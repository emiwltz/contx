"""Transactional and ownership-safe user LaunchAgent lifecycle."""

from __future__ import annotations

import stat
from collections.abc import Sequence
from pathlib import Path

import pytest

import contx.daemon.launch_agent_lifecycle as lifecycle_module
from contx.daemon.launch_agent_lifecycle import (
    LaunchAgentSpec,
    ManifestState,
    UserLaunchAgentLifecycle,
    resolve_launch_agents_directory,
)
from contx.errors import ConfigurationError

LABELS = ("io.contx.collector", "io.contx.processor")
USER_ID = 501


class FakeLaunchctl:
    def __init__(self) -> None:
        self.loaded: set[str] = set()
        self.calls: list[tuple[str, ...]] = []
        self.bootstrap_return_code = 0
        self.bootstrap_load_count: int | None = None
        self.bootstrap_interrupt = False
        self.stuck_bootout: set[str] = set()

    def run(self, arguments: Sequence[str]) -> int:
        call = tuple(arguments)
        self.calls.append(call)
        operation = call[0]
        if operation == "print":
            return 0 if call[1].rsplit("/", 1)[-1] in self.loaded else 113
        if operation == "bootstrap":
            labels = tuple(Path(path).stem for path in call[2:])
            limit = (
                len(labels)
                if self.bootstrap_load_count is None
                else self.bootstrap_load_count
            )
            self.loaded.update(labels[:limit])
            if self.bootstrap_interrupt:
                raise KeyboardInterrupt("synthetic interrupted bootstrap")
            return self.bootstrap_return_code
        if operation == "bootout":
            label = call[1].rsplit("/", 1)[-1]
            if label not in self.stuck_bootout:
                self.loaded.discard(label)
            return 0
        raise AssertionError(f"unexpected launchctl operation: {operation}")


def test_inspection_is_content_free_and_does_not_create_files(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)

    statuses = lifecycle.inspect()

    assert [
        (status.label, status.manifest_state, status.loaded) for status in statuses
    ] == [
        (LABELS[0], ManifestState.MISSING, False),
        (LABELS[1], ManifestState.MISSING, False),
    ]
    assert not specs[0].path.parent.exists()


def test_stage_is_private_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)

    assert lifecycle.stage() == LABELS
    assert lifecycle.stage() == ()
    for spec in specs:
        assert spec.path.read_bytes() == spec.manifest
        assert stat.S_IMODE(spec.path.stat().st_mode) == 0o600

    specs[0].path.write_bytes(b"user-owned conflicting manifest")
    specs[0].path.chmod(0o600)
    with pytest.raises(ConfigurationError, match="conflicting"):
        lifecycle.stage()
    assert specs[0].path.read_bytes() == b"user-owned conflicting manifest"


def test_stage_rolls_back_earlier_creation_if_later_target_is_unsafe(
    tmp_path: Path,
) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)
    specs[0].path.parent.mkdir(mode=0o700)
    external = tmp_path / "external.plist"
    external.write_bytes(b"external")
    specs[1].path.symlink_to(external)

    with pytest.raises(ConfigurationError, match="unsafe"):
        lifecycle.stage()

    assert not specs[0].path.exists()
    assert specs[1].path.is_symlink()
    assert external.read_bytes() == b"external"


def test_stage_rolls_back_an_interrupted_partial_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)
    write_manifest = lifecycle_module._write_new_manifest
    calls = 0

    def interrupt_second(spec: LaunchAgentSpec) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("synthetic interrupted staging")
        write_manifest(spec)

    monkeypatch.setattr(lifecycle_module, "_write_new_manifest", interrupt_second)

    with pytest.raises(KeyboardInterrupt, match="interrupted staging"):
        lifecycle.stage()

    assert all(not spec.path.exists() for spec in specs)


def test_loads_both_jobs_together_and_is_idempotent(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, _specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()

    assert lifecycle.load() == LABELS
    assert lifecycle.load() == ()
    assert runner.loaded == set(LABELS)
    assert (
        "bootstrap",
        f"gui/{USER_ID}",
        str(_specs[0].path),
        str(_specs[1].path),
    ) in runner.calls


def test_partial_bootstrap_failure_rolls_back_every_newly_loaded_job(
    tmp_path: Path,
) -> None:
    runner = FakeLaunchctl()
    runner.bootstrap_return_code = 5
    runner.bootstrap_load_count = 1
    lifecycle, _specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()

    with pytest.raises(ConfigurationError, match="could not be loaded together"):
        lifecycle.load()

    assert runner.loaded == set()
    assert ("bootout", f"gui/{USER_ID}/{LABELS[0]}") in runner.calls


def test_partial_bootstrap_reports_a_job_that_cannot_be_rolled_back(
    tmp_path: Path,
) -> None:
    runner = FakeLaunchctl()
    runner.bootstrap_return_code = 5
    runner.bootstrap_load_count = 1
    runner.stuck_bootout.add(LABELS[0])
    lifecycle, _specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()

    with pytest.raises(ConfigurationError, match="rollback is incomplete"):
        lifecycle.load()

    assert runner.loaded == {LABELS[0]}


def test_interrupted_bootstrap_rolls_back_every_observed_loaded_job(
    tmp_path: Path,
) -> None:
    runner = FakeLaunchctl()
    runner.bootstrap_load_count = 1
    runner.bootstrap_interrupt = True
    lifecycle, _specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()

    with pytest.raises(KeyboardInterrupt, match="interrupted bootstrap"):
        lifecycle.load()

    assert runner.loaded == set()


def test_unload_then_remove_deletes_only_exact_manifests(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()
    lifecycle.load()

    assert lifecycle.unload() == LABELS
    assert lifecycle.remove() == LABELS
    assert all(not spec.path.exists() for spec in specs)


def test_selected_rollback_removes_only_newly_staged_manifest(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()

    assert lifecycle.remove_staged((LABELS[1],)) == (LABELS[1],)
    assert specs[0].path.exists()
    assert not specs[1].path.exists()


def test_selected_rollback_unloads_only_newly_loaded_job(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, _specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()
    lifecycle.load()

    assert lifecycle.unload_staged((LABELS[1],)) == (LABELS[1],)
    assert runner.loaded == {LABELS[0]}


def test_unload_refuses_a_loaded_job_after_manifest_changes(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()
    lifecycle.load()
    specs[0].path.write_bytes(b"modified")
    specs[0].path.chmod(0o600)

    with pytest.raises(ConfigurationError, match="unmanaged"):
        lifecycle.unload()

    assert runner.loaded == set(LABELS)


def test_unload_reports_a_service_that_remains_loaded(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, _specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()
    lifecycle.load()
    runner.stuck_bootout.add(LABELS[0])

    with pytest.raises(ConfigurationError, match="did not stop"):
        lifecycle.unload()

    assert LABELS[0] in runner.loaded


def test_remove_refuses_modified_or_loaded_manifests(tmp_path: Path) -> None:
    runner = FakeLaunchctl()
    lifecycle, specs = _lifecycle(tmp_path, runner)
    lifecycle.stage()
    runner.loaded.add(LABELS[0])
    with pytest.raises(ConfigurationError, match="loaded"):
        lifecycle.remove()

    runner.loaded.clear()
    specs[1].path.write_bytes(b"modified")
    specs[1].path.chmod(0o600)
    with pytest.raises(ConfigurationError, match="modified"):
        lifecycle.remove()
    assert all(spec.path.exists() for spec in specs)


def test_resolve_launch_agents_directory_is_user_scoped(tmp_path: Path) -> None:
    assert resolve_launch_agents_directory(home=tmp_path) == (
        tmp_path / "Library" / "LaunchAgents"
    )


def _lifecycle(
    root: Path,
    runner: FakeLaunchctl,
) -> tuple[UserLaunchAgentLifecycle, tuple[LaunchAgentSpec, ...]]:
    directory = root / "LaunchAgents"
    specs = tuple(
        LaunchAgentSpec(
            label=label,
            path=directory / f"{label}.plist",
            manifest=f"synthetic {label}".encode(),
        )
        for label in LABELS
    )
    return (
        UserLaunchAgentLifecycle(
            specs=specs,
            launch_agents_directory=directory,
            user_id=USER_ID,
            runner=runner,
        ),
        specs,
    )
