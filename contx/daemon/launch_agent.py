"""Pure launchd manifest rendering; installation remains an explicit action."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

from contx.errors import ConfigurationError
from contx.settings import RuntimePaths

LAUNCH_AGENT_LABEL = "io.contx.collector"
PROCESSOR_LAUNCH_AGENT_LABEL = "io.contx.processor"


def launch_agent_program_arguments(collector_executable: Path) -> tuple[str, ...]:
    """Build the stable argv for the dedicated collector entrypoint."""
    executable = _validate_executable(collector_executable)
    return (str(executable),)


def processor_launch_agent_program_arguments(
    processor_executable: Path,
) -> tuple[str, ...]:
    """Build the stable argv for the dedicated periodic processor entrypoint."""
    executable = _validate_executable(processor_executable)
    return (str(executable),)


def render_launch_agent(
    *,
    program_arguments: tuple[str, ...],
    paths: RuntimePaths,
    environment_variables: dict[str, str] | None = None,
) -> bytes:
    """Render a private user-agent plist without writing or loading it."""
    if not program_arguments:
        raise ConfigurationError("LaunchAgent program arguments must not be empty")
    if not paths.logs.is_absolute():
        raise ConfigurationError("LaunchAgent log directory must be absolute")
    executable = _validate_executable(Path(program_arguments[0]))
    arguments = (str(executable), *program_arguments[1:])
    if any(not argument or "\x00" in argument for argument in arguments):
        raise ConfigurationError("LaunchAgent arguments contain an invalid value")
    manifest = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": list(arguments),
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
    _add_environment(manifest, environment_variables)
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False)


def render_processor_launch_agent(
    *,
    program_arguments: tuple[str, ...],
    paths: RuntimePaths,
    interval_seconds: int,
    environment_variables: dict[str, str] | None = None,
) -> bytes:
    """Render a periodic private processor job without writing or loading it."""
    if not 60 <= interval_seconds <= 3600:
        raise ConfigurationError(
            "Processor LaunchAgent interval must be between 60 and 3600 seconds"
        )
    if not program_arguments:
        raise ConfigurationError(
            "Processor LaunchAgent program arguments must not be empty"
        )
    if not paths.logs.is_absolute():
        raise ConfigurationError("LaunchAgent log directory must be absolute")
    executable = _validate_executable(Path(program_arguments[0]))
    arguments = (str(executable), *program_arguments[1:])
    if any(not argument or "\x00" in argument for argument in arguments):
        raise ConfigurationError("LaunchAgent arguments contain an invalid value")
    manifest = {
        "Label": PROCESSOR_LAUNCH_AGENT_LABEL,
        "ProgramArguments": list(arguments),
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "ProcessType": "Background",
        "ThrottleInterval": 60,
        "ExitTimeOut": 10,
        "Umask": "077",
        "StandardOutPath": str(paths.logs / "processor.stdout.log"),
        "StandardErrorPath": str(paths.logs / "processor.stderr.log"),
    }
    _add_environment(manifest, environment_variables)
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False)


def _add_environment(
    manifest: dict[str, object],
    environment_variables: dict[str, str] | None,
) -> None:
    if not environment_variables:
        return
    if any(
        not key or "\x00" in key or "=" in key or not value or "\x00" in value
        for key, value in environment_variables.items()
    ):
        raise ConfigurationError("LaunchAgent environment contains an invalid value")
    manifest["EnvironmentVariables"] = dict(sorted(environment_variables.items()))


def _validate_executable(path: Path) -> Path:
    if not path.is_absolute():
        raise ConfigurationError("LaunchAgent executable path must be absolute")
    if path.is_symlink():
        raise ConfigurationError("LaunchAgent executable must not be a symlink")
    try:
        if not path.is_file():
            raise ConfigurationError("LaunchAgent executable is not a regular file")
    except OSError as error:
        raise ConfigurationError("Cannot inspect LaunchAgent executable") from error
    if not os.access(path, os.X_OK):
        raise ConfigurationError("LaunchAgent program is not executable")
    return path
