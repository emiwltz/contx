# AGENTS.md — CONTX working agreement

Read this file first, then the specification. It defines how any agent
(including you) must cooperate with this project. It must never contradict
the specification; if it does, the specification wins and this file must be
updated in the same change.

## 1. What this project is

CONTX is a personal, local-first, open-source infrastructure that selectively
observes a user's activity on their Mac (active app, active window, durations,
selective screenshots) to automatically build the **working memory of their
personal agent**. Raw observations are filtered and secured locally, structured
into events, patterns and inferences, distilled into memory candidates, then
stored through a `MemoryStore` interface backed by OptMem (or a derivative).
The memory output **is** the context handed to the agent — there is no separate
Context Builder.

- **Product owner:** Emi
- **Source of truth:** `cahier_des_charges.md` (French, normative).
  Section references below (`§`) point to it.
- **Target machine:** Mac Apple Silicon M4, 16 GB RAM, macOS 26.

## 2. Specification governance (§0)

- Decision statuses: `[INVARIANT]`, `[DÉCISION V1]`, `[HYPOTHÈSE]`, `[OUVERT]`.
- Normative vocabulary: **DOIT / NE DOIT PAS / DEVRAIT / PEUT**.
- Conflict resolution order (§0.1): latest accepted architecture decision →
  the specification → automated tests → code comments.
- Any structural change (invariant, scope, main architecture) requires: an ADR
  in `docs/adr/`, a spec version bump, a changelog entry, and a review of the
  affected tests (§35.2).

## 3. Current state (2026-08-01)

- Greenfield: the repo contains only the specification, this file, and a
  vendored reference clone of OptMem (`optmem/`).
- **Active goal — Milestone 0 + minimal vertical slice (§37):**
  `active app → local observation → simple event → memory candidate → OptMem → contx wake`
  This slice must work end-to-end before OCR, complex patterns, or any UI.

## 4. Confirmed working agreements (decided with the owner, 2026-08-01)

1. Start with Milestone 0 + the vertical slice.
2. **Everything in English**: code, comments, identifiers, docs, ADRs, commit
   messages. The specification itself remains in French.
3. **uv** manages the Python toolchain (3.12+) and dependencies.
4. Git: the agent may initialize the repo and create atomic commits. Never
   push, amend, rebase, or force-push without explicit owner approval.
5. Agent integration is **generic**: a small instruction block for any agent
   able to run shell commands (§19.6). No agent-product-specific coupling.
6. Memory backend: OptMem **unchanged** behind the `MemoryStore` interface,
   with sidecar metadata (provenance, confidence, relations) in SQLite —
   option B of §18.5. The final A/B/C decision stays **[OUVERT]** until the
   real pilot (Milestone 7).

## 5. Invariants to respect when writing code (§4)

The load-bearing ones for daily work:

- Single user, local-first, fully functional offline (§4.1, 4.2, 4.15).
- Raw data never leaves the Mac, is filtered locally, and is deleted **at most
  48 h after capture**: every raw record carries `expires_at`, a logged purge
  runs regularly and at startup (§4.3–4.5, §12).
- Remote model calls are optional, **disabled by default**, and inspectable
  by the user (§4.6–4.8, §13.4). Never send raw data remotely (§13.1).
- A raw observation never becomes a memory directly; observation, event,
  pattern/inference, candidate, and stored memory are distinct stages (§4.9–4.10).
- Agent proposals go through CONTX validation before the `MemoryStore`; the
  agent never owns the memory; subagents never write to it (§4.11–4.12, §19.4–19.5).
- OptMem (or a derivative) is the final context layer; no separate Context
  Builder (§4.13–4.14).
- The user can pause collection instantly and exclude apps/windows; the
  collector never screenshots blindly at a fixed cadence (§4.16–4.18, §10.3, §11).
- Modular monolith; every replaceable component (OCR, model, raw store, event
  engine, pattern engine, memory backend, frontend, agent integration) sits
  behind a stable interface (§4.24–4.25, §35.3).
- Quality over quantity (§4.23). Never become an agent orchestrator, never
  execute the user's personal actions (§4.21–4.22).

## 6. Repository layout (§23)

