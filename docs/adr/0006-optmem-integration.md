# ADR 0006: Integrate OptMem behind MemoryStore

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

The founding specification selects OptMem, or an evolution of it, as the final
memory context. A local clone exists at upstream commit
`1fb164cf39028047781f72ac3bb1e5a691c1dcb0`. Emi has authorized CONTX to use
and evolve the complete codebase and knows its creator.

The local and current public upstream snapshots do not contain a license file.

## Problem

CONTX needs to prototype and evaluate the real OptMem behavior without making
its domain depend on OptMem's files or distributing third-party code without
permission that downstream users can rely on.

## Options considered

1. Use the local upstream code behind `MemoryStore`, preserve provenance, and
   gate bundling on redistributable permission.
2. Reimplement OptMem immediately.
3. Block all memory integration until public licensing is resolved.
4. Import OptMem directly into CONTX modules without an adapter.

## Decision

Use option 1.

The ignored local clone is the development reference. CONTX accesses it only
through a typed `MemoryStore` adapter and uses an explicit CONTX-owned memory
directory. Provenance and correction metadata remain in SQLite.

Once an upstream license or written grant permits use, modification, and
redistribution, the complete reviewed snapshot may be tracked under
`third_party/optmem/`. Upstream code and CONTX modifications remain
distinguishable.

The final upstream, sidecar, or evolved OptMem strategy remains an experiment
until the v0 pilot.

For the v0.0.1 process boundary, the adapter pins the reviewed `memo`
executable by SHA-256, passes only a small allowlisted environment, bounds
runtime and stdout/stderr, enforces UTF-8 and the 280-byte entry limit, and
protects its CONTX-owned state with local-user permissions.

OptMem and SQLite cannot share a transaction. The adapter therefore keeps an
atomic, private idempotency sidecar keyed by a digest of the CONTX candidate
key. If interruption occurs after OptMem appends but before the sidecar or
SQLite link commits, exact OptMem recall recovers the backend identity on the
next pipeline run. Technical identifiers are not injected into semantic memory
text. This recovery strategy is covered by failure-injection tests and may be
replaced when an evolved backend offers native idempotency metadata.

## Rationale

This exercises the actual memory algorithm early while preserving a stable
exit boundary. The rights gate protects future users rather than relying on
project-owner knowledge that is not present in distributed artifacts.

## Consequences

- Public release artifacts cannot bundle OptMem without redistributable rights.
- CI cannot silently depend on the ignored local clone.
- Test doubles cover deterministic unit behavior.
- Replay compares upstream and evolved behavior before a fork is selected.
- OptMem's compression maintenance becomes an explicit application concern.

## Rollback or replacement

`MemoryStore` permits replacement with an independently implemented backend.
Before removal, the replacement must import or migrate canonical memory,
preserve current corrections, reproduce wake behavior within the configured
budget, and pass the same replay suite.
