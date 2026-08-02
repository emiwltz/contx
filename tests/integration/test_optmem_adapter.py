"""OptMem adapter process, recovery, and privacy boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from contx.errors import MemoryStoreError, MemoryStoreUnavailableError
from contx.memory_store.optmem import (
    IDEMPOTENCY_FILE,
    OPTMEM_EXECUTABLE_ENV,
    OptMemAdapter,
    resolve_optmem_executable,
)

FAKE_OPTMEM = r"""
import json
import os
import pathlib
import re
import sys

root = pathlib.Path(os.environ["MEMORY_DIR"])
command = sys.argv[1]
if "CONTX_TEST_SECRET" in os.environ:
    raise SystemExit("secret leaked")
if command == "init":
    root.mkdir(parents=True, exist_ok=True)
    (root / "TREE").mkdir(exist_ok=True)
    (root / "LOG.json").touch(exist_ok=True)
    (root / "config").touch(exist_ok=True)
    print("Created memory.")
    raise SystemExit(0)

entries = json.loads((root / "LOG.json").read_text() or "[]")
if command == "note":
    entries.append(sys.argv[2])
    (root / "LOG.json").write_text(json.dumps(entries))
    print(f"Saved as #{len(entries) - 1}.")
elif command == "recall":
    expression = re.compile(sys.argv[2], re.IGNORECASE)
    matches = [
        f"#{index} 2026-08-02 {text}"
        for index, text in enumerate(entries)
        if expression.search(f"#{index} 2026-08-02 {text}")
    ]
    if not matches:
        print("No match.")
    else:
        print("\n".join(matches + [f"{len(matches)} match."]))
elif command == "wake":
    for index, text in enumerate(entries):
        print(f"#{index} 2026-08-02 {text}")
    print("You are awake.")
elif command == "zoom":
    index = int(sys.argv[2].split("-", 1)[0])
    print(f"#{index} 2026-08-02 {entries[index]}")
else:
    raise SystemExit("unsupported command")
"""


def test_append_replay_and_crash_recovery_are_idempotent(tmp_path: Path) -> None:
    executable, checksum = _write_executable(tmp_path, FAKE_OPTMEM)
    memory = tmp_path / "memory"
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=memory,
        expected_sha256=checksum,
        environ={
            "HOME": str(tmp_path),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CONTX_TEST_SECRET": "must-not-cross-boundary",
        },
    )

    adapter.initialize()
    with pytest.raises(MemoryStoreError, match="280-byte limit"):
        adapter.append("x" * 281, idempotency_key="too-large")
    first = adapter.append("Resume CONTX safely.", idempotency_key="candidate-1")
    replay = adapter.append("Resume CONTX safely.", idempotency_key="candidate-1")
    (memory / IDEMPOTENCY_FILE).unlink()
    recovered = adapter.append("Resume CONTX safely.", idempotency_key="candidate-1")
    wake = adapter.wake()

    assert first.backend_id == replay.backend_id == recovered.backend_id == "0"
    assert wake.complete
    assert wake.content == "#0 2026-08-02 Resume CONTX safely.\nYou are awake.\n"
    assert json.loads((memory / "LOG.json").read_text()) == ["Resume CONTX safely."]
    for path in memory.rglob("*"):
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_modified_executable_is_rejected_before_execution(tmp_path: Path) -> None:
    executable, _checksum = _write_executable(tmp_path, FAKE_OPTMEM)
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256="0" * 64,
    )

    with pytest.raises(MemoryStoreUnavailableError, match="reviewed snapshot"):
        adapter.initialize()

    assert not (tmp_path / "memory").exists()


def test_subprocess_timeout_is_bounded(tmp_path: Path) -> None:
    executable, checksum = _write_executable(
        tmp_path,
        "import time\ntime.sleep(2)\n",
    )
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256=checksum,
        timeout_seconds=0.05,
    )

    with pytest.raises(MemoryStoreError, match="timeout"):
        adapter.initialize()


def test_invalid_utf8_output_is_rejected(tmp_path: Path) -> None:
    executable, checksum = _write_executable(
        tmp_path,
        "import os\nos.write(1, b'\\xff')\n",
    )
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256=checksum,
    )

    with pytest.raises(MemoryStoreError, match="valid UTF-8"):
        adapter.initialize()


def test_output_limit_stops_the_process(tmp_path: Path) -> None:
    executable, checksum = _write_executable(
        tmp_path,
        "import os\nos.write(1, b'x' * 4096)\n",
    )
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256=checksum,
        output_bytes=128,
    )

    with pytest.raises(MemoryStoreError, match="safe output limit"):
        adapter.initialize()


def test_pending_maintenance_is_explicit_and_wake_is_not_complete(
    tmp_path: Path,
) -> None:
    executable, checksum = _write_executable(
        tmp_path,
        r"""
import os
import pathlib
import sys

root = pathlib.Path(os.environ["MEMORY_DIR"])
command = sys.argv[1]
if command == "init":
    root.mkdir(parents=True, exist_ok=True)
    (root / "TREE").mkdir(exist_ok=True)
    (root / "config").touch(exist_ok=True)
    (root / "LOG").touch(exist_ok=True)
elif command == "recall":
    print("No match.")
elif command == "note":
    print("Saved as #0.\n\nRun: memo nap 0-1 \"<your line>\"")
elif command == "wake":
    print("Cannot wake: compression pending.\nRun: memo nap 0-1 \"<your line>\"")
    raise SystemExit(1)
""",
    )
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256=checksum,
    )

    adapter.initialize()
    appended = adapter.append("Durable work.", idempotency_key="candidate")
    wake = adapter.wake()

    assert appended.maintenance_required
    assert wake.maintenance_required
    assert not wake.complete
    assert wake.content.startswith("Cannot wake:")


def test_reviewed_optmem_snapshot_end_to_end_when_available(tmp_path: Path) -> None:
    executable = Path(__file__).resolve().parents[2] / "optmem" / "memo"
    if not executable.is_file():
        pytest.skip("ignored OptMem development snapshot is not available")
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
    )

    adapter.initialize()
    first = adapter.append("Resume CONTX safely.", idempotency_key="candidate")
    replay = adapter.append("Resume CONTX safely.", idempotency_key="candidate")
    wake = adapter.wake()

    assert first.backend_id == replay.backend_id == "0"
    assert wake.complete
    assert "Resume CONTX safely." in wake.content


def test_executable_override_must_be_absolute(tmp_path: Path) -> None:
    with pytest.raises(MemoryStoreUnavailableError, match="absolute"):
        resolve_optmem_executable({OPTMEM_EXECUTABLE_ENV: "relative/memo"})

    absolute = tmp_path / "memo"
    assert resolve_optmem_executable({OPTMEM_EXECUTABLE_ENV: str(absolute)}) == absolute


def _write_executable(tmp_path: Path, body: str) -> tuple[Path, str]:
    executable = tmp_path / f"memo-{len(tuple(tmp_path.iterdir()))}"
    payload = f"#!{sys.executable}\n{body.lstrip()}".encode()
    executable.write_bytes(payload)
    executable.chmod(0o700)
    return executable, hashlib.sha256(payload).hexdigest()
