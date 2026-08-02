"""Typed configuration tests."""

from pathlib import Path

import pytest

from contx.errors import ConfigurationError
from contx.settings import (
    RUNTIME_ROOT_ENV,
    RuntimePaths,
    initialize_runtime_paths,
    load_settings,
    resolve_runtime_paths,
)
from contx.settings.models import RAW_RETENTION_ENV, WINDOW_TITLES_ENV


def _initialized_paths(tmp_path: Path) -> RuntimePaths:
    paths = resolve_runtime_paths({RUNTIME_ROOT_ENV: str(tmp_path)})
    initialize_runtime_paths(paths)
    return paths


def test_defaults_are_safe(tmp_path: Path) -> None:
    settings = load_settings(_initialized_paths(tmp_path), {})

    assert settings.collection.raw_retention_hours == 48
    assert settings.collection.window_titles_enabled is False
    assert settings.collection.background_collection_enabled is False
    assert settings.collection.retain_excluded_activity is False
    assert settings.collection.segment_max_duration_seconds == 60
    assert settings.collection.purge_interval_seconds == 900
    assert settings.collection.screenshots_enabled is False
    assert settings.collection.screenshot_min_interval_seconds == 15
    assert settings.collection.screenshot_max_interval_seconds == 120
    assert settings.collection.raw_disk_budget_mb == 5120
    assert settings.model.provider == "ollama"
    assert settings.model.endpoint == "http://127.0.0.1:11434"
    assert settings.model.model_name == "qwen3-vl:4b-instruct-q4_K_M"
    assert settings.model.timeout_seconds == 120.0
    assert settings.model.context_tokens == 8192


def test_environment_overrides_config(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    paths.config_file.write_text(
        """config_version = 1
[collection]
raw_retention_hours = 36
window_titles_enabled = false
background_collection_enabled = false
""",
        encoding="utf-8",
    )

    settings = load_settings(
        paths,
        {RAW_RETENTION_ENV: "12", WINDOW_TITLES_ENV: "true"},
    )

    assert settings.collection.raw_retention_hours == 12
    assert settings.collection.window_titles_enabled is True


def test_invalid_value_does_not_echo_private_input(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    private_value = "private-invalid-value"

    with pytest.raises(ConfigurationError) as caught:
        load_settings(paths, {WINDOW_TITLES_ENV: private_value})

    assert private_value not in str(caught.value)


def test_retention_cannot_exceed_48_hours(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)

    with pytest.raises(ConfigurationError, match="less than or equal to 48"):
        load_settings(paths, {RAW_RETENTION_ENV: "49"})


def test_collection_controls_can_be_configured_without_enabling_screenshots(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    paths.config_file.write_text(
        """config_version = 1
[collection]
raw_retention_hours = 48
window_titles_enabled = false
background_collection_enabled = true
""",
        encoding="utf-8",
    )

    settings = load_settings(paths, {})

    assert settings.collection.background_collection_enabled
    assert not settings.collection.screenshots_enabled


def test_broad_config_permissions_are_rejected(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    paths.config_file.chmod(0o644)

    with pytest.raises(ConfigurationError, match="permissions are too broad"):
        load_settings(paths, {})


def test_screenshot_maximum_interval_cannot_be_below_minimum(
    tmp_path: Path,
) -> None:
    paths = _initialized_paths(tmp_path)
    paths.config_file.write_text(
        """config_version = 1
[collection]
screenshot_min_interval_seconds = 60
screenshot_max_interval_seconds = 30
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="maximum interval"):
        load_settings(paths, {})


@pytest.mark.parametrize(
    "endpoint",
    (
        "http://localhost:11434",
        "http://192.168.1.10:11434",
        "https://127.0.0.1:11434",
    ),
)
def test_model_endpoint_cannot_leave_literal_loopback(
    tmp_path: Path,
    endpoint: str,
) -> None:
    paths = _initialized_paths(tmp_path)
    paths.config_file.write_text(
        f'''config_version = 1
[model]
endpoint = "{endpoint}"
''',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="literal loopback"):
        load_settings(paths, {})


def test_local_model_resource_limits_are_validated(tmp_path: Path) -> None:
    paths = _initialized_paths(tmp_path)
    paths.config_file.write_text(
        """config_version = 1
[model]
context_tokens = 1024
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="greater than or equal to 2048"):
        load_settings(paths, {})
