"""OptMem adapter process, recovery, and privacy boundary tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from contx.errors import MemoryStoreError, MemoryStoreUnavailableError
from contx.memory_store import MemoryCompressionRequest
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
    (root / "SUMMARIES.json").write_text("{}")
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
elif command == "config":
    (root / "CONFIG_ARGS.json").write_text(json.dumps(sys.argv[2:]))
    print("Configured.")
elif command == "nap":
    summaries = json.loads((root / "SUMMARIES.json").read_text())
    if len(entries) < 2 or "0-1" in summaries:
        print("Nothing left to compress.")
    elif len(sys.argv) == 2:
        print(
            "Compress memories #0-1 into one line of at most 280 bytes.\n\n"
            f"  #0 2026-08-02 {entries[0]}\n"
            f"  #1 2026-08-02 {entries[1]}\n"
            'Run: memo nap 0-1 "<your line>"'
        )
    else:
        summaries["0-1"] = sys.argv[3]
        (root / "SUMMARIES.json").write_text(json.dumps(summaries))
        print("0-1 saved.")
elif command == "forget":
    summaries = json.loads((root / "SUMMARIES.json").read_text())
    summaries.pop(sys.argv[2], None)
    (root / "SUMMARIES.json").write_text(json.dumps(summaries))
    print(f"Forgot 1 summary, from {sys.argv[2]} up. Run: memo nap")
else:
    raise SystemExit("unsupported command")
"""


def test_executable_preflight_does_not_initialize_or_run_optmem(
    tmp_path: Path,
) -> None:
    executable, checksum = _write_executable(tmp_path, FAKE_OPTMEM)
    memory = tmp_path / "memory"
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=memory,
        expected_sha256=checksum,
    )

    adapter.validate_executable()

    assert not memory.exists()


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
    assert wake.content == "#0 2026-08-02 Resume CONTX safely.\n"
    assert wake.technical_status == "You are awake.\n"
    assert json.loads((memory / "LOG.json").read_text()) == ["Resume CONTX safely."]
    assert json.loads((memory / "CONFIG_ARGS.json").read_text()) == [
        "WAKE_LINES=59",
        "PART_CHARS=18976",
        "PART_LINES=59",
    ]
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
        "import os\nos.write(1, b'x' * 8192)\n",
    )
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256=checksum,
        output_bytes=4096,
        wake_budget_bytes=4096,
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
elif command == "config":
    print("Configured.")
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
    assert wake.content == ""
    assert wake.technical_status is not None
    assert wake.technical_status.startswith("Cannot wake:")


def test_compression_is_bounded_and_invalid_summary_can_be_rebuilt(
    tmp_path: Path,
) -> None:
    executable, checksum = _write_executable(tmp_path, FAKE_OPTMEM)
    memory = tmp_path / "memory"
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=memory,
        expected_sha256=checksum,
    )
    compressor = RecordingCompressor("Atlas now targets local execution only.")

    adapter.initialize()
    adapter.append("Atlas previously targeted staging.", idempotency_key="first")
    adapter.append("Atlas now targets local execution only.", idempotency_key="second")
    maintained = adapter.maintain(compressor, max_compressions=1)

    assert maintained.complete
    assert maintained.completed_compressions == 1
    assert compressor.requests[0].block == "0-1"
    assert compressor.requests[0].max_bytes == 280
    assert json.loads((memory / "SUMMARIES.json").read_text()) == {
        "0-1": "Atlas now targets local execution only."
    }

    adapter.invalidate_summary("0-1")
    pending = adapter.maintain(compressor, max_compressions=1)

    assert pending.complete
    assert pending.completed_compressions == 1
    assert len(compressor.requests) == 2


def test_reads_and_maintenance_share_the_cross_process_adapter_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable, checksum = _write_executable(tmp_path, FAKE_OPTMEM)
    adapter = OptMemAdapter(
        executable=executable,
        memory_directory=tmp_path / "memory",
        expected_sha256=checksum,
    )
    adapter.initialize()
    adapter.append("First memory.", idempotency_key="first")
    adapter.append("Second memory.", idempotency_key="second")
    lock_events: list[str] = []

    @contextmanager
    def tracked_adapter_lock() -> Iterator[None]:
        lock_events.append("enter")
        try:
            yield
        finally:
            lock_events.append("exit")

    monkeypatch.setattr(adapter, "_adapter_lock", tracked_adapter_lock)

    adapter.wake()
    adapter.recall("memory")
    adapter.zoom("0-1")
    adapter.maintain(RecordingCompressor("Combined memory."), max_compressions=1)
    adapter.invalidate_summary("0-1")

    assert lock_events == ["enter", "exit"] * 5


class RecordingCompressor:
    def __init__(self, summary: str) -> None:
        self._summary = summary
        self.requests: list[MemoryCompressionRequest] = []

    def compress(self, request: MemoryCompressionRequest) -> str:
        self.requests.append(request)
        return self._summary


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
