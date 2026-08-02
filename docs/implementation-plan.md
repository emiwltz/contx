# CONTX Implementation Plan

**Status:** Accepted for execution

**Date:** 2026-08-02

**Product owner:** Emi

**Initial target:** Apple Silicon Mac, single user, one primary agent

**Normative source:** `cahier_des_charges.md`

## Execution status

| Scope | Status | Evidence |
|---|---|---|
| Phase 0 decision baseline | Complete | ADRs 0001–0006, aligned specification and architecture, Apache-2.0 baseline, OptMem provenance gate |
| J0 engineering foundation | Complete | Locked Python 3.12 package, private runtime paths, Alembic/SQLite WAL, strict schemas and repositories |
| v0.0.1 vertical slice | Complete | Synthetic and live one-shot collectors, candidate decision, OptMem persistence, provenance, direct `contx wake`, replay/failure tests |
| v0.1 / J1 | In progress | Controlled metadata and screenshot policy, exclusions, bounded raw storage, restart-safe purge, gated AppKit daemon, menu control, permission preflights, and launchd manifest are implemented; real activation, installation, one-day collection, and measured target-Mac baselines remain gated |
| v0.2 / J2 | In progress | Mandatory loopback Ollama boundary, strict prompt v2/schema validation, persisted version-scoped transformations, restart-safe backlog/retry, `contx process`, and a real synthetic full-pipeline proof are implemented; event building, comprehensive sensitive fixtures, and resource gates remain |

The completed vertical-slice evidence and residual limitations are recorded in
`docs/evaluation/v0.0.1-validation.md`. Completion here does not imply that the
whole v0 is complete; v0 still requires J1 through J7 and the real pilot.

## 1. Purpose

This document turns the founding specification and the decisions accepted by
Emi on 2026-08-02 into an executable delivery plan.

It defines:

- the release model from the first vertical slice through v1;
- the architecture and operational boundaries that implementation must keep;
- the order in which capabilities will be built and validated;
- the acceptance gates for each increment;
- the decisions that remain intentionally deferred;
- the points where implementation must stop for explicit approval.

This is an implementation plan, not a replacement for the product
specification or accepted Architecture Decision Records (ADRs). If evidence
invalidates this plan, the affected step must return to discovery and the plan
must be revised before implementation continues.

## 2. Accepted decision baseline

The following decisions are approved for planning and implementation.

| Area | Decision | Consequence |
|---|---|---|
| Release model | v0 spans Milestones 0 through 7; v1 is Milestone 8 hardening and release readiness | The current specification's "V1 acceptance criteria" will become the v0 product acceptance criteria |
| Context boundary | OptMem or its successor produces the semantic context directly | There is no separate Context Builder; the agent gateway may transport, paginate, and report technical status, but must not add a second semantic context source |
| First collection path | A deterministic synthetic collector and an on-demand live macOS active-application collector | Window titles are opt-in; screenshots are excluded from the first vertical slice |
| First consuming agent | Codex | The first real integration is tested with Codex while the shell contract remains agent-neutral |
| First product proof | Project resumption, using CONTX itself as the controlled dogfooding case | Early fixtures, candidate rules, and evaluation focus on resuming project context |
| Backend foundation | Python 3.12, Pydantic, SQLAlchemy 2, Alembic, SQLite WAL, Typer, pytest | Python is pinned by `uv`; persistence models and boundary schemas remain distinct |
| Runtime data layout | Native macOS locations | Durable state, temporary raw data, and logs have separate lifecycle and backup behavior |
| OptMem | CONTX may use and evolve the complete OptMem codebase | OptMem remains behind `MemoryStore`; provenance and redistributable license evidence are still required before public distribution |
| Initial process model | Explicit on-demand execution first; a background daemon only after control and visibility exist | No unattended collection in the first vertical slice |
| Project license | Apache License 2.0 | J0 adds the canonical license text and records third-party notices separately |
| Git workflow | Work directly on `main` | Commits remain small and local; no push, amend, rebase, or history rewrite without explicit approval |

## 3. Release model

Version numbers below are delivery labels. They do not create a public
compatibility promise before v1. Persisted user data is protected by explicit
migrations from the first version regardless of the public API status.

### 3.1 v0

v0 is complete when Milestones 0 through 7 are complete and the product has
been validated in a 7- to 14-day real pilot.

A complete v0 must:

