# ADR 0013: Keep v0 memory corrections append-only and explicit

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

ADRs 0002 and 0006 establish that `MemoryStore`, initially backed by upstream
OptMem, produces the semantic context directly while structured provenance and
correction metadata remain in SQLite. OptMem stores an append-only raw log and
a rebuildable compression tree, but it does not natively understand active,
superseded, or corrected records.

The J5 experiment used the reviewed upstream OptMem snapshot and the mandatory
local `gemma4:e4b-it-qat` model. It established that recent entries which fit
the wake budget are returned as raw chronological leaves, even when a summary
already exists. `recall` searches the raw log and `zoom` can expose raw or
historical tree nodes. When the wake budget selects the rebuilt summary, a
correction-aware compression prompt can retain the current fact and remove the
contradicted one.

## Problem

CONTX must correct a durable memory without deleting its evidence or creating
a second semantic context source. A recent raw OptMem wake can legitimately
contain both the old line and its correction, so the format must make their
authority unambiguous. The solution must also survive interruption between the
SQLite and OptMem stores, preserve transitive provenance, require the local
model, and remain simple enough to evaluate during v0.

## Options considered

1. Keep upstream OptMem append-only, append a self-contained explicit
   correction, and store status, supersession, provenance, and model audit in
   the existing SQLite sidecar.
2. Make SQLite the canonical active-memory set and continuously rebuild a
   filtered OptMem projection containing only active records.
3. Modify or fork OptMem now to add native supersession, active filtering, and
   correction-aware queries.

## Decision

Use option 1 for v0.

An explicit user correction targets one active `MemoryLink` and supplies the
authorized replacement fact. The mandatory loopback-only local model receives
the original memory and that replacement, then returns one short autonomous
current fact through a strict JSON schema. CONTX, rather than the model,
prepends the protocol marker `Correction: ` before appending the line to
OptMem. There is no deterministic semantic extraction or no-model correction
path.

SQLite records:

- a deterministic correction candidate and memory-link identity;
- `pending`, `active`, and `superseded` link states;
- one unique direct `supersedes` relation per memory;
- the complete inherited pattern, event, and observation provenance;
- the model endpoint, name, digest, prompt and output-schema versions, timing,
  and a hash of the authorized replacement without duplicating it in the audit
  record.

The correction is staged in SQLite before the OptMem append. Only after the
idempotent append succeeds does one SQLite transaction activate the correction,
mark the target superseded, and mark the candidate stored. An interruption
leaves the original active and the correction pending; replay uses the
persisted correction text and the same append key, without calling the model or
creating another raw entry.

The v0 read semantics are explicit:

- `wake` is chronological. A newer line beginning with `Correction:` is
  authoritative over the older claim it contradicts, even when both recent
  raw lines are visible.
- correction-aware maintenance progressively rebuilds summaries. A summary
  must preserve the corrected current fact and remove every contradicted
  fragment rather than rewriting the obsolete claim as history or a
  transition.
- `recall` and `zoom` are historical inspection tools. They may expose both an
  original record and its correction and are not active-only queries.
- SQLite owns structured current status and provenance, but it does not inject
  a filtered semantic projection beside OptMem output.
- an agent may not invoke `contx correct` without an explicit user instruction.

The exact same correction request is idempotent. A later change must target the
currently active successor, producing a linear correction chain.

## Rationale

This is the smallest design that satisfies the current requirements while
retaining upstream OptMem behavior. It keeps the final semantic context in one
place, preserves raw history, avoids a continuously synchronized second memory
representation, and leaves OptMem replaceable behind `MemoryStore`.

The explicit marker makes the raw recent-window semantics understandable
without hiding that OptMem is chronological. The strengthened local
compression prompt handles the older summarized window. SQLite provides the
transactional and audit capabilities OptMem lacks without becoming another
context builder.

## Consequences

- Recent `wake` output can contain both lines; consuming agents must follow the
  explicit correction protocol.
- `recall` and `zoom` cannot be presented as current-state-only views.
- Correct summary quality depends on the mandatory local model and remains a
  regression and pilot measurement.
- The raw OptMem log remains a complete exportable history.
- The database gains a correction-build audit table and a uniqueness
  constraint that prevents branching direct successors.
- Upstream OptMem remains unmodified, reducing maintenance and future update
  cost.
- The local model being unavailable blocks new corrections but does not alter
  existing memory.

## Rollback or replacement strategy

Reopen this decision if fixed correction fixtures or real-agent evaluation
show any of the following reproducibly:

- an agent treats a superseded raw claim as equally current despite the
  correction marker and instruction contract;
- correction-aware rebuilding retains a contradicted claim as current;
- correction chains consume enough wake budget to hide materially relevant
  current context;
- a required product surface needs an active-only semantic view that cannot be
  expressed safely as historical inspection.

The next implementation is option 2 before an OptMem fork: derive an
active-only projection from SQLite into a separately identified OptMem store,
rebuild it atomically, and keep the append-only source history for audit and
export. Existing `MemoryLink` identities, linear `supersedes` chains, candidate
text, and provenance provide the migration source. A native OptMem evolution
is considered only if the projection itself creates demonstrated correctness
or operational problems.
