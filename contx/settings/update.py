"""Narrow atomic updates for explicitly authorized collection activation."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from contx.errors import ConfigurationError
from contx.settings.models import AppSettings, load_settings
from contx.settings.paths import PRIVATE_FILE_MODE, RuntimePaths

_COLLECTION_KEYS = (
    "window_titles_enabled",
    "background_collection_enabled",
    "screenshots_enabled",
)
_TABLE_PATTERN = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")


@dataclass(frozen=True, slots=True)
class ConfigMutation:
    """Exact before/after bytes used for conditional rollback."""

    path: Path
    previous: bytes
    updated: bytes


def set_background_collection_features(
    paths: RuntimePaths,
    *,
    enabled: bool,
) -> ConfigMutation:
    """Atomically set the three accepted v0 live-collection feature flags."""
    load_settings(paths, environ={})
    previous = _read_private_config(paths.config_file)
    updated = _replace_collection_flags(previous, enabled=enabled)
    _validate_updated_config(updated)
    mutation = ConfigMutation(
        path=paths.config_file,
        previous=previous,
        updated=updated,
    )
    if updated != previous:
        _replace_if_unchanged(
            paths.config_file,
            expected=previous,
            replacement=updated,
        )
    return mutation


def rollback_config_mutation(mutation: ConfigMutation) -> None:
    """Restore exact prior bytes only if no later edit replaced our update."""
    if mutation.previous == mutation.updated:
        return
    _replace_if_unchanged(
        mutation.path,
        expected=mutation.updated,
        replacement=mutation.previous,
    )


def _replace_collection_flags(payload: bytes, *, enabled: bool) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ConfigurationError("CONTX configuration is not valid UTF-8") from error
    replacement = "true" if enabled else "false"
    counts = {key: 0 for key in _COLLECTION_KEYS}
    current_table: str | None = None
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]
        table = _TABLE_PATTERN.fullmatch(body)
        if table is not None:
            current_table = table.group(1).strip()
            output.append(line)
            continue
        if current_table != "collection":
            output.append(line)
            continue
        changed = body
        for key in _COLLECTION_KEYS:
            assignment = re.fullmatch(
                rf"(\s*{re.escape(key)}\s*=\s*)(?:true|false)(\s*(?:#.*)?)",
                body,
            )
            if assignment is None:
                continue
            counts[key] += 1
            changed = f"{assignment.group(1)}{replacement}{assignment.group(2)}"
            break
        output.append(changed + ending)
    missing_or_duplicate = tuple(key for key, count in counts.items() if count != 1)
    if missing_or_duplicate:
        raise ConfigurationError(
            "CONTX collection flags are missing or duplicated: "
            + ", ".join(missing_or_duplicate)
        )
    return "".join(output).encode("utf-8")


def _validate_updated_config(payload: bytes) -> None:
    try:
        parsed = tomllib.loads(payload.decode("utf-8"))
        AppSettings.model_validate(parsed)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as error:
        raise ConfigurationError("Updated CONTX configuration is invalid") from error


def _read_private_config(path: Path) -> bytes:
    try:
        file_status = path.lstat()
    except OSError as error:
        raise ConfigurationError("Cannot inspect CONTX configuration") from error
    if (
        not stat.S_ISREG(file_status.st_mode)
        or stat.S_IMODE(file_status.st_mode) & 0o077
    ):
        raise ConfigurationError("CONTX configuration is not a private regular file")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ConfigurationError("Cannot read CONTX configuration") from error


def _replace_if_unchanged(
    path: Path,
    *,
    expected: bytes,
    replacement: bytes,
) -> None:
    current = _read_private_config(path)
    if current != expected:
        raise ConfigurationError(
            "CONTX configuration changed concurrently; refusing to overwrite it"
        )
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            PRIVATE_FILE_MODE,
        )
        _write_all(descriptor, replacement)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError as error:
        raise ConfigurationError(
            "Cannot update CONTX configuration atomically"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    if _read_private_config(path) != replacement:
        raise ConfigurationError("CONTX configuration update could not be verified")


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written == 0:
            raise OSError("zero-byte CONTX configuration write")
        remaining = remaining[written:]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