- collect the intended Mac activity selectively;
- enforce pause and exclusions before capture;
- keep raw data local and purge it within 48 hours;
- turn observations into traceable events, patterns, and memory candidates;
- prevent direct agent writes to memory;
- produce the agent's semantic context directly from `MemoryStore`;
- allow the user to inspect and correct the transformation chain;
- require a local multimodal model for semantic processing;
- operate without any remote model or outbound user-content path;
- expose usable local controls;
- demonstrate measurable benefit for project resumption, recent-period
  understanding, and change detection;
- remain light enough for daily use on the target M4 Mac with 16 GB RAM.

v0 may still require a developer-oriented installation and may lack polished
update, backup, recovery, and public distribution workflows.

### 3.2 v1

v1 is the hardening and release-readiness milestone. It adds the operational
properties required for confident daily installation and broader open-source
distribution:

- supported install, upgrade, and uninstall paths;
- backup, restore, export, and recovery validation;
- schema and configuration compatibility policy;
- signed or otherwise clearly documented macOS distribution;
- resource and long-duration reliability validation;
- completed security and privacy documentation;
- complete third-party license and notice chain;
- user and contributor documentation;
- resolved critical findings from the v0 pilot.

### 3.3 Planned increments

| Increment | Specification milestone | Outcome |
|---|---|---|
| v0.0.1 | J0 plus minimal vertical slice | Reproducible foundation and one end-to-end path from observation to `contx wake` |
| v0.1 | J1 | Controlled macOS collection, exclusions, bounded raw storage, and purge |
| v0.2 | J2 | Mandatory local multimodal interpretation, structured output, sensitivity, and transformation audit |
| v0.3 | J3 | Rebuildable, provenance-backed events and activity timeline |
| v0.4 | J4 | Multi-event patterns, changes, candidates, scoring, and worker decisions |
| v0.5 | J5 | Complete memory lifecycle and real Codex integration |
| v0.6 | J6 | Local web controls and inspectable transformation views |
| v0.9 | J7 | Real pilot, comparison, error analysis, and final memory strategy decision |
| v1.0 | J8 | Hardening, distribution, recovery, documentation, and release readiness |

## 4. Architectural boundaries

### 4.1 System shape

CONTX is a modular monolith. Modules may be executed by a small number of
local processes, but process separation must follow demonstrated operational
needs rather than mirror every code module.

The initial runtime progression is:

1. an on-demand CLI application service for v0.0.1;
2. one supervised local daemon for continuous collection in v0.1;
3. CLI, menu-bar control, and later local web UI as clients of the same
   application contracts.

Microservices, distributed queues, mandatory containers, and mandatory cloud
services are out of scope.

### 4.2 Semantic pipeline

The domain pipeline keeps these concepts distinct in code and storage:

```text
Observation
    -> Event
    -> Pattern or inference
    -> MemoryCandidate
    -> MemoryWorker decision
    -> MemoryStore entry
    -> wake output
```

A narrow v0.0.1 path may create a candidate from one explicitly important
event because the specification permits it. It must not weaken the conceptual
separation or accept trivial live observations merely to make a demonstration
pass.

The positive end-to-end acceptance fixture will therefore represent sustained,
meaningful project work. A one-shot live active-application observation is
allowed to stop before memory when it is not useful enough to retain.

### 4.3 Context boundary

`MemoryStore.wake()` is the only source of semantic memory context delivered
by `contx wake`.

The agent gateway may:

- invoke the memory backend;
- preserve a stable CLI contract;
- paginate without changing semantic content;
- report technical conditions separately, such as unavailable memory,
  pending maintenance, or a stale processing timestamp.

It must not:

- inject recent events beside memory;
- build a task-specific context pack from non-memory data;
- silently summarize or rewrite memory output;
- expose raw observations to the consuming agent by default.

If task-specific context construction becomes desirable later, it requires an
explicit product decision, an ADR, and a specification change.

### 4.4 Replaceable contracts

The following boundaries require small typed interfaces before their first
real implementation:

- `Collector`
- `RawStore`
- `EventBuilder`
- `PatternEngine`
- `CandidateProducer`
- `MemoryWorker`
- `MemoryStore`
- `MemoryCompressor`
- `ModelProvider`
- clock and identifier sources where determinism matters

Only interfaces with at least one immediate implementation or test double are
introduced. Empty abstraction layers for hypothetical variants are not.

### 4.5 Runtime data locations

The production defaults are:

```text
~/Library/Application Support/CONTX/
    config.toml
    contx.db
    memory/
    exports/

~/Library/Caches/CONTX/
    raw/
    processing/

~/Library/Logs/CONTX/
```

Requirements:

