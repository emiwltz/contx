"""Explicit full-data deletion with narrow runtime-root validation."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine

from contx.errors import ConfigurationError, PipelineError
from contx.settings import RuntimePaths


@dataclass(frozen=True, slots=True)
class DataDeletionResult:
    removed_stores: tuple[str, ...]


class DataDeletionService:
    """Remove only the three configured CONTX data roots after safety checks."""

    def __init__(self, *, paths: RuntimePaths, engine: Engine | None = None) -> None:
        self._paths = paths
        self._engine = engine

    def delete_all(self) -> DataDeletionResult:
        from contx.daemon.lease import probe_daemon_lease

        roots = (
            ("caches", self._paths.caches),
            ("logs", self._paths.logs),
            ("application_support", self._paths.application_support),
        )
        _validate_roots(tuple(path for _label, path in roots))
        if self._paths.processing.is_dir():
            daemon = probe_daemon_lease(self._paths.daemon_lock)
            if daemon.running:
                raise PipelineError(
                    "Stop the CONTX collection daemon before deleting all data"
                )
        for _label, root in roots:
            _validate_tree(root)
        if self._engine is not None:
            self._engine.dispose()

        removed: list[str] = []
        for label, root in roots:
            if not root.exists():
                continue
            try:
                shutil.rmtree(root)
            except OSError as error:
                raise ConfigurationError(
                    f"Cannot remove the CONTX {label} store"
                ) from error
            removed.append(label)
        return DataDeletionResult(removed_stores=tuple(removed))


def _validate_roots(roots: tuple[Path, ...]) -> None:
    if len(set(roots)) != len(roots):
        raise ConfigurationError("CONTX deletion roots must be distinct")
    for root in roots:
        if not root.is_absolute() or len(root.parts) < 4:
            raise ConfigurationError("CONTX deletion root is too broad")
        if root == Path.home() or root == Path.home().parent:
            raise ConfigurationError("CONTX deletion root is unsafe")
    for root in roots:
        if any(root != other and root in other.parents for other in roots):
            raise ConfigurationError("CONTX deletion roots must not be nested")


def _validate_tree(root: Path) -> None:
    if not root.exists():
        return
    if root.is_symlink() or not root.is_dir():
        raise ConfigurationError("CONTX deletion root is not a safe directory")
    try:
        if root.resolve(strict=True) != root:
            raise ConfigurationError("CONTX deletion root uses a symbolic link")
        pending = [root]
        while pending:
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        raise ConfigurationError(
                            "CONTX data contains a symbolic link; deletion refused"
                        )
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif not entry.is_file(follow_symlinks=False):
                        raise ConfigurationError(
                            "CONTX data contains an unsupported filesystem entry"
                        )
    except ConfigurationError:
        raise
    except OSError as error:
        raise ConfigurationError("Cannot inspect CONTX data before deletion") from error
