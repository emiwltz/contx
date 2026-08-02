# Dependency review

This record documents direct dependency decisions. Exact resolved versions and
artifact hashes are authoritative in `uv.lock`.

## Runtime foundation

| Dependency | Locked version | License | Cost and platform notes | Removal strategy |
|---|---:|---|---|---|
| Pydantic | 2.13.4 | MIT | Actively maintained; typed boundary validation. `pydantic-core` provides a macOS arm64 wheel. | Replace boundary models and validation explicitly before removing it. Domain rules must remain independent of serialization details. |
| PyObjC ApplicationServices | 12.2.1 | MIT | Narrow maintained bridge for HIServices Accessibility APIs. It provides the authorized focused-window title path; the Python 3.12 Universal 2 wheel is used and adds the matching PyObjC CoreText package transitively. | Remove it if titles leave v0 scope or a native helper owns Accessibility behind the activity-sampler contract. |
| PyObjC Cocoa | 12.2.1 | MIT | Maintained macOS bridge for AppKit. Adds `pyobjc-core`; the Python 3.12 Apple Silicon wheel is used. No all-framework metapackage is installed. | The `Collector` boundary permits replacement by a small native Swift helper if packaging, permissions, or reliability justify it. |
| PyObjC Quartz | 12.2.1 | MIT | Narrow maintained bridge for public CoreGraphics idle and screen-capture APIs. The Python 3.12 Universal 2 wheel is used; the all-framework metapackage remains excluded. | Remove it if a future native helper owns idle and screen-capture probes behind the accepted activity contracts. |
| SQLAlchemy | 2.0.51 | MIT | Actively maintained; explicit transactions and SQLite persistence. Adds `greenlet`, with a macOS arm64 wheel. | Repository contracts isolate persistence. Replacing it requires rewriting repositories and migration integration, not domain models. |
| Alembic | 1.18.5 | MIT | Maintained with SQLAlchemy; adds Mako and MarkupSafe for migration generation. Runtime upgrades remain offline after installation. | Replace only with a tested migration runner preserving revision history, retry behavior, and recovery guarantees. |
| Typer | 0.27.0 | MIT | Actively maintained; adds Rich and Shellingham for CLI behavior. Pure Python on the target platform. | Keep command services independent of Typer so another CLI layer can call the same application contracts. |

These libraries solve standardized or failure-prone boundaries. CONTX does not
add FastAPI, OCR, or web dependencies until the milestone that has an immediate
consumer for them.

## External local model runtime

| Component | Evaluated identity | License | Cost and platform notes | Removal strategy |
|---|---|---|---|---|
| Ollama | 0.32.5 | MIT | External loopback-only model runtime. It is installed and updated outside the Python environment and is not bundled by CONTX. | Replace behind `ModelProvider` only after reproducing the local-only boundary, strict validation, model identity, and evaluation gates. |
| Gemma 4 E4B QAT | `gemma4:e4b-it-qat`, digest `ee665637121887cf3befff38abbb1be4ee117c7db867d97a67e29049ecd7e15f` | Apache-2.0 | Selected v0 default for the M4/16 GB target. Approximately 6.1 GB on disk in Ollama's external store; sampled cold peak RSS was approximately 6.07 GiB. The repository neither downloads nor redistributes the weights. | Change the configured model only after the fixed synthetic matrix, persistent-pipeline proof, resource comparison, and an accepted replacement decision. |

The [Gemma 4 model card](https://ai.google.dev/gemma/docs/core/model_card_4)
and the installed artifact's license output provide current license evidence.
If CONTX later downloads or redistributes runtime binaries or model weights,
the distribution and notice obligations must be reviewed again rather than
inferred from this external-development setup.

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
CONTX. The resolved environment contains 33 installed packages including
development tooling. `uv` verified a source distribution and wheel containing
only the CONTX Python package and the project license.
