"""Resolve and create CONTX-owned runtime paths."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from contx.errors import ConfigurationError

RUNTIME_ROOT_ENV = "CONTX_RUNTIME_ROOT"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

DEFAULT_CONFIG = """config_version = 1

[collection]
raw_retention_hours = 48
window_titles_enabled = false
background_collection_enabled = false
retain_excluded_activity = false
poll_interval_seconds = 1.0
segment_max_duration_seconds = 60
idle_threshold_seconds = 300
purge_interval_seconds = 900
screenshots_enabled = false
screenshot_min_interval_seconds = 15
screenshot_max_interval_seconds = 120
raw_disk_budget_mb = 5120

[model]
provider = "ollama"
endpoint = "http://127.0.0.1:11434"
model_name = "qwen3-vl:4b-instruct-q4_K_M"
timeout_seconds = 120.0
keep_alive = "5m"
context_tokens = 8192
max_image_mb = 20
max_response_kb = 1024
"""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Every filesystem path owned by one CONTX runtime."""

    application_support: Path
    caches: Path
    logs: Path

    @property
    def config_file(self) -> Path:
        return self.application_support / "config.toml"

    @property
    def database_file(self) -> Path:
        return self.application_support / "contx.db"

    @property
    def memory(self) -> Path:
        return self.application_support / "memory"

    @property
    def exports(self) -> Path:
        return self.application_support / "exports"

    @property
    def raw(self) -> Path:
        return self.caches / "raw"

    @property
    def processing(self) -> Path:
        return self.caches / "processing"

    @property
    def daemon_lock(self) -> Path:
        return self.processing / "collector.lock"

    @property
    def owned_directories(self) -> tuple[Path, ...]:
        return (
            self.application_support,
            self.caches,
            self.logs,
            self.memory,
            self.exports,
            self.raw,
            self.processing,
        )


def resolve_runtime_paths(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> RuntimePaths:
    """Resolve production paths or one explicit isolated override."""
    environment = os.environ if environ is None else environ
    override_value = environment.get(RUNTIME_ROOT_ENV)

    if override_value is not None:
        if not override_value.strip():
            raise ConfigurationError(f"{RUNTIME_ROOT_ENV} must not be empty")
        override = Path(override_value).expanduser()
        if not override.is_absolute():
            raise ConfigurationError(f"{RUNTIME_ROOT_ENV} must be an absolute path")
        root = override.resolve(strict=False)
        return RuntimePaths(
            application_support=root / "application-support",
            caches=root / "caches",
            logs=root / "logs",
        )

    resolved_home = (Path.home() if home is None else home).expanduser()
    if not resolved_home.is_absolute():
        raise ConfigurationError("The resolved home directory must be absolute")
    resolved_home = resolved_home.resolve(strict=False)
    library = resolved_home / "Library"
    return RuntimePaths(
        application_support=library / "Application Support" / "CONTX",
        caches=library / "Caches" / "CONTX",
        logs=library / "Logs" / "CONTX",
    )


def initialize_runtime_paths(paths: RuntimePaths) -> None:
    """Create known runtime paths and a private default configuration."""
    for directory in paths.owned_directories:
        _ensure_private_directory(directory)
    _write_private_file_if_missing(paths.config_file, DEFAULT_CONFIG.encode("utf-8"))
    _validate_private_regular_file(paths.config_file)


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigurationError(f"Cannot create CONTX directory: {path}") from error

    if path.is_symlink() or not path.is_dir():
        raise ConfigurationError(f"CONTX path is not a safe directory: {path}")
    try:
        path.chmod(PRIVATE_DIRECTORY_MODE)
    except OSError as error:
        raise ConfigurationError(f"Cannot protect CONTX directory: {path}") from error


def _write_private_file_if_missing(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        return

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            PRIVATE_FILE_MODE,
        )
        remaining = memoryview(content)
        while remaining:
            written = os.write(descriptor, remaining)
            if written == 0:
                raise OSError("zero-byte write while creating state file")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        with suppress(FileExistsError):
            os.link(temporary, path)
    except OSError as error:
        raise ConfigurationError(f"Cannot create CONTX state file: {path}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _validate_private_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise ConfigurationError(f"CONTX state file must not be a symlink: {path}")
    try:
        file_status = path.stat()
    except OSError as error:
        raise ConfigurationError(f"Cannot inspect CONTX state file: {path}") from error
    if not stat.S_ISREG(file_status.st_mode):
        raise ConfigurationError(f"CONTX state path is not a regular file: {path}")
    try:
        path.chmod(PRIVATE_FILE_MODE)
    except OSError as error:
        raise ConfigurationError(f"Cannot protect CONTX state file: {path}") from error