- directories are private to the local user (`0700` where applicable);
- files containing application state are private (`0600` where applicable);
- raw artifacts never enter the durable export or backup path by default;
- tests use isolated temporary directories;
- a single explicit development override redirects all runtime roots without
  changing production defaults;
- application removal can enumerate every CONTX-owned path before deleting
  anything;
- all persistent schema changes use migrations.

The exact override name and path resolver contract are recorded in the data
foundation ADR before implementation.

### 4.6 Time, provenance, and idempotency

From the first migration:

- timestamps are timezone-aware and stored in a canonical form;
- original source timing remains reconstructible;
- every derived record references its source records;
- processing code and policy versions are recorded;
- retries use stable idempotency keys;
- a crash cannot acknowledge a partially persisted transformation;
- corrections append state transitions or replacement relations rather than
  erasing history silently.

## 5. Repository target

The repository grows only as capabilities are implemented. Empty placeholder
directories are avoided.

The expected shape by the end of v0.0.1 is:

```text
contx/
├── AGENTS.md
├── CHANGELOG.md
├── LICENSE
├── README.md
├── cahier_des_charges.md
├── pyproject.toml
├── uv.lock
├── contx/
│   ├── __init__.py
│   ├── cli/
│   ├── collectors/
│   │   └── macos/
│   ├── events/
│   ├── candidates/
│   ├── memory_worker/
│   ├── memory_store/
│   ├── models/
│   ├── settings/
│   ├── db/
│   └── audit/
├── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── privacy/
├── fixtures/
└── docs/
    ├── adr/
    ├── architecture/
    ├── evaluation/
    ├── threat-model/
    └── implementation-plan.md
```

Later modules and test suites are added with the milestone that needs them.
The web application and its Node toolchain are not scaffolded in v0.0.1.

## 6. Phase 0: reconcile decisions and governance

This phase prevents implementation from beginning against contradictory
documents.

### 6.1 Deliverables

1. Create `docs/adr/` and record at least:
   - the v0/v1 release model;
   - the direct `MemoryStore` context boundary;
   - the Python and persistence foundation;
   - the macOS runtime data layout;
   - the initial process and collection-control model;
   - the OptMem integration and upstream provenance strategy.
2. Update `cahier_des_charges.md`:
   - change the current V1 acceptance terminology to v0 where appropriate;
   - describe v1 as the hardening and release-readiness milestone;
   - clarify that the agent gateway does not add semantic context;
   - retain future-source ideas as explicitly post-v0.
3. Update the architecture source and rendered image:
   - replace the current Context Interface with an Agent Gateway/transport
     boundary;
   - remove semantic enrichment after OptMem;
   - label mail, messages, calendar, location, photos, and other non-Mac-v0
     sources as future scope.
4. Create `CHANGELOG.md` and record the specification revision.
5. Add the canonical Apache-2.0 project license.
6. Create a third-party provenance record for OptMem including:
   - upstream repository URL;
   - imported commit;
   - import date;
   - local modifications, if any;
   - upstream license or written permission evidence;
   - update procedure.

### 6.2 OptMem rights gate

Emi has authorized use and evolution of the OptMem code and knows its creator.
Development may use the existing local reference clone.

Before OptMem code is tracked in CONTX or distributed to third parties, the
repository must contain redistributable permission in a form future users can
rely on: preferably an upstream license file, or an explicit written grant
that covers use, modification, and redistribution under compatible terms.

Until that evidence exists:

- the ignored `optmem/` clone remains a development reference;
- CONTX integrates it through `MemoryStore` without copying it into a release;
- CI and public packages must not silently depend on the untracked clone;
- the release is marked non-redistributable with OptMem bundled.

Once the rights gate is satisfied, the planned tracked location is
`third_party/optmem/`, with the upstream snapshot kept reviewable and local
changes isolated. `MemoryStore` remains the only dependency boundary even if
the full source is available in the repository.

### 6.3 Exit gate

- The specification, ADRs, README, and architecture diagram agree.
- The v0 and v1 acceptance boundaries are unambiguous.
- The Apache-2.0 license is present.
- OptMem's development and release conditions are explicit.
- No product code has to interpret conflicting context architecture.

## 7. Phase 1: J0 engineering foundation

### 7.1 Project and dependency setup

- Pin Python 3.12 in `pyproject.toml` and the `uv` configuration.
- Generate and commit `uv.lock`.
- Define only runtime and development dependencies needed by v0.0.1.
- Configure pytest, Ruff, and type checking.
- Expose the `contx` console entry point through Typer.
- Keep all commands functional without network access after dependencies are
  installed.

