# Architecture Decision Records

Architecture Decision Records (ADRs) capture consequential CONTX decisions
that must survive individual implementation sessions.

## Statuses

- `Proposed`: under discussion and not authorized for implementation.
- `Accepted`: approved and authoritative.
- `Superseded`: replaced by a later ADR.
- `Rejected`: considered and intentionally not selected.

## Required structure

Every ADR records:

1. context;
2. problem;
3. realistic options;
4. decision;
5. rationale;
6. consequences;
7. rollback or replacement strategy.

## Index

- [0001 — Define the v0 and v1 release model](0001-release-model.md)
- [0002 — Deliver semantic context directly from MemoryStore](0002-direct-memory-context.md)
- [0003 — Use the Python persistence foundation](0003-python-persistence-foundation.md)
- [0004 — Use native macOS runtime data locations](0004-macos-runtime-data-layout.md)
- [0005 — Start collection on demand before introducing a daemon](0005-initial-process-model.md)
- [0006 — Integrate OptMem behind MemoryStore](0006-optmem-integration.md)
- [0007 — Keep the v0.1 macOS controller in Python and add Quartz](0007-macos-controller-and-quartz.md)
- [0008 — Use ApplicationServices for authorized window titles](0008-applicationservices-window-titles.md)
- [0009 — Require a local multimodal model in v0](0009-mandatory-local-model.md)
- [0010 — Select Gemma 4 E4B QAT as the v0 default local model](0010-gemma4-e4b-default.md)
- [0011 — Rebuild activity timelines as versioned session snapshots](0011-sessionized-event-replay.md)
