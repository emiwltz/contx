# ADR 0015: Build `wake` from an atomic active-only OptMem projection

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

ADR 0013 selected append-only OptMem corrections with authoritative
`Correction:` entries and SQLite-owned supersession metadata. It also defined
an explicit fallback: if chronological context or rebuilt summaries could not
reliably present only the current truth, CONTX would build a separately
identified OptMem projection from active SQLite records before considering an
OptMem fork.

The long-history experiment exercised 32 synthetic entries, a four-state
correction chain, independent durable facts, every level of the real upstream
OptMem merge tree, and the mandatory local `gemma4:e4b-it-qat` model. A valid
correction fixture that named the obsolete claim caused a root summary to
retain that claim as current beside its successor. After strengthening the
compression prompt and using replacement-only correction lines, the root was
correct, but the normal budgeted wake still exposed an older
`production-only` child summary beside the newer `edge-only` child summary.

The correction protocol is understandable to tested agents, but `wake` is the
product's current semantic-memory view. Requiring every consumer and product
surface to resolve arbitrary historical correction chains would make that view
less reliable and would duplicate correction interpretation outside CONTX.
`recall` and `zoom`, in contrast, are intentionally historical inspection
tools.

## Problem

CONTX needs an active-only `wake` while preserving all of these properties:

- the append-only OptMem source history remains inspectable and exportable;
- local Gemma and OptMem still produce the delivered semantic context;
- SQLite does not become a second text summarizer or context builder;
- a crash or model failure cannot replace a valid projection with a partial
  one;
- concurrent active-memory changes cannot publish a stale generation;
- pagination cannot mix two generations that happen to contain the same
  number of entries;
- the solution remains replaceable and substantially simpler than an OptMem
  fork.

## Options considered

1. Keep chronological `wake` and rely on every consumer to interpret all
   correction chains.
2. Rebuild a separate OptMem store from exact active SQLite-backed memory text.
3. Generate active context directly from SQLite.
4. Fork OptMem to add native record status and supersession.

## Decision

Use option 2 for v0.

The existing `memory/` OptMem identity remains the append-only historical
source. Every successful promotion, proposal adoption, and correction appends
there exactly as before. `contx recall`, `contx zoom`, historical summary
maintenance, summary invalidation, audit, and future export continue to use
that identity.

`contx wake` uses a separate `memory-active/` projection. SQLite selects
`MemoryLink.status == active` records in stable chronological order. CONTX
copies each selected candidate's exact persisted text into a fresh OptMem
generation; it does not extract, rewrite, mask, enrich, rank, or summarize the
text itself. Upstream OptMem and the mandatory loopback-only local compressor
then build the context tree. The resulting OptMem `wake` output is transported
directly to the agent.

Each active set has a SHA-256 source fingerprint over the projection version,
ordered memory and candidate identities, idempotency identity, and exact text.
Each complete build also receives a distinct generation identity so the same
source set can be rebuilt without mutating or deleting its current generation.
Synchronization follows this protocol:

1. acquire a private projection lock;
2. read the active SQLite snapshot and reuse a complete matching generation if
   one already exists;
3. otherwise build under a private `.building-*` directory;
4. append every active text with a projection-specific idempotency key;
5. run bounded OptMem maintenance cycles until OptMem reports completion;
6. write a private readiness marker;
7. acquire SQLite's immediate writer reservation, re-read the active set, and
   discard the build if its fingerprint changed;
8. while writers remain reserved, atomically rename the complete generation,
   atomically replace `CURRENT.json`, then release SQLite;
9. retain the current and immediately previous derived generations and remove
   older derived generations.

The append-only source is never deleted or rewritten by projection cleanup.
Interrupted build directories are derived state and may be discarded. A
failed rebuild leaves the previous pointer and generation intact, but `wake`
fails closed rather than knowingly returning stale current context.

CONTX exposes a projection-generation snapshot token for paginated wake. It
translates that token to OptMem's internal entry-count snapshot only inside the
gateway. A continuation therefore reads the retained original generation even
if active memory changes between pages. An unavailable old token fails with an
instruction to restart wake rather than mixing generations.

An empty active set is a valid, complete empty wake. An unchanged active set
reuses its generation without a model call. The first wake after a changed
active set may block while local compression completes; no deterministic or
remote-model fallback exists.

`contx memory rebuild-active` explicitly rebuilds an unchanged source
fingerprint into a new generation, then publishes it through the same protocol.
It is the recovery path for a semantically poor active summary. Historical
`invalidate-summary` remains scoped to the historical tree. Agents require an
explicit user instruction before forcing an active rebuild.

New correction composition uses prompt `memory-correction-v2`, which asks for
only the authorized current replacement and forbids restating the original
claim. This reduces ambiguity inside the exact active correction line; the
projection does not independently try to infer or remove clauses from it.

## Rationale

This design gives `wake` one unambiguous active source while preserving the
successful parts of the OptMem-like workflow: append-only source history,
progressive local merging, direct OptMem context, and historical navigation.
SQLite performs the operation it can prove exactly—status filtering—and OptMem
continues to perform semantic compression.

Source fingerprints and immutable generation identities make interruption and
retry behavior simple to inspect. The short SQLite writer reservation closes the gap between
final validation and filesystem publication; a later write creates the next
snapshot only after publication. Retaining one previous generation is enough
to preserve a continuation across one concurrent memory change without turning
projection storage into another history archive.

The design stays behind existing application and `MemoryStore` boundaries. A
future native active-aware backend can replace the projection without changing
the stored correction chain or provenance.

## Consequences

- `wake` is active-only; `recall` and `zoom` are explicitly historical.
- The durable runtime adds `memory-active/`, private readiness/pointer state,
  and at most two complete derived generations.
- SQLite is canonical for active membership, but not for semantic context.
- The source OptMem log remains canonical for append-only memory history.
- A changed active set can make the next wake slower because it requires a
  complete local rebuild before publication.
- Disk use temporarily includes two complete projection generations plus one
  bounded in-progress build. All are rebuildable from SQLite and the source
  history.
- Projection compression is not governed by the bounded historical
  `contx memory maintain` cycle; wake completes the new generation because a
  partial active projection is not a valid context.
- Exact active text can still contain poorly composed language. The correction
  prompt narrows this risk, while evaluation and user correction remain the
  appropriate semantic controls.
- Rebuilding a semantically poor active summary is explicit, local-model
  dependent, atomic, and safe to retry; it does not alter historical summaries.
- The ignored upstream OptMem executable and its redistribution gate are
  unchanged.

## Rollback or replacement strategy

The projection can be disabled without migrating source history: stop routing
`wake` to `memory-active/` and delete only the derived projection after an
explicit product decision. No `MemoryLink`, candidate, provenance, correction,
or historical OptMem entry must change.

Reopen this decision if evaluation demonstrates any of the following:

- projection rebuild latency or disk churn is materially harmful in daily use;
- large active sets cannot complete within the local-model resource budget;
- retained-generation pagination is insufficient for real concurrent use;
- exact active correction text still produces unacceptable semantic ambiguity;
- maintaining two upstream OptMem identities creates correctness or upgrade
  problems.

The next candidate is an active-aware `MemoryStore` implementation or a
reviewed OptMem evolution. Direct SQLite-generated semantic context remains a
separate product and architecture decision.