Dependency changes require a short review of maintenance health, license,
platform support, transitive cost, and removal strategy before addition.

### 7.2 Settings and filesystem foundation

- Implement a typed settings loader with explicit defaults.
- Resolve production and overridden runtime paths in one module.
- Create directories idempotently with restrictive permissions.
- Validate configuration before starting collection or opening persisted
  state.
- Provide actionable errors for missing permissions, invalid paths, unwritable
  storage, and incompatible configuration.
- Never log secrets or full private payloads.

Initial commands:

```text
contx --help
contx init
contx status
```

`contx init` is idempotent. It creates only known CONTX-owned paths and the
initial database. It does not enable background collection.

### 7.3 Database foundation

- Configure SQLite WAL and foreign-key enforcement.
- Define SQLAlchemy models separately from Pydantic domain schemas.
- Add Alembic with a tested initial migration.
- Implement explicit transaction boundaries.
- Add repository contracts only for records used by the vertical slice.
- Record processing status and failures without including private content in
  error fields.

Minimum persisted concepts for v0.0.1:

- `Observation`
- `Event`
- `MemoryCandidate`
- `MemoryLink`
- `ProcessingRun`

Exclusions and richer audit records may be included in the first migration
only if they are required to enforce a boundary in the vertical slice. Other
concepts are added through later migrations rather than speculative columns.

### 7.4 Foundation tests

- settings validation and precedence;
- production path resolution and temporary test overrides;
- private directory and file permissions;
- clean database creation;
- migration upgrade from an empty database;
- startup after an interrupted initialization;
- SQLite foreign keys and WAL configuration;
- CLI help, initialization idempotency, and actionable error output;
- offline execution after installation.

### 7.5 Exit gate

From a clean checkout on the target Mac:

```sh
uv sync
uv run contx init
uv run contx status
uv run pytest
uv run ruff check .
```

The project starts, the database is created through migrations, runtime paths
are private, repeated initialization is safe, and the relevant checks pass.

## 8. Phase 2: v0.0.1 minimal vertical slice

### 8.1 Objective

Prove one end-to-end path without screenshots, OCR, patterns, a daemon, a
remote model, or a web UI:

```text
active application observations
    -> persisted Observation
    -> simple provenance-backed Event
    -> evaluated MemoryCandidate
    -> explicit MemoryWorker decision
    -> MemoryStore/OptMem
    -> contx wake
```

### 8.2 Collector boundary

Implement one `Collector` contract and two implementations.

#### Synthetic collector

The synthetic collector:

- reads deterministic fixtures;
- uses synthetic names, paths, and times;
- models sustained work on the CONTX project;
- includes meaningful and trivial cases;
- supports exact replay;
- never reads the user's real activity.

#### macOS active-application collector

The initial live collector:

- runs only after an explicit CLI invocation;
- reads active application name and bundle identifier;
- records collection capability and permission failures;
- does not collect screenshots;
- does not run OCR;
- does not enable launchd or persistent polling;
- leaves window-title collection disabled by default;
- stores no window title unless a later explicit configuration enables it.

The live collector proves the platform boundary. It is not required to create
a memory from an isolated observation when that observation is not useful.

### 8.3 Event and candidate rules

The initial deterministic path must be deliberately narrow:

- a coherent fixture sequence becomes one bounded project-work event;
- the event records all source observation identifiers;
- factual fields remain separate from the summary interpretation;
- confidence is a ranking aid, not a probability claim;
- a trivial or unsupported event is rejected or deferred;
- only an explicitly useful project-resumption event reaches memory;
- every accepted candidate has provenance before `MemoryStore.append()`;
- replaying the same inputs does not create duplicate events, candidates, or
  memories.

The vertical slice must demonstrate both a positive path and a justified
rejection. It must not lower the memory-quality threshold to manufacture an
end-to-end success.

### 8.4 Memory boundary

Define a typed `MemoryStore` contract covering the capabilities needed now and
the known v0 direction without exposing OptMem's filesystem internals.

Initial implementations:

- an in-memory or recording store for deterministic unit tests;
- an OptMem adapter using an explicit memory directory under CONTX's durable
  runtime state.

The adapter must:

- validate OptMem availability and version/provenance;
- pass an explicit memory directory rather than relying on global user state;
- use bounded subprocess execution or an equivalently isolated adapter;
- preserve UTF-8 and byte limits;
- serialize concurrent writes safely;
- surface actionable failures without a traceback or private payload;
- treat OptMem output as untrusted process output at the parsing boundary;
- keep `MemoryLink` provenance and correction metadata in SQLite;
- never let other CONTX modules read or modify OptMem storage directly.

