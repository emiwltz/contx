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
- [0012 — Preserve pattern, candidate, and decision replays as snapshots](0012-replayable-pattern-candidate-decisions.md)
- [0013 — Keep v0 memory corrections append-only and explicit](0013-append-only-memory-corrections.md)
- [0014 — Require explicit user adoption of agent proposals](0014-explicit-agent-proposal-adoption.md)
- [0015 — Build `wake` from an atomic active-only OptMem projection](0015-active-optmem-wake-projection.md)
- [0016 — Serve one same-origin loopback web interface](0016-loopback-web-interface.md)
- [0017 — Evaluate v0 with paired, content-minimized pilot evidence](0017-paired-real-pilot-evaluation.md)
- [0018 — Run semantic refreshes as bounded periodic local jobs](0018-bounded-periodic-context-refresh.md)
- [0019 — Capture only the policy-authorized focused window](0019-capture-only-focused-window.md)
- [0020 — Put the collector behind a signed native macOS host](0020-native-macos-host.md)
- [0021 — Use a native window for supervised prototype control](0021-native-window-control.md)
