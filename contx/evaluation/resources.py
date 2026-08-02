"""Content-free process and disk sampling for an authorized running pilot."""

from __future__ import annotations

import ctypes
import math
import os
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from contx.daemon import probe_daemon_lease
from contx.evaluation.models import ResourcePhase, ResourceSample
from contx.evaluation.pilot import PilotEvaluationError, PilotWorkspace
from contx.models import Clock
from contx.settings import RuntimePaths

PROC_PIDTASKINFO = 4
SAMPLE_INTERVAL_SECONDS = 0.2


@dataclass(frozen=True, slots=True)
class ProcessResourceMetrics:
    cpu_percent: float
    rss_bytes: int


ProcessProbe = Callable[[int], ProcessResourceMetrics]


@dataclass(frozen=True, slots=True)
class _ProcessSnapshot:
    cpu_ticks: int
    rss_bytes: int


class _ProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("context_switches", ctypes.c_int32),
        ("thread_count", ctypes.c_int32),
        ("running_thread_count", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


class _MachTimebaseInfo(ctypes.Structure):
    _fields_ = [
        ("numerator", ctypes.c_uint32),
        ("denominator", ctypes.c_uint32),
    ]


class PilotResourceSampler:
    """Measure named local processes without reading arguments or user content."""

    def __init__(
        self,
        *,
        workspace: PilotWorkspace,
        paths: RuntimePaths,
        clock: Clock,
        process_probe: ProcessProbe | None = None,
    ) -> None:
        self._workspace = workspace
        self._paths = paths
        self._clock = clock
        self._process_probe = process_probe or probe_process_resources

    def capture(
        self,
        *,
        phase: ResourcePhase,
        detection_latency_ms: float | None = None,
        local_model_pid: int | None = None,
        web_pid: int | None = None,
    ) -> ResourceSample:
        lease = probe_daemon_lease(self._paths.daemon_lock)
        if not lease.running or lease.pid is None:
            raise PilotEvaluationError(
                "Resource sampling requires the authorized collector to be running"
            )
        pids = [lease.pid]
        if local_model_pid is not None:
            pids.append(_validate_pid(local_model_pid))
        if web_pid is not None:
            pids.append(_validate_pid(web_pid))
        if len(set(pids)) != len(pids):
            raise PilotEvaluationError("Resource process identifiers must be distinct")

        collector = self._process_probe(lease.pid)
        local_model = (
            ProcessResourceMetrics(cpu_percent=0, rss_bytes=0)
            if local_model_pid is None
            else self._process_probe(local_model_pid)
        )
        web = (
            ProcessResourceMetrics(cpu_percent=0, rss_bytes=0)
            if web_pid is None
            else self._process_probe(web_pid)
        )
        sample = ResourceSample(
            captured_at=self._clock.now(),
            phase=phase,
            collector_cpu_percent=collector.cpu_percent,
            collector_rss_bytes=collector.rss_bytes,
            local_model_cpu_percent=local_model.cpu_percent,
            local_model_rss_bytes=local_model.rss_bytes,
            web_cpu_percent=web.cpu_percent,
            web_rss_bytes=web.rss_bytes,
            detection_latency_ms=detection_latency_ms,
            raw_disk_bytes=directory_usage_bytes(self._paths.raw),
            durable_disk_bytes=directory_usage_bytes(
                self._paths.application_support
            ),
        )
        self._workspace.append_resource_sample(sample)
        return sample


def probe_process_resources(pid: int) -> ProcessResourceMetrics:
    """Read CPU time and RSS from one PID through the native macOS libproc API."""
    validated_pid = _validate_pid(pid)
    started_at = time.monotonic_ns()
    started = _read_process_snapshot(validated_pid)
    time.sleep(SAMPLE_INTERVAL_SECONDS)
    ended = _read_process_snapshot(validated_pid)
    ended_at = time.monotonic_ns()
    elapsed_ns = ended_at - started_at
    tick_delta = ended.cpu_ticks - started.cpu_ticks
    if elapsed_ns <= 0 or tick_delta < 0:
        raise PilotEvaluationError("The local process resource result is invalid")
    numerator, denominator = _mach_timebase()
    cpu_ns = tick_delta * numerator / denominator
    cpu_percent = cpu_ns / elapsed_ns * 100
    if (
        not math.isfinite(cpu_percent)
        or not 0 <= cpu_percent <= 1600
        or ended.rss_bytes <= 0
    ):
        raise PilotEvaluationError("The local process resource result is unsafe")
    return ProcessResourceMetrics(
        cpu_percent=cpu_percent,
        rss_bytes=ended.rss_bytes,
    )


def _read_process_snapshot(pid: int) -> _ProcessSnapshot:
    if sys.platform != "darwin":
        raise PilotEvaluationError("The native process probe requires macOS")
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        function = library.proc_pidinfo
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        function.restype = ctypes.c_int
        info = _ProcTaskInfo()
        written = function(
            pid,
            PROC_PIDTASKINFO,
            0,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
    except (AttributeError, OSError) as error:
        raise PilotEvaluationError(
            "The native process resource probe is unavailable"
        ) from error
    if written != ctypes.sizeof(info):
        raise PilotEvaluationError("The selected local process is unavailable")
    return _ProcessSnapshot(
        cpu_ticks=info.total_user + info.total_system,
        rss_bytes=info.resident_size,
    )


@lru_cache(maxsize=1)
def _mach_timebase() -> tuple[int, int]:
    try:
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        function = library.mach_timebase_info
        function.argtypes = [ctypes.POINTER(_MachTimebaseInfo)]
        function.restype = ctypes.c_int
        info = _MachTimebaseInfo()
        result = function(ctypes.byref(info))
    except (AttributeError, OSError) as error:
        raise PilotEvaluationError("Cannot resolve the macOS CPU timebase") from error
    if result != 0 or info.numerator == 0 or info.denominator == 0:
        raise PilotEvaluationError("The macOS CPU timebase is invalid")
    return info.numerator, info.denominator


def directory_usage_bytes(root: Path) -> int:
    """Measure regular files below one exact root without following symlinks."""
    if root.is_symlink():
        raise PilotEvaluationError("Resource data directory must not be a symlink")
    if not root.exists():
        return 0
    if not root.is_dir():
        raise PilotEvaluationError("Resource data path is not a directory")
    try:
        if root.resolve(strict=True) != root:
            raise PilotEvaluationError("Resource data directory must not use symlinks")
    except OSError as error:
        raise PilotEvaluationError("Cannot inspect resource data directory") from error

    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as error:
            raise PilotEvaluationError(
                "Cannot inspect resource data directory"
            ) from error
        for entry in entries:
            try:
                if entry.is_symlink():
                    raise PilotEvaluationError(
                        "Resource data directory contains a symlink"
                    )
                entry_status = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise PilotEvaluationError(
                    "Cannot inspect resource data entry"
                ) from error
            if stat.S_ISDIR(entry_status.st_mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(entry_status.st_mode):
                total += entry_status.st_size
            else:
                raise PilotEvaluationError(
                    "Resource data directory contains an unsupported entry"
                )
    return total


def _validate_pid(pid: int) -> int:
    if isinstance(pid, bool) or pid <= 0 or pid > 2**31 - 1:
        raise PilotEvaluationError("Process identifier must be a positive integer")
    return pid