OptMem maintenance is represented explicitly. v0.0.1 may use a deterministic
compressor for synthetic fixtures and may report unresolved maintenance for
real data. It must not pretend a lossy placeholder compressor is a production
memory strategy. A complete offline compression strategy is a v0.5 exit gate.

### 8.5 CLI surface

The planned development and user-facing commands are:

```text
contx run-once --source synthetic
contx run-once --source active-app
contx wake
contx status
```

`run-once` invokes the same application services later used by the daemon. It
is not a separate prototype pipeline.

`contx wake` returns the semantic memory output directly. Technical status or
maintenance instructions are clearly separated and do not inject recent
events into the memory body.

### 8.6 Vertical-slice tests

#### Unit

- observation, event, candidate, and memory-link validation;
- candidate state transitions;
- provenance requirements;
- byte-limit handling;
- candidate rejection for trivial or unsupported activity;
- deterministic time and identifier injection;
- idempotency-key construction.

#### Integration

- synthetic observations to persisted event;
- event to accepted and rejected candidates;
- accepted candidate to test `MemoryStore`;
- accepted candidate to isolated OptMem memory;
- duplicate replay without duplicate memory;
- restart between every pipeline stage;
- transaction rollback after an injected failure;
- `contx wake` after successful storage;
- OptMem unavailable, corrupt output, timeout, and pending-maintenance paths.

#### Privacy

- no screenshots or OCR artifacts are produced;
- window titles remain absent by default;
- raw or derived private text is absent from logs and exception messages;
- no network call is attempted;
- synthetic secrets never reach a memory or outbound payload;
- the test OptMem directory is isolated from the user's personal memory.

#### macOS smoke test

- explicit active-application collection returns the expected capability
  status on the target Mac;
- denial or absence of permission produces an actionable degraded result;
- no background process remains after the command exits.

The smoke test is separated from deterministic CI because it depends on the
host session and macOS permissions.

### 8.7 v0.0.1 exit gate

All of the following must be true:

- a clean checkout installs reproducibly with Python 3.12 and `uv`;
- the database is created and upgraded through Alembic;
- synthetic project activity reaches `contx wake` end to end;
- every accepted memory has queryable provenance;
- a trivial live observation is not promoted merely for demonstration;
- an explicit live active-app command works or reports a precise capability
  limitation;
- replay and restart do not duplicate accepted output;
- execution remains fully offline;
- no screenshot or window title is collected;
- no unattended collection is enabled;
- focused and full relevant test suites pass;
- the final diff contains no runtime data, secrets, captures, databases, or
  machine-specific paths.

## 9. Phase 3: v0.1 controlled macOS collection

### 9.1 Scope

- continuous active application and optional authorized window metadata;
- durations and session boundaries;
- idle, locked, asleep, and wake state;
- capability and permission model;
- exclusions applied before any sensitive capture;
- bounded raw store with `expires_at`;
- purge at startup and on a schedule;
- selective screenshots only after exclusion tests pass;
- one supervised local daemon;
- immediate pause and resume;
- visible collection status before unattended use.

### 9.2 Control surface

A persistent collector is not enabled until the user can see and control its
real state. The preferred v0.1 control is a minimal menu-bar surface backed by
the same application service used by the CLI.

Before implementing a native helper, run a focused prototype to determine
whether Python/PyObjC is sufficient or whether a small Swift component and
full Xcode are justified. Choosing the helper technology is an ADR because it
affects packaging, permissions, signing, and long-term maintenance.

### 9.3 Privacy order

Implementation order is mandatory:

1. capability detection;
2. pause state and exclusions;
3. active application and duration;
4. idle and lock state;
5. raw-store retention and purge;
6. synthetic screenshot fixtures;
7. selective live screenshots after explicit action-time approval.

No live screenshot collection begins merely because the code exists.

### 9.4 Exit gate

- one controlled day can be collected on the target Mac;
- pause and exclusion behavior is proven before capture;
- no excluded context creates a screenshot or sensitive title record;
- every raw record has an expiry at or below 48 hours;
- purge survives restart and reports failures;
- the user can see whether collection is active;
- CPU, memory, detection latency, and raw disk use have measured baselines.

## 10. Phase 4: v0.2 local model boundary

### 10.1 Scope