```text
contx/
├── cahier_des_charges.md   # source of truth (French)
├── AGENTS.md               # this file
├── optmem/                 # third-party reference clone — READ-ONLY, untracked
├── pyproject.toml
├── apps/{api,cli,web}/
├── contx/                  # the Python package (collectors, raw_store,
│                           # privacy, processing, events, patterns,
│                           # candidates, memory_worker, memory_store,
│                           # agent_gateway, audit, models, settings, db)
├── migrations/
├── tests/{unit,integration,privacy,replay,performance}/
├── fixtures/
├── scripts/
└── docs/{architecture,adr,evaluation,threat-model}/
```

Rules:

- `optmem/` is a vendored third-party clone (VictorTaelin/OptMem). Treat it as
  **read-only**. Never import its internals from `contx` modules — the memory
  layer reaches OptMem only through the `MemoryStore` adapter. It is currently
  untracked (see `.gitignore`); the vendoring strategy is a Milestone 0 decision.
- Runtime data lives **outside the repo**, in `~/.contx/` (permissions `0700`):
  `contx.db`, `raw/`, `logs/`, `config.toml`. Never commit runtime data,
  captures, or databases.

## 7. Stack and tooling (§22)

- Python 3.12+ (via uv), Pydantic schemas, SQLite in WAL mode, SQLAlchemy or
  SQLModel, Alembic migrations, Typer CLI, FastAPI (local API), pytest.
- macOS: PyObjC (NSWorkspace, Accessibility API, ScreenCaptureKit/CoreGraphics),
  background launch via `launchd`.
- OCR behind a replaceable interface; Apple Vision is the first candidate
  `[HYPOTHÈSE]`.
- Model access behind `ModelProvider` (§22.4); deterministic rules with no
  model are a valid backend.
- Web UI (Milestone 6): React + Vite + TypeScript SPA, bound to `127.0.0.1` only.

Commands once the scaffold exists:

```sh
uv sync                 # create venv, install dependencies
uv run pytest           # run the test suite
uv run contx --help     # CLI entry point
```

## 8. Definition of done (§34)

A feature is done only if: its behavior is documented, its data is modeled,
its errors are handled, its logs leak no secrets, it has unit tests (and
integration tests where needed), its privacy and resource impacts are
assessed, its state is understandable from the interface, it respects the
retention policy, it works offline when required, and it ships a migration
strategy when it touches persisted data.

## 9. Testing strategy (§29)

- Suites: `tests/unit`, `tests/integration`, `tests/privacy`, `tests/replay`,
  `tests/performance`.
- Privacy gates (§28.7): no test secret ever appears in an outbound payload;
  no excluded app produces a capture; 100% of accepted memories have a
  provenance; no raw data older than 48 h.
- Every bug fix ships with a regression test.
- The pipeline must be replayable: a frozen set of observations must allow
  comparing two processing versions, two models, or two memory strategies (§29.4).

## 10. Git conventions

- Small, atomic commits; imperative English subject (e.g. `Add raw purge job`).
- Never commit: secrets, runtime data, raw captures, virtualenvs, `optmem/`.
- ADR before any structural decision: `docs/adr/NNNN-short-title.md` with
  context, problem, options, decision, rationale, consequences, rollback (§35.1).
- No push, amend, rebase, or force-push without explicit owner approval.

## 11. Scope guardrails — do NOT build in V1 (§5)

No phone/watch/location/smart-home collection, no calendar/mail/message
ingestion, no multi-user, no multi-agent memory, no mandatory cloud sync, no
mobile app, no Windows/Linux support, no general automation, no in-app actions,
no built-in chat assistant, no psychological/emotional profiling, no permanent
screenshot archive, no screen video recording, no commercial profiling, no
default telemetry. Scope drift is a named risk (§31.10): respect the milestones.

## 12. Roadmap (§30)

J0 foundations → J1 macOS collection → J2 local privacy → J3 events →
J4 patterns & candidates → J5 memory & agent → J6 web UI → J7 real pilot →
J8 hardening. Acceptance criteria for V1: §33.

## 13. Key references

- `cahier_des_charges.md` — normative specification: data model §21, local API
  draft §24, performance targets §27, quality thresholds §28.7, acceptance §33.
- `optmem/README.md` — OptMem contract: `wake`, `note` (one line ≤ 280 bytes),
  `nap`, `recall <regex>`, `zoom <lo>-<hi>`, `forget`. Append-only log +
  rebuildable binary summary tree.
- `MemoryStore` interface: §18.4. Integration instruction block: §19.6 (must
  include the subagent rule, §19.5).
