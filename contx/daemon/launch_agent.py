"""Pure launchd manifest rendering; installation remains an explicit action."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

from contx.errors import ConfigurationError
from contx.settings import RuntimePaths

LAUNCH_AGENT_LABEL = "io.contx.collector"
PROCESSOR_LAUNCH_AGENT_LABEL = "io.contx.processor"


def launch_agent_program_arguments(python_executable: Path) -> tuple[str, ...]:
    """Build the stable argv for the internal module entrypoint."""
    executable = _validate_executable(python_executable)
    return (str(executable), "-m", "contx.daemon.entrypoint")


def processor_launch_agent_program_arguments(
    python_executable: Path,
) -> tuple[str, ...]:
    """Build the stable argv for the periodic local processor."""
    executable = _validate_executable(python_executable)
    return (str(executable), "-m", "contx.processing.entrypoint")


def render_launch_agent(
    *,
    program_arguments: tuple[str, ...],
    paths: RuntimePaths,
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
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False)


def render_processor_launch_agent(
    *,
    program_arguments: tuple[str, ...],
    paths: RuntimePaths,
    interval_seconds: int,
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
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False)


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