- typed, replaceable `ModelProvider` contract with a local multimodal backend;
- strict request and structured-result schemas;
- direct local interpretation of permitted screenshots and metadata;
- sensitivity categories and user-defined forbidden information;
- prompt, model, schema, latency, source, and processing provenance;
- inspectable local transformation records without private log payloads;
- no remote provider or outbound user-content transport in v0;
- result validation that fails closed.

### 10.2 Required fixtures

Synthetic screenshot and metadata fixtures cover ordinary project work plus
API keys, tokens, passwords, recovery codes, SSH material, payment data,
medical text, login screens, password managers, and private browsing
indicators. They contain no real credentials or personal data. Sensitive
fixtures prove exclusion, sensitivity classification, rejection, logging, and
local-only processing; they are not a requirement to mask content before the
local model sees an authorized capture.

### 10.3 Exit gate

- no user content can cross a remote boundary;
- every authorized fixture is processed only by the local provider;
- excluded sources never reach the model;
- model, prompt, schema, source, latency, and decision provenance is
  inspectable;
- invalid model output is rejected without fixture content in logs or errors;
- sensitive content is not promoted deliberately into durable memory;
- the product requires a configured local model and works fully offline.

## 11. Phase 5: v0.3 events and activity timeline

### 11.1 Scope

- robust sessionization;
- structured event types;
- project and entity attribution;
- observed, inferred, and hypothetical epistemic states;
- confidence, sensitivity, and validity periods;
- processing-version lineage;
- correction and replay;
- intelligible activity timeline from one day.

### 11.2 Exit gate

- a frozen day of observations produces a deterministic, intelligible
  timeline;
- each event links to supporting observations;
- corrections survive rebuilds;
- the same processing version is idempotent;
- a changed processing version can be replayed and compared without erasing
  the former evidence.

## 12. Phase 6: v0.4 patterns and memory candidates

### 12.1 Scope

- recurrence and project-resumption detection;
- temporal comparisons;
- simple change detection;
- pattern validity and expiration;
- candidate scoring, deduplication, fusion, deferral, and rejection;
- transparent worker decisions;
- replay comparison across rules and thresholds.

Project resumption is implemented and evaluated first. Recent-period
understanding and change detection follow without changing the core evidence
model.

### 12.2 Exit gate

- multi-day fixtures produce useful candidates from multiple events;
- a single weak event cannot become a durable pattern;
- every candidate cites its evidence;
- false, ambiguous, sensitive, and redundant candidates are rejected or
  deferred with a reason;
- threshold changes can be compared through replay;
- no test is tuned solely to one expected fixture string.

## 13. Phase 7: v0.5 complete memory and Codex integration

### 13.1 Scope

- complete `MemoryStore` lifecycle;
- automated and offline-capable OptMem compression strategy;
- `wake`, `recall`, and `zoom`;
- agent proposals through validation and provenance;
- append-only corrections and supersession;
- invalid-summary recovery;
- configurable wake budget;
- generic instruction block, validated first with Codex;
- protection against direct agent and subagent writes.

### 13.2 OptMem decision experiment

Compare through the same replay fixtures:

- upstream OptMem behavior;
- OptMem with SQLite sidecar metadata;
- any CONTX-maintained OptMem evolution needed for supersession or filtering.

The experiment measures correctness, correction behavior, context quality,
rebuildability, latency, operational complexity, and exit cost. It does not
select a fork merely because the code is available.

### 13.3 Exit gate

- Codex can start a real session with `contx wake`;
- context comes directly from memory;
- output stays within its configured budget;
- the memory remains usable without network access;
- pending compression cannot silently block or corrupt wake;
- proposals and corrections are validated by CONTX;
- superseded content is not presented as an equal current truth;
- another shell-capable agent can use the same stable command contract.

## 14. Phase 8: v0.6 local web interface

### 14.1 Scope

- local FastAPI contract, versioned before use by the UI;
- React/Vite/TypeScript application bound to `127.0.0.1` by default;
- status and permissions;
- activity, events, and provenance;
- patterns and their evidence;
- candidates, memories, corrections, and wake preview;
- privacy exclusions, raw expiry, local-model inputs/results/provenance, and
  sensitivity decisions;
- settings and operational errors.

The UI is a client of application services. It does not own business rules or
become a second source of truth.

### 14.2 Exit gate

- all important state can be understood without a terminal;
- pause/resume reflects actual collector state;
- destructive actions name their scope and require confirmation;
- the browser cannot access raw files by guessed paths;
- the API is unreachable from non-loopback interfaces by default;
- the UI exposes a clear degraded state when the required local model is
  unavailable.

