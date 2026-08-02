# Changelog

All notable changes to CONTX are documented in this file.

The project follows a pre-v1 milestone scheme during development. Persisted
data migrations remain explicit even when public API compatibility is not yet
guaranteed.

## [Unreleased]

### Added

- Detailed implementation plan from the first vertical slice through v1.
- Architecture Decision Record structure and the initial accepted decisions.
- Apache-2.0 project licensing baseline.
- OptMem provenance and redistribution gate documentation.
- Python 3.12 project foundation with a typed Typer CLI and reproducible `uv`
  environment.
- Typed configuration, native macOS runtime paths, and idempotent private-path
  initialization.
- Initial SQLite WAL schema with Alembic migrations, foreign-key-backed
  provenance, and explicit transaction boundaries.
- Strict domain records, deterministic idempotency, replaceable pipeline
  contracts, and transaction-scoped repositories.
- A replayable synthetic CONTX project-resumption pipeline with an explicit
  positive memory decision and a justified trivial-activity rejection.
- An explicit one-shot macOS frontmost-application collector that stores no
  window title, screenshot, or artifact.

### Changed

- Defined v0 as Milestones 0 through 7 and v1 as Milestone 8 hardening.
- Clarified that semantic agent context comes directly from `MemoryStore`.
- Selected native macOS runtime data locations.
- Selected an on-demand collection path before any background daemon.
