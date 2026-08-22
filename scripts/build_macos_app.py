"""Build a signed development CONTX.app without launching or installing it."""

from __future__ import annotations

import argparse
from pathlib import Path

from contx.macos_app.bundle import (
    BUNDLE_IDENTIFIER,
    MacOSAppBuildError,
    build_contx_app_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build and verify the signed CONTX macOS host. The command never "
            "launches, registers, installs, or activates the application."
        )
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--collector-executable", required=True, type=Path)
    parser.add_argument("--control-executable", required=True, type=Path)
    parser.add_argument("--signing-identity", required=True)
    parser.add_argument("--screen-recording-usage-description", required=True)
    arguments = parser.parse_args()
    try:
        bundle = build_contx_app_bundle(
            arguments.output,
            collector_executable=arguments.collector_executable,
            control_executable=arguments.control_executable,
            signing_identity=arguments.signing_identity,
            screen_recording_usage_description=(
                arguments.screen_recording_usage_description
            ),
        )
    except (MacOSAppBuildError, OSError, ValueError) as error:
        parser.exit(2, f"CONTX app build failed: {error}\n")
    print(f"CONTX app bundle: {bundle}")
    print(f"bundle identifier: {BUNDLE_IDENTIFIER}")
    print("application launched: no")
    print("permissions requested: no")
    print("collection started: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