## 15. Phase 9: v0.9 real pilot

### 15.1 Preconditions

- all privacy gates pass;
- raw purge has been tested under interruption and restart;
- visible pause control is available;
- backup behavior excludes raw artifacts;
- a complete data deletion path is tested;
- real collection receives explicit action-time approval.

### 15.2 Pilot protocol

Run a 7- to 14-day pilot with CONTX as the first controlled project-resumption
case and Codex as the first consuming agent.

Maintain minimal ground truth for:

- important projects and work periods;
- changes of priority;
- decisions and blockers worth remembering;
- events that CONTX should ignore;
- corrections and privacy incidents.

Compare agent behavior with and without CONTX for:

- project resumption;
- recent-period understanding;
- change detection.

Measure accuracy, coverage, false memories, irrelevant memories, duplicates,
manual corrections, provenance coverage, sensitive-content promotion, invalid
model-output rate, context size, wake latency, CPU, memory, and disk use.

### 15.3 v0 exit gate

- all v0 acceptance criteria in the revised specification are evaluated;
- 100% of accepted memories have provenance;
- no raw data exceeds 48 hours;
- no excluded application produces a capture;
- no user content uses a remote transport;
- no synthetic test secret is promoted deliberately into durable memory;
- materially false memories remain below the provisional threshold;
- the majority of user-important events are recoverable;
- the context stays within budget;
- Emi can understand why each sampled memory exists;
- the three target behaviors show a documented benefit or an explicit failure
  analysis;
- the final OptMem strategy is chosen through an ADR.

Failure to demonstrate product benefit does not automatically advance to v1.
The result may instead require changing or narrowing v0 behavior.

## 16. Phase 10: v1 hardening and release readiness

### 16.1 Scope

- address pilot defects and failure patterns;
- measure and optimize long-duration resource use;
- validate crash, sleep, low disk, permission loss, and schema failure paths;
- installation, upgrade, uninstall, and rollback;
- backup, restore, export, and full deletion;
- macOS signing and minimum-version policy;
- stable configuration and data migration policy;
- third-party notices and license verification;
- threat model, privacy guide, operator guide, and contributor guide;
- release packaging and reproducible verification.

### 16.2 v1 exit gate

- a new user can install, understand permissions, pause, inspect, export, and
  uninstall CONTX using documented flows;
- upgrade and rollback protect persisted memory and provenance;
- all critical security and privacy findings are resolved;
- all bundled third-party code has redistributable license evidence;
- performance targets are measured on the target Mac;
- no known temporary production path or manual recovery dependency remains;
- release artifacts are reproducible and pass the complete relevant suite.

## 17. Testing and verification strategy

### 17.1 Test layers

Use the cheapest layer that proves the behavior:

- unit tests for domain rules and state transitions;
- integration tests for SQLite, filesystem, OptMem, process, and migration
  boundaries;
- privacy tests for exclusions, local-only model transport, sensitive
  promotion, invalid output, and logs;
- replay tests for event, pattern, candidate, and memory policy changes;
- performance tests for long-running collection, storage, and wake;
- end-to-end tests for the critical user workflows.

### 17.2 Determinism

- clocks and identifiers are injectable where output depends on them;
- fixtures contain no real user data;
- network access is disabled or explicitly trapped in offline tests;
- test databases, raw stores, logs, and OptMem memory use isolated temporary
  directories;
- expected behavior is asserted at contracts, not private implementation
  details.

### 17.3 Migration verification

Every migration must be tested for:

- clean installation;
- upgrade from the previous released schema;
- interruption and retry where applicable;
- preservation of provenance and memory links;
- explicit rollback support or a documented forward-only recovery path;
- absence of silent data loss.

### 17.4 Completion evidence

Every implementation report states:

- what changed and why;
- the exact tests and checks executed;
- their results;
- what could not be verified;
- privacy, data, and resource impact;
- remaining risks or deferred decisions;
- the commit or commits produced.

## 18. Observability and audit requirements

From the first vertical slice, CONTX must make failure diagnosable without
recording private content.

At minimum:

- every pipeline execution has a `ProcessingRun` identifier;
- state transitions and counts are structured;
- source and derived identifiers allow provenance traversal;
- logs contain categories and hashes or identifiers, not raw text by default;
- purge, migration, memory append, and correction outcomes are auditable;
- local model provider, model identity, prompt/schema versions, input
  references, result linkage, and decision provenance are recorded;
