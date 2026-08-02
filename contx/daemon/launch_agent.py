"""Pure launchd manifest rendering; installation remains an explicit action."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

from contx.errors import ConfigurationError
from contx.settings import RuntimePaths

LAUNCH_AGENT_LABEL = "io.contx.collector"


def launch_agent_program_arguments(python_executable: Path) -> tuple[str, ...]:
    """Build the stable argv for the internal module entrypoint."""
    executable = _validate_executable(python_executable)
    return (str(executable), "-m", "contx.daemon.entrypoint")


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
