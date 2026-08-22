"""Native-host background composition remains explicit and fail closed."""

from pathlib import Path

import pytest

from contx.application.background_collection import (
    NATIVE_HOST_RELATIVE_EXECUTABLE,
    resolve_native_host_executable,
)
from contx.errors import ConfigurationError


def test_native_host_has_one_stable_user_application_path(tmp_path: Path) -> None:
    assert resolve_native_host_executable(home=tmp_path) == (
        tmp_path / "Applications/CONTX.app/Contents/MacOS/CONTX"
    )
    assert (
        Path("Applications/CONTX.app/Contents/MacOS/CONTX")
        == NATIVE_HOST_RELATIVE_EXECUTABLE
    )


def test_native_host_rejects_a_relative_home() -> None:
    with pytest.raises(ConfigurationError, match="must be absolute"):
        resolve_native_host_executable(home=Path("relative-home"))
