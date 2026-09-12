"""Internal process entrypoint for a supervised CONTX collection daemon."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import Protocol

from contx.daemon.factory import (
    ConfiguredMacOSCollectionDaemon,
    build_macos_collection_daemon,
)
from contx.daemon.permission_preflight import run_permission_preflight
from contx.errors import ContxError


class DaemonBuilder(Protocol):
    def __call__(self) -> ConfiguredMacOSCollectionDaemon: ...


def run_collection_daemon(
    *,
    build: DaemonBuilder = build_macos_collection_daemon,
    write_error: Callable[[str], object] | None = None,
) -> int:
    """Run one supervised process and return a sanitized launchd exit status."""
    error_sink = write_error or _write_standard_error
    daemon: ConfiguredMacOSCollectionDaemon | None = None
    try:
        daemon = build()
        daemon.run()
    except ContxError as error:
        error_sink(f"CONTX collector stopped: {error}\n")
        return 2
    except Exception:
        error_sink("CONTX collector stopped after an unexpected local failure.\n")
        return 3
    finally:
        if daemon is not None:
            daemon.close()
    return 0


def _write_standard_error(message: str) -> int:
    return sys.stderr.write(message)


def run_entrypoint(arguments: Sequence[str]) -> int:
    """Reject unknown modes before constructing a collection runtime."""
    if list(arguments) == ["--permission-preflight"]:
        return run_permission_preflight()
    if arguments:
        _write_standard_error("CONTX collector received invalid arguments.\n")
        return 64
    return run_collection_daemon()


def main() -> None:
    raise SystemExit(run_entrypoint(sys.argv[1:]))


if __name__ == "__main__":
    main()
