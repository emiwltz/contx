# ADR 0018: Run semantic refreshes as bounded periodic local jobs

- **Status:** Accepted
- **Date:** 2026-08-13
- **Decision owner:** Emi

## Context

CONTX already persists the individual stages from authorized screenshots to
local-model transformations, events, frozen timelines, patterns, candidates,
decisions, historical OptMem memory, maintenance, and the active-only OptMem
projection. Those stages were replayable and tested independently, but the
production collection daemon only collected observations and the `contx
process` command stopped after event construction.

The v0 requires automatic local memory rather than a collection archive that
needs a developer to assemble its later stages manually. It also requires the
collector to remain responsive, local-model work to be bounded, interrupted
work to be retryable, and exact replay not to append duplicate memories.

## Problem

Running the local multimodal model in the AppKit collection loop could block
capture, pause controls, and the menu for up to the model timeout. Conversely,
an unbounded drain command could monopolize the machine and make shutdown
unpredictable. Deriving patterns while either the model or event queue is only
partially drained would publish a misleading snapshot. Blindly rebuilding the
same frozen window would also create different timeline-run identities and
could promote an equivalent candidate twice.

## Options considered

1. Interpret screenshots and derive memory synchronously inside the collection
   daemon.
2. Run one unbounded background processor until every stage and backlog is
   complete.
3. Run a separate short-lived local job periodically, process one bounded
   batch per invocation, defer derivation while either queue remains, and
   derive a stable aligned rolling window once both queues are empty.

## Decision

Use option 3 for v0.

The collection LaunchAgent remains a continuously supervised AppKit process.
A second LaunchAgent label, `io.contx.processor`, invokes
the dedicated `contx-processor` console entrypoint as a short-lived background
job. The continuous job invokes the corresponding `contx-collector`
entrypoint. Dedicated regular executable scripts avoid relying on the
environment's symlinked Python launcher and keep each launchd program explicit.
Neither job is installed or loaded without the same action-time approval
required for persistent real collection.

The two manifests are managed as one user-scoped lifecycle. A read-only
preflight checks existing permissions without prompting, the mandatory local
model, the reviewed OptMem checksum, the current database schema, both
entrypoints, and existing plist ownership. Activation stages only missing exact
`0600` manifests without overwrite. It first persists a pause, atomically
enables background collection, window titles and selective screenshots, and
then bootstraps both jobs in the current `gui/<uid>` domain. Collection resumes
only after both jobs and all three flags form a consistent active state. A
failed or interrupted attempt pauses before rollback, unloads only jobs loaded
by that attempt, restores exact prior configuration bytes, removes only
manifests created by that attempt, and restores an initially unpaused control
only after the rest of rollback succeeds. Deactivation persists an indefinite
pause before stopping either process, then disables all three feature flags
and removes only stopped manifests whose bytes still match CONTX's expected
content. Any modified, symlinked, unsafe, or concurrently changed file causes
a fail-closed result instead of replacement or deletion.

The processor takes its dedicated process lease before database setup and
holds it through engine teardown. Manual `contx process` and `contx refresh`
use the same lease. OptMem independently serializes appends, reads,
maintenance, and summary invalidation so an approved web action cannot mutate
the historical tree concurrently with the periodic job.

The initial configurable cadence is:

```text
model_interval_seconds: 900
analysis_interval_seconds: 7200
analysis_window_days: 14
comparison_period_days: 7
```

Every invocation processes at most one existing local-model batch and one
event batch. A failed mandatory stage reports a blocked result. A remaining
backlog reports an expected deferral and publishes no timeline, pattern,
candidate, memory, or active projection based on partial evidence.

When both queues are empty, the processor uses a UTC-aligned 14-day window and
a boundary seven days before its end. It then executes the complete chain:

```text
timeline snapshot
  -> pattern snapshot
  -> fused candidates
  -> transparent decisions
  -> provenance-backed historical OptMem promotion
  -> bounded progressive OptMem maintenance
  -> atomic active-only OptMem projection
```

The explicit `contx refresh --from ... --compare-at ... --until ...` command
uses the same orchestration for inspection and controlled replay. Production
refreshes reuse a successful timeline when its window, processing version,
session parameters, and complete transformation identity set are unchanged.
Downstream deterministic identities and memory-promotion idempotency then
prevent an exact retry from appending a second historical memory. Explicit
`contx timeline build` keeps its separate replay-run behavior for comparison
and audit.

## Rationale

Process separation keeps AppKit collection and immediate controls responsive
without introducing IPC or a second long-running service. A launchd interval
bounds retry delay, while the service-level batch bounds resource use and
preserves interruption recovery already present in model transformations.

Aligned windows give all worker invocations in one two-hour analysis interval
the same replay boundary. Requiring drained queues prevents a fast machine and
a slow machine from producing different partial memories merely because one
had processed more of the same backlog at a checkpoint. Reusing exact timeline
evidence fixes retry idempotency without erasing historical processing runs or
weakening changed-version replay.

## Consequences

- Automatic processing remains disabled whenever background collection is
  disabled.
- Pausing collection stops new observations but does not discard already
  authorized pending local work.
- A sustained backlog advances by one bounded batch every interval and remains
  visible in CLI, API, web, and pilot evidence instead of triggering an
  unbounded catch-up loop.
- New evidence in a later aligned window may produce a newer related memory.
  OptMem keeps those entries append-only and merges history progressively; the
  pilot still measures irrelevant or duplicate active results.
- Exact same-evidence retries reuse the production timeline and the existing
  memory link.
- The processor uses no remote provider and transports no user content outside
  the literal-loopback local-model boundary.
- Two LaunchAgent manifests must be installed, stopped, diagnosed, and removed
  together during the real pilot.
- `contx background status` is non-mutating. `activate` and `deactivate`
  require distinct exact confirmation phrases; neither confirmation is implied
  by installing the package or inspecting readiness.
- Battery/load-aware deferral is not inferred in this ADR; the pilot measures
  resource cost before that policy is added.

## Rollback or replacement strategy

The processor LaunchAgent can be unloaded independently without changing
persisted observations or the collection daemon. Manual `contx process` and
`contx refresh` remain available for diagnosis. Cadences and rolling-window
parameters can be changed through private configuration and recorded replay
parameters without a schema migration.

If the pilot shows that one batch every 15 minutes cannot keep raw work inside
the 48-hour lifetime, or that periodic startup costs are excessive, replace
the short-lived job with a supervised processing service. That replacement
must keep the same bounded stage services, backlog-before-derivation gate,
aligned replay parameters, exact retry idempotency, local-only model boundary,
and observable interruption semantics.
