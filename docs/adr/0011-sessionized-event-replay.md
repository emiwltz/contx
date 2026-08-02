# ADR 0011: Rebuild activity timelines as versioned session snapshots

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

J2 persisted one validated event per successful local-model transformation. J3
must turn those fragments into an intelligible activity timeline, support
correction, and compare processing versions without erasing earlier evidence.
The specification also requires sessionization to remain configurable and
testable.

Event rows already retain observation, model-transformation, and processing-run
provenance. They did not yet have a stable cross-version lineage, explicit
validity fields, a bounded event vocabulary, replay-window metadata, or an
append-only correction representation.

## Problem

Incrementally mutating one "current" event is incompatible with deterministic
replay: new evidence can change a session boundary, processing changes can alter
grouping, and an in-place correction would destroy the original interpretation.
Conversely, keeping every event from every run in one undifferentiated query
would produce duplicate timelines and present corrected and uncorrected facts as
equal truths.

## Options considered

1. Update events in place as new transformations arrive and overwrite fields
   when the user corrects them.
2. Keep immutable versioned events but attach corrections only to one concrete
   event identifier.
3. Treat every frozen-window build as a processing-run snapshot, derive a
   stable evidence lineage independent of the processing version, and append
   correction snapshots to that lineage.

## Decision

Use option 3.

An activity-timeline build selects an explicit timezone-aware half-open window
and persists its processing run, version, session gap, and maximum session
duration. The run links only the events belonging to that snapshot. Replaying
the same evidence and version reuses deterministic event identities while a
changed processing version creates comparable events without deleting the old
ones.

The initial configurable defaults are:

```text
session_gap_seconds = 600
max_session_duration_seconds = 7200
```

Evidence is grouped when it remains within those time bounds and has compatible
project, application, and bounded activity-type signals. A shared project may
span applications for project work, research, or planning. A project conflict,
incompatible activity type, excessive gap, or excessive duration starts a new
session.

The event vocabulary is a closed enum. Model labels such as coding, testing,
document editing, and project work normalize to `project_work` for sessionized
events. Unknown future labels fail into `other` while the original model labels
remain in event facts.

Every event has:

- a `lineage_key` derived only from its ordered source-observation identities;
- an idempotency key and UUID derived from the lineage and processing version;
- explicit activity and validity periods;
- the conservative maximum sensitivity and minimum confidence of its segments;
- unique project/entity attribution and source observations;
- foreign-key links to every supporting transformation and processing run.

A correction is an immutable, complete semantic snapshot containing event type,
summary, epistemic status, confidence, projects, entities, and validity. It does
not change the captured activity period, source observations, or sensitivity.
Each correction points to the stable lineage and may supersede exactly the
previous correction. Timeline reads apply only the latest correction.

A replay whose event retains the same source-observation set receives the same
lineage and therefore the correction. If a new algorithm changes the grouping,
its lineage changes and CONTX does not apply an ambiguous correction
automatically.

## Rationale

Processing-run snapshots distinguish persistent historical evidence from the
specific timeline a user selected. They avoid mutable "current row" state and
allow two algorithms to be compared directly. Stable evidence lineage solves
the common replay case without pretending that a correction to one grouping is
valid for a materially different grouping.

The 10-minute gap is a conservative initial continuity window for desktop work,
and the two-hour cap bounds accidental over-grouping. Both are configuration,
not a permanent product contract. The synthetic frozen-day fixture proves the
mechanism and edge cases; the real pilot must calibrate the values.

Aggregating already validated interpretations deterministically avoids a second
semantic model path, retains exact provenance, and permits offline replay after
raw image expiry.

## Consequences

- Old and new processing versions coexist in storage; callers must select a
  processing-run snapshot instead of querying all events as one timeline.
- Event rows gain explicit lineage and validity fields through a tested
  migration. Existing rows use their former idempotency key as initial lineage.
- Corrections remain durable and append-only even when their target event is no
  longer part of the selected processing version.
- Corrections may change epistemic status to `hypothetical`, so all three
  epistemic states are representable in the effective timeline.
- User correction text is durable by explicit action. It does not bypass the
  later candidate and memory validation gates.
- A correction never lowers the persisted sensitivity inherited from model
  evidence.
- Rebuilding currently scans a bounded maximum of 10,000 successful
  transformations. Exceeding that limit fails visibly rather than returning a
  partial timeline.
- Live incremental finalization is not inferred from this snapshot mechanism.
  The daemon may schedule bounded windows later, but J3 correctness is defined
  first through explicit frozen-window replay.

## Rollback or replacement

Session thresholds can change through configuration. An algorithm change must
use a new processing version and be evaluated beside the former snapshot. The
schema may later add explicit session records or a more efficient range query,
but it must preserve event lineage, processing-run selection, append-only
correction history, and observation/transformation provenance. Supersede this
ADR before adopting in-place mutation or automatic correction transfer across
different evidence groupings.
