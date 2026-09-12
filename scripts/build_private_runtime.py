"""Build a private final-path runtime without installing or activating CONTX."""

from __future__ import annotations

import argparse
from pathlib import Path

from contx.macos_app.runtime_release import build_private_runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("root", "manifest", "repository", "python-home", "optmem", "uv"):
        parser.add_argument(f"--{flag}", required=True, type=Path)
    args = parser.parse_args()
    result = build_private_runtime(
        args.root,
        args.manifest,
        repository=args.repository,
        python_home=args.python_home,
        optmem=args.optmem,
        uv=args.uv,
    )
    print(f"Verified private release: {result.root}")
    print(f"Source commit: {result.sourceCommit}")
    print(f"Inventoried nodes: {len(result.files)}")
    print("Application launched: no; collection started: no")


if __name__ == "__main__":
    main()
