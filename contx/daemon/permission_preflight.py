"""Content-free access checks for the signed host's exact collector child."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from importlib import import_module
from types import ModuleType


def run_permission_preflight(
    *,
    platform: str = sys.platform,
    module_loader: Callable[[str], ModuleType] = import_module,
    write_output: Callable[[str], object] | None = None,
    write_error: Callable[[str], object] | None = None,
) -> int:
    """Check access without prompting, observing activity or opening runtime data."""
    output = write_output or sys.stdout.write
    error = write_error or sys.stderr.write
    try:
        if platform != "darwin":
            raise RuntimeError("Unsupported platform")
        quartz = module_loader("Quartz")
        accessibility = module_loader("ApplicationServices")
        screen = quartz.CGPreflightScreenCaptureAccess()
        titles = accessibility.AXIsProcessTrusted()
        if type(screen) is not bool or type(titles) is not bool:
            raise TypeError("Unexpected permission result")
        output(
            json.dumps(
                {
                    "schema_version": 1,
                    "screen_recording": screen,
                    "accessibility": titles,
                    "permissions_requested": False,
                    "content_read": False,
                }
            )
            + "\n"
        )
    except Exception:
        error("CONTX permission verification failed without collecting content.\n")
        return 2
    return 0
