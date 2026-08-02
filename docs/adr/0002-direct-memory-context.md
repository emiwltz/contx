# ADR 0002: Deliver semantic context directly from MemoryStore

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

The founding invariants state that OptMem, or its successor, is the final
memory context and that v0 has no separate Context Builder. The initial visual
diagram nevertheless showed a context interface adding recent activity and
task-specific information after OptMem.

## Problem

Adding events or current activity after memory would create a second semantic
context source. It would weaken provenance guarantees and make it unclear
whether an agent received validated memory, recent evidence, or an untracked
mixture of both.

## Options considered

1. Return `MemoryStore.wake()` as the semantic context without enrichment.
2. Add a Context Builder that mixes memory, recent events, and task-specific
   retrieval.
3. Return direct memory plus separate technical operational status.

## Decision

Use options 1 and 3.

`MemoryStore.wake()` is the only semantic memory context returned by
`contx wake`. The agent gateway may invoke, transport, and paginate that
output. It may report technical status separately, including unavailable
memory, pending maintenance, or processing freshness.

The gateway must not inject events, observations, current activity, or a
task-specific semantic summary into the memory body.

## Rationale

One semantic source is easier to audit, correct, replace, budget, and explain.
It preserves the specification's distinction between enriched evidence and
durable memory.

## Consequences

- The architecture diagram replaces "Context Interface" with "Agent Gateway".
- Direct memory delivery is covered by contract tests.
- Recent events remain inspectable through product interfaces but are not
  silently included in `wake`.
- Task-specific context construction remains outside v0.

## Rollback or replacement

Introducing a Context Builder requires a superseding ADR and an explicit
specification change. The existing agent gateway can remain as transport, but
the new semantic source and its provenance, privacy, correction, and budget
contracts must be designed and migrated deliberately.
