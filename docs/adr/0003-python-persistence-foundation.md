# ADR 0003: Use the Python persistence foundation

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

CONTX needs a local modular backend, explicit schemas, durable migrations, a
CLI, and deterministic tests. The founding specification selected Python,
Pydantic, SQLite, Alembic, Typer, and pytest but left the ORM choice open.

## Problem

The initial stack must support provenance, corrections, replay, schema
evolution, and macOS integration without coupling API schemas to storage or
depending on the machine's changing system Python.

## Options considered

1. Python 3.12 with Pydantic, SQLAlchemy 2, Alembic, SQLite WAL, Typer, and
   pytest.
2. Python with SQLModel combining validation and persistence models.
3. A native Swift backend.
4. A JavaScript or TypeScript backend.

## Decision

Use option 1.

Python 3.12 is pinned and managed with `uv`. Pydantic schemas define typed
domain and boundary contracts. SQLAlchemy 2 models persistence separately.
Alembic owns all schema changes. SQLite runs locally with WAL and foreign-key
enforcement. Typer provides the CLI and pytest provides the test foundation.

FastAPI is introduced only when a local HTTP contract has an immediate
consumer. The web toolchain is not scaffolded during the first vertical slice.

## Rationale

SQLAlchemy and Alembic make persistence and migrations explicit. Keeping
Pydantic separate avoids binding the domain and public boundaries to a storage
implementation. Pinning Python prevents drift from the host's default runtime.

## Consequences

- Persistence models and boundary schemas require explicit mapping.
- Dependency additions receive maintenance, license, platform, transitive
  cost, and exit-strategy review.
- Tests inject clocks, identifiers, paths, and external adapters when output
  depends on them.
- No database schema is changed without a migration.

## Rollback or replacement

The modular boundaries permit a later backend language or storage replacement,
but persisted data requires an explicit export or migration. A replacement
must preserve provenance, correction history, and offline operation before the
current implementation is removed.
