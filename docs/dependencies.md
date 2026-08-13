# Dependency review

This record documents direct dependency decisions. Exact resolved versions and
artifact hashes are authoritative in `uv.lock`.

## Runtime foundation

| Dependency | Locked version | License | Cost and platform notes | Removal strategy |
|---|---:|---|---|---|
| Pydantic | 2.13.4 | MIT | Actively maintained; typed boundary validation. `pydantic-core` provides a macOS arm64 wheel. | Replace boundary models and validation explicitly before removing it. Domain rules must remain independent of serialization details. |
| PyObjC ApplicationServices | 12.2.1 | MIT | Narrow maintained bridge for HIServices Accessibility APIs. It provides the authorized focused-window title path; the Python 3.12 Universal 2 wheel is used and adds the matching PyObjC CoreText package transitively. | Remove it if titles leave v0 scope or a native helper owns Accessibility behind the activity-sampler contract. |
| PyObjC Cocoa | 12.2.1 | MIT | Maintained macOS bridge for AppKit. Adds `pyobjc-core`; the Python 3.12 Apple Silicon wheel is used. No all-framework metapackage is installed. | The `Collector` boundary permits replacement by a small native Swift helper if packaging, permissions, or reliability justify it. |
| PyObjC Quartz | 12.2.1 | MIT | Narrow maintained bridge for public CoreGraphics idle/window metadata and ScreenCaptureKit exact-window capture APIs. The Python 3.12 Universal 2 wheel is used; the all-framework metapackage remains excluded. The screenshot manager path requires macOS 14 or later and captures no display-wide fallback. PyObjC's public manual-metadata registry supplies the two completion-block signatures absent from the target runtime. | Remove it if a future native helper owns idle and screen-capture probes behind the accepted activity contracts. |
| SQLAlchemy | 2.0.51 | MIT | Actively maintained; explicit transactions and SQLite persistence. Adds `greenlet`, with a macOS arm64 wheel. | Repository contracts isolate persistence. Replacing it requires rewriting repositories and migration integration, not domain models. |
| Alembic | 1.18.5 | MIT | Maintained with SQLAlchemy; adds Mako and MarkupSafe for migration generation. Runtime upgrades remain offline after installation. | Replace only with a tested migration runner preserving revision history, retry behavior, and recovery guarantees. |
| Typer | 0.27.0 | MIT | Actively maintained; adds Rich and Shellingham for CLI behavior. Pure Python on the target platform. | Keep command services independent of Typer so another CLI layer can call the same application contracts. |

These libraries solve standardized or failure-prone boundaries. CONTX does not
add OCR dependencies. J6 is the consumer for the web dependencies below.

## J6 local web interface

Both dependency graphs are now resolved. Exact Python artifacts and hashes are
authoritative in `uv.lock`; exact browser build artifacts are authoritative in
`webui/package-lock.json`.

| Dependency | Locked version | License | Cost and removal strategy |
|---|---:|---|---|
| [FastAPI](https://pypi.org/project/fastapi/) | `0.139.2` | MIT | Typed local HTTP and OpenAPI boundary on Pydantic/Starlette. Replaceable without data migration if response validation and security middleware are preserved. |
| [Uvicorn](https://pypi.org/project/uvicorn/) | `0.51.0` | BSD-3-Clause | Minimal ASGI server, forced to literal loopback by CONTX. Replaceable by another local ASGI server after bind and shutdown tests. |
| [httpx](https://pypi.org/project/httpx/) | `0.28.1` | BSD-3-Clause | Development-only in-process FastAPI/Starlette integration tests. Not shipped as an application requirement. FastAPI's compatibility import currently emits a deprecation warning in favor of a future `httpx2` test client. |

| Web build dependency | Selected version | License | Scope and removal strategy |
|---|---:|---|---|
| [React](https://www.npmjs.com/package/react) and React DOM | `19.2.8` | MIT | Browser component layer only. Another `/api/v1` client can replace it without changing product data. |
| [Vite](https://www.npmjs.com/package/vite) | `8.1.5` | MIT | Development server and static production bundler; absent at runtime after packaging. |
| [TypeScript](https://www.npmjs.com/package/typescript) | `7.0.2` | Apache-2.0 | Strict build-time checking; generated JavaScript is the runtime artifact. |
| `@types/react`, `@types/react-dom` | `19.2.7`, `19.2.3` | MIT | Build-time declarations only. |

The React Vite plugin is intentionally omitted: the v0 interface does not need
React Refresh in production, and Vite can compile the selected JSX transform
from the TypeScript configuration. This keeps the build dependency graph
smaller. Type checking and the production build both pass; the npm audit
reports zero known vulnerabilities at the configured moderate severity gate.

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
CONTX. The resolved environment contains 43 installed packages including
development tooling. `uv` verified a source distribution and wheel containing
the CONTX Python package, the built static interface, metadata, and the project
license.
