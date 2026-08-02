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
- A typed final-memory contract with deterministic test storage and a
  checksum-pinned, bounded OptMem subprocess adapter.
- Crash-recoverable idempotent memory appends, SQLite provenance links, and
  direct `contx wake` context output.
- A mandatory local-model boundary with loopback-only Ollama transport,
  schema-validated multimodal results, content-free execution provenance,
  safe runtime preflight, and a synthetic benchmark.
- A restart-safe, version-scoped local-model backlog with bounded retries,
  interruption recovery, content-free status, persisted transformations, and
  deterministic provenance-backed event construction.
- A fixed 16-screen synthetic privacy and quality matrix, configurable image
  profiles, conservative category-to-sensitivity normalization, and a hard
  final gate preventing sensitive or forbidden durable-memory writes.
- Stable event lineage and validity fields, a bounded event vocabulary, and a
  migration that preserves existing events.
- Configurable deterministic sessionization and processing-run-selected
  activity timelines with explicit frozen-window replay parameters.
- Append-only event correction chains that survive compatible cross-version
  rebuilds without changing source evidence or sensitivity.
- `contx timeline build`, `timeline show`, and `timeline correct` commands for
  explicit replay, inspection, and correction.
- Immutable multi-event pattern snapshots for recurrence, project resumption,
  and temporal change, plus fused memory candidates and append-only policy
  decisions with complete replay provenance.
- Provenance-preserving promotion of accepted candidates to OptMem, bounded
  local-model compression, direct historical `recall` and `zoom`, and stable
  agent memory instructions.
- Restart-safe append-only memory corrections composed by the mandatory local
  model, with explicit `Correction:` semantics, linear supersession, inherited
  provenance, idempotent OptMem recovery, and content-free model audit.
- Explicit agent-proposal review commands, mandatory local semantic adoption,
  unchanged OptMem append, transitive provenance, audited negative decisions,
  and restart-safe idempotent recovery.
- An atomic source-fingerprinted OptMem projection for active-only `wake`, with
  exact SQLite-backed membership, complete local compression before publish,
  stale-build detection, crash recovery, bounded generation retention, and
  generation-stable pagination.
- `contx memory rebuild-active` for safe local regeneration of a poor active
  summary without mutating historical OptMem summaries.
- A real 32-entry long-history comparison covering correction chains,
  independent durable facts, historical inspection, and active projection
  reuse with the installed local Gemma model.
- A bounded inspection service and explicit `/api/v1` FastAPI contract for
  status, activity, patterns, memory, privacy, processing, agent, and settings
  views without exposing raw filesystem paths.
- A same-origin React/Vite/TypeScript control interface source with visible
  local-model degradation, pause/resume, exclusions, wake preview, append-only
  corrections, proposal review, and inspectable provenance.
- Loopback host, origin, fetch-site, strict JSON content-type, caching, framing,
  referrer, and MIME-sniffing protections for the local browser boundary.
- Exact-confirmation immediate raw purge and full-data deletion; complete
  deletion refuses a running collector and unsafe filesystem roots, closes
  SQLite, and retires the current API process.
- A private, versioned real-pilot harness with independent ground truth,
  same-task paired with/without-CONTX trials, technical and resource evidence,
  privacy incidents, cutoff-aware scoring, and aggregate acceptance reports.
- `contx pilot prepare`, `pilot validate`, and `pilot report` commands that do
  not enable collection or request macOS permissions.
- `contx pilot sample-resources` with exact-PID native macOS CPU/RSS sampling,
  safe disk measurement, four representative phases, and atomic private
  evidence append.

### Changed

- Revised the product specification to version 0.3: v0 now requires a local
  multimodal model, removes deterministic semantic extraction and secret
  redaction from scope, and prohibits remote user-content processing.
- Defined v0 as Milestones 0 through 7 and v1 as Milestone 8 hardening.
- Clarified that semantic agent context comes directly from `MemoryStore`.
- Selected native macOS runtime data locations.
- Selected an on-demand collection path before any background daemon.
- Selected `gemma4:e4b-it-qat` as the v0 default local model after a 16/16
  synthetic matrix result and target-Mac resource comparison; Ollama thinking
  is disabled and model output is bounded to 512 tokens.
- Selected upstream OptMem plus SQLite sidecar metadata for v0 corrections;
  writes and source history remain append-only, while ADR 0015 now routes
  `wake` through a separate active-only OptMem projection and keeps `recall`
  and `zoom` historical.
- Strengthened correction and compression prompts so current replacements do
  not restate obsolete claims and `Correction:` authority survives tree merges.
- Centralized event summary correction so CLI and HTTP clients preserve the
  same append-only semantics.
- Corrected persisted observations so optional activity interval boundaries
  remain readable when absent.