- operational status distinguishes disabled, paused, degraded, failed, and
  healthy states.

## 19. Security and privacy delivery gates

The following changes require explicit action-time approval even if their code
has already been implemented:

- enabling persistent background collection;
- enabling live screenshot collection;
- enabling window-title collection outside a controlled test;
- introducing a remote model or any outbound user-data path, which also
  requires a new product decision and ADR;
- beginning the real-data pilot;
- deleting real user data;
- changing the 48-hour maximum retention invariant;
- opening the API beyond loopback;
- installing system-level helpers or requesting new macOS permissions;
- publishing or distributing bundled OptMem code without verified rights.

## 20. Git and commit sequence

Implementation proceeds directly on `main`, as approved. The existing local
commit remains untouched.

Expected atomic sequence for v0.0.1:

1. `Record foundational architecture decisions`
2. `Align specification with the v0 release model`
3. `Add Apache 2.0 project licensing`
4. `Add Python project foundation`
5. `Add runtime settings and private paths`
6. `Add initial database migration`
7. `Add observation and pipeline contracts`
8. `Add synthetic project activity pipeline`
9. `Add on-demand macOS app collection`
10. `Integrate OptMem behind MemoryStore`
11. `Verify the minimal wake workflow`

The exact grouping may change if review shows that a smaller or different
atomic boundary is clearer. Before each commit:

- inspect status and diff;
- stage only intended files;
- run the focused relevant checks;
- confirm no runtime data, secrets, captures, databases, or machine paths are
  included.

No push is implied by this plan.

## 21. Known risks and planned responses

### 21.1 OptMem rights and maintainability

**Risk:** development relies on code that has no license file in the current
snapshot, or local changes make upstream updates difficult.

**Response:** preserve upstream provenance, obtain redistributable rights,
isolate the backend behind `MemoryStore`, keep local changes reviewable, and
maintain replay coverage against upstream behavior.

### 21.2 False or self-confirming memory

**Risk:** an early inference becomes durable and biases future processing.

**Response:** provenance, epistemic status, deferral, supersession, replay,
ground-truth comparison, and no direct observation-to-memory path.

### 21.3 Privacy failure before processing

**Risk:** a sensitive application or screen is captured before the local model
can classify it.

**Response:** exclusions and pause are enforced at collection time, screenshot
work starts only after those gates pass, and live activation requires explicit
approval.

### 21.4 macOS permission and API fragility

**Risk:** Accessibility, screen capture, or app-state APIs behave differently
across macOS versions or unsigned execution contexts.

**Response:** capability-based collectors, degraded operation, host smoke
tests, small platform adapters, and a focused native-helper experiment before
committing to Swift or full Xcode.

### 21.5 Schema lock-in

**Risk:** the conceptual model is copied prematurely into a rigid schema that
cannot represent correction or replay.

**Response:** persist only concepts used by the current increment, record
processing versions and provenance from the first migration, and evolve only
through tested migrations.

### 21.6 Demonstration-driven quality shortcuts

**Risk:** the first vertical slice accepts trivial activity solely to show a
memory in `wake`.

**Response:** use a meaningful sustained-work fixture for the positive path,
test rejections, and allow an isolated live observation to stop before memory.

### 21.7 Context Builder scope drift

**Risk:** the agent gateway gradually injects recent events or task-specific
data beside memory.

**Response:** direct `MemoryStore` output is an explicit contract with tests;
semantic enrichment requires a new decision and specification change.

## 22. Deferred decisions

The following decisions are intentionally deferred until their milestone has
evidence to support them:

- final OptMem upstream/adapted/forked strategy;
- correction encoding and superseded-memory filtering details;
- semantic search;
- local model identity and MLX versus Ollama/`llama.cpp` runtime;
- whether standalone OCR provides enough measured value as an optimization;
- screenshot format and visual-change thresholds;
- sessionization and pattern thresholds;
- final web design and notification behavior;
- menu-bar implementation technology;
- minimum supported macOS version;
- signing, installer, and update mechanism.

Each consequential choice is recorded in an ADR before implementation.

## 23. Immediate execution sequence

Implementation begins with Phase 0 only:

1. create the ADR structure and decision records;
2. update the specification, README, changelog, and architecture diagram;
3. add Apache-2.0 licensing and OptMem provenance documentation;
4. review and commit that documentation baseline on `main`;
5. start the Python foundation only after the repository is internally
   consistent.

This ordering gives the first code commit a stable product and architecture
contract while keeping every subsequent change small, testable, and
reversible.
