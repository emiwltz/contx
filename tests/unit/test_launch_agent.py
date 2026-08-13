"""LaunchAgent manifest rendering without installation or process startup."""

import plistlib
from pathlib import Path

import pytest

from contx.daemon import (
    LAUNCH_AGENT_LABEL,
    PROCESSOR_LAUNCH_AGENT_LABEL,
    launch_agent_program_arguments,
    processor_launch_agent_program_arguments,
    render_launch_agent,
    render_processor_launch_agent,
)
from contx.errors import ConfigurationError
from contx.settings import RuntimePaths


def test_manifest_is_deterministic_private_and_bound_to_aqua(
    tmp_path: Path,
) -> None:
    executable = _executable(tmp_path)
    paths = _paths(tmp_path)
    arguments = launch_agent_program_arguments(executable)

    first = render_launch_agent(program_arguments=arguments, paths=paths)
    second = render_launch_agent(program_arguments=arguments, paths=paths)
    manifest = plistlib.loads(first)

    assert first == second
    assert manifest == {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(executable),
            "-m",
            "contx.daemon.entrypoint",
        ],
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Interactive",
        "ThrottleInterval": 30,
        "ExitTimeOut": 10,
        "Umask": "077",
        "StandardOutPath": str(paths.logs / "collector.stdout.log"),
        "StandardErrorPath": str(paths.logs / "collector.stderr.log"),
    }


def test_manifest_rejects_relative_missing_and_symlinked_executables(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(ConfigurationError, match="must be absolute"):
        render_launch_agent(program_arguments=("python",), paths=paths)
    with pytest.raises(ConfigurationError, match="not a regular file"):
        render_launch_agent(
            program_arguments=(str(tmp_path / "missing-python"),),
            paths=paths,
        )
    executable = _executable(tmp_path)
    link = tmp_path / "python-link"
    link.symlink_to(executable)
    with pytest.raises(ConfigurationError, match="must not be a symlink"):
        render_launch_agent(program_arguments=(str(link),), paths=paths)
    non_executable = tmp_path / "non-executable"
    non_executable.write_bytes(b"fixture")
    non_executable.chmod(0o600)
    with pytest.raises(ConfigurationError, match="not executable"):
        render_launch_agent(
            program_arguments=(str(non_executable),),
            paths=paths,
        )


def test_periodic_processor_manifest_is_bounded_and_not_kept_alive(
    tmp_path: Path,
) -> None:
    executable = _executable(tmp_path)
    paths = _paths(tmp_path)
    arguments = processor_launch_agent_program_arguments(executable)

    manifest = plistlib.loads(
        render_processor_launch_agent(
            program_arguments=arguments,
            paths=paths,
            interval_seconds=900,
        )
    )

    assert manifest == {
        "Label": PROCESSOR_LAUNCH_AGENT_LABEL,
        "ProgramArguments": [
            str(executable),
            "-m",
            "contx.processing.entrypoint",
        ],
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "StartInterval": 900,
        "ProcessType": "Background",
        "ThrottleInterval": 60,
        "ExitTimeOut": 10,
        "Umask": "077",
        "StandardOutPath": str(paths.logs / "processor.stdout.log"),
        "StandardErrorPath": str(paths.logs / "processor.stderr.log"),
    }


def test_processor_manifest_rejects_an_unbounded_interval(tmp_path: Path) -> None:
    executable = _executable(tmp_path)

    with pytest.raises(ConfigurationError, match="between 60 and 3600"):
        render_processor_launch_agent(
            program_arguments=processor_launch_agent_program_arguments(executable),
            paths=_paths(tmp_path),
            interval_seconds=59,
        )


def _executable(root: Path) -> Path:
    executable = root / "python"
    executable.write_bytes(b"synthetic executable fixture")
    executable.chmod(0o700)
    return executable


def _paths(root: Path) -> RuntimePaths:
    return RuntimePaths(
        application_support=root / "application-support",
        caches=root / "caches",
        logs=root / "logs",
    )
