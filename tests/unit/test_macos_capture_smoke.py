"""Content-free helpers for the host-dependent focused-window smoke."""

import stat
import struct
from pathlib import Path

import pytest

from contx.errors import CollectorUnavailableError
from contx.evaluation.macos_capture_smoke import (
    png_dimensions,
    validate_private_output_path,
    write_private_png,
)

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + struct.pack(">I", 13)
    + b"IHDR"
    + struct.pack(">II", 640, 400)
    + b"\x08\x06\x00\x00\x00"
)


def test_png_dimensions_read_only_the_ihdr_boundary() -> None:
    assert png_dimensions(PNG) == (640, 400)


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"not a png" + (b"\x00" * 30),
        b"\x89PNG\r\n\x1a\n" + (b"\x00" * 16),
    ),
)
def test_invalid_png_header_is_actionable(payload: bytes) -> None:
    with pytest.raises(CollectorUnavailableError, match="PNG"):
        png_dimensions(payload)


def test_private_writer_is_exclusive_and_uses_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "focused-window.png"

    written = write_private_png(target, PNG)

    assert written == target
    assert written.read_bytes() == PNG
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="already exists"):
        write_private_png(target, PNG)


def test_private_writer_requires_an_absolute_png_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        write_private_png(Path("relative.png"), PNG)
    with pytest.raises(ValueError, match="end in .png"):
        write_private_png(tmp_path / "capture.jpg", PNG)


def test_output_validation_rejects_existing_target_before_capture(
    tmp_path: Path,
) -> None:
    target = tmp_path / "existing.png"
    target.write_bytes(b"existing")

    with pytest.raises(ValueError, match="already exists"):
        validate_private_output_path(target)
