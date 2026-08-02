"""Internal process entrypoint for a supervised CONTX collection daemon."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol

from contx.daemon.factory import (
    ConfiguredMacOSCollectionDaemon,
    build_macos_collection_daemon,
)
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


def main() -> None:
    raise SystemExit(run_collection_daemon())


if __name__ == "__main__":
    main()
