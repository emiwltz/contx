"""Run the explicitly confirmed synthetic focused-window smoke on macOS."""

from __future__ import annotations

import argparse
from pathlib import Path

from contx.errors import ContxError
from contx.evaluation.macos_capture_smoke import run_focused_window_smoke

CONFIRMATION = "CAPTURE CONTX SYNTHETIC WINDOW"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Create harmless AppKit windows, prove a focus-race rejection, "
            "and capture only the authorized synthetic window. This command "
            "never requests macOS permissions."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm", required=True)
    arguments = parser.parse_args()
    if arguments.confirm != CONFIRMATION:
        parser.error(f"--confirm must be exactly: {CONFIRMATION}")
    try:
        result = run_focused_window_smoke(arguments.output)
    except (ContxError, OSError, ValueError) as error:
        parser.exit(2, f"focused-window smoke failed: {error}\n")
    print("focused-window smoke: passed")
    print(f"focus-race rejection: {result.focus_race_reason}")
    print(f"capture dimensions: {result.width}x{result.height}")
    print(f"capture sha256: {result.content_hash}")
    print(f"private artifact: {result.output_path}")
    print("permissions requested: no")
    print("background collection changed: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
