# Dependency review

This record documents direct dependency decisions. Exact resolved versions and
artifact hashes are authoritative in `uv.lock`.

## Runtime foundation

| Dependency | Locked version | License | Cost and platform notes | Removal strategy |
|---|---:|---|---|---|
| Pydantic | 2.13.4 | MIT | Actively maintained; typed boundary validation. `pydantic-core` provides a macOS arm64 wheel. | Replace boundary models and validation explicitly before removing it. Domain rules must remain independent of serialization details. |
| PyObjC Cocoa | 12.2.1 | MIT | Maintained macOS bridge for AppKit. Adds `pyobjc-core`; the Python 3.12 Apple Silicon wheel is used. No all-framework metapackage is installed. | The `Collector` boundary permits replacement by a small native Swift helper if packaging, permissions, or reliability justify it. |
| SQLAlchemy | 2.0.51 | MIT | Actively maintained; explicit transactions and SQLite persistence. Adds `greenlet`, with a macOS arm64 wheel. | Repository contracts isolate persistence. Replacing it requires rewriting repositories and migration integration, not domain models. |
| Alembic | 1.18.5 | MIT | Maintained with SQLAlchemy; adds Mako and MarkupSafe for migration generation. Runtime upgrades remain offline after installation. | Replace only with a tested migration runner preserving revision history, retry behavior, and recovery guarantees. |
| Typer | 0.27.0 | MIT | Actively maintained; adds Rich and Shellingham for CLI behavior. Pure Python on the target platform. | Keep command services independent of Typer so another CLI layer can call the same application contracts. |

These libraries solve standardized or failure-prone boundaries. CONTX does not
add FastAPI, OCR, model, or web dependencies until the milestone that has an
immediate consumer for them.

## Development and build tools

| Dependency | Locked version | License | Scope |
|---|---:|---|---|
| pytest | 9.1.1 | MIT | Deterministic unit and integration tests. |
| pytest-cov | 7.1.0 | MIT | Coverage diagnostics; no coverage-percentage completion gate. |
| Ruff | 0.16.1 | MIT | Formatting and static linting. |
| mypy | 1.20.2 | MIT | Strict type checking for the CONTX package. |
| uv / uv_build | 0.11.7 tool and build-backend line | Apache-2.0 or MIT | Python 3.12 selection, locked resolution, isolated builds, and console-package installation. |

Development tools are not runtime dependencies. The build backend is bounded
to the current `0.11` compatibility line and can be replaced through standard
Python packaging metadata if necessary.

## Review result

The selected direct packages are actively maintained, compatible with Python
3.12 and the Apple Silicon target, and use permissive licenses compatible with
CONTX. The resolved environment contains 30 installed packages including
development tooling. `uv` verified a source distribution and wheel containing
only the CONTX Python package and the project license.
