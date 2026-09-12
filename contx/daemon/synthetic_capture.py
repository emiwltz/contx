"""One explicit synthetic capture, without initializing collection or processing."""

from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import asdict
from pathlib import Path

from contx.evaluation.macos_capture_smoke import run_focused_window_smoke
from contx.macos_app.control import NativeHostControlService, NativeHostState
from contx.models import SystemClock
from contx.settings import resolve_runtime_paths


def run_synthetic_capture(output: Path) -> int:
    """Refuse capture unless dispatched by the host with collection disabled."""
    try:
        if os.environ.get("CONTX_NATIVE_HOST") != "1":
            raise ValueError("Native host required")
        status = NativeHostControlService(
            paths=resolve_runtime_paths(), clock=SystemClock()
        ).status()
        if (
            status.state is not NativeHostState.DISABLED
            or status.background_enabled
            or not status.collection_paused
            or status.collector_running
        ):
            raise ValueError("Disabled collection required")
        result = run_focused_window_smoke(output)
        print(json.dumps({"schema_version": 1, **asdict(result)}, default=str))
        return 0
    except Exception as error:
        # Never expose a real foreground title or native exception in the UI.
        frames = traceback.extract_tb(error.__traceback__)
        sites = [
            f"{Path(frame.filename).stem}:{frame.lineno}"
            for frame in frames
            if "/contx/" in frame.filename
        ]
        print(json.dumps({"schema_version": 1, "failure_site": sites[-1]}))
        sys.stderr.write("CONTX synthetic capture failed.\n")
        return 2
