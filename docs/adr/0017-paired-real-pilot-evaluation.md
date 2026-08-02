# ADR 0017: Evaluate v0 with paired, content-minimized pilot evidence

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

The v0 exit gate is product benefit, not pipeline activity. J7 must compare an
agent with and without CONTX for project resumption, recent-period
understanding, and change detection over a 7- to 14-day real pilot. It must
also measure privacy invariants, memory quality, context size, wake latency,
and daily resource cost.

The comparison itself creates sensitive evidence. Ground truth can contain
project names, decisions, blockers, and priority changes. Full agent answers
can repeat more private material than the memory under review. A loose diary
and an unstructured retrospective would be difficult to reproduce, easy to
bias, and needlessly broad in retained content.

## Problem

CONTX needs an evaluation contract that:

- compares equivalent tasks rather than unrelated conversations;
- distinguishes missing evidence from a failed gate;
- exposes the provisional scoring rule before results are known;
- retains enough detail to audit a score without copying raw observations;
- measures invariants separately from subjective usefulness;
- cannot pass if any one of the three target behaviors is unevaluated;
- leaves CPU, memory, and detection thresholds explicit instead of inventing
  acceptable daily-use limits on the user's behalf.

## Options considered

1. Use paired trials with the same prompt key, cutoff, relevant ground-truth
   identifiers, and explicit `with_contx` or `without_contx` condition.
2. Compare two unpaired periods before and after enabling CONTX.
3. Rely on a qualitative retrospective after daily use.
4. Store full prompts, answers, screenshots, and model traces in an evaluation
   database.

## Decision

Use option 1 with content-minimized, private files.

`contx pilot prepare` creates an isolated `0700` evidence directory with
`0600` files. It does not enable collection, request a permission, install a
LaunchAgent, or start the pilot. Inputs are versioned strict JSON/JSONL
records:

- a manifest fixes the 7- to 14-day interval and thresholds;
- ground truth records intentional short summaries and their relevant
  scenarios;
- paired trial scores refer to the same prompt and ground-truth identifiers;
- technical snapshots record cumulative quality and invariant counters;
- resource samples record CPU, RSS, detection latency, and disk baselines;
- privacy incidents contain only a category, severity, redacted summary, and
  resolution time.

Full prompts, full agent answers, window titles, screenshots, secrets, and raw
paths do not belong in this workspace. The generated report includes only
aggregate scores and identifiers.

For each condition and scenario, the initial quality score is:

```text
0.40 * ground-truth coverage
+ 0.30 * factual precision
+ 0.15 * normalized relevance score
+ 0.15 * normalized synthesis score
```

Coverage is retrieved relevant truth divided by relevant truth. Factual
precision is supported claims divided by supported plus materially false
claims; an answer with neither scores `1.0` for this term but still receives no
coverage benefit. Human relevance and synthesis scores use a documented 1-5
rubric.

A target behavior demonstrates provisional benefit only when:

- its paired quality score improves by at least 0.05; and
- its materially false claim rate does not regress.

All three behaviors must demonstrate benefit. In addition, important-event
recall must exceed 50%, materially false accepted memories must remain below
10%, provenance coverage must be 100%, raw-retention, excluded-capture,
remote-transport, and synthetic-secret violations must remain zero, context
must fit the configured budget, and no critical privacy incident may remain
unresolved.

The 5-point behavior delta and score weights are provisional v0 decision rules,
not timeless product truths. They are fixed before the pilot so results cannot
move the goalposts. Pilot evidence may justify a later ADR revision.

Combined measured-process CPU p95, combined RSS p95, and detection-latency p95
limits have no default.
Emi must approve them before the pilot decision; until then the resource gate
is `review`, never `pass`. The existing raw-disk target remains below 5 GiB.

## Rationale

Paired prompts reduce variation from task framing and make the actual memory
contribution visible. Stable truth identifiers permit coverage calculation
without putting private summaries in the report. Separating invariant gates
from a composite usefulness score prevents a quality improvement from
compensating for a privacy failure.

JSONL is simple to append during a short single-user pilot and remains
inspectable without a database migration. Strict schemas, size bounds,
symlink refusal, private permissions, duplicate-ID checks, pair validation,
cumulative-counter validation, and cutoff-aware reports make errors visible.

## Consequences

- Ground truth remains intentionally user-authored; CONTX must not generate
  its own answer key from the data being evaluated.
- Scoring remains a human review activity. CONTX calculates metrics but does
  not pretend to decide whether a claim is materially false or irrelevant.
- Partial pairs are treated as incomplete and excluded from comparison.
- Evidence dated after a requested report cutoff is ignored.
- Resource data and several human-reviewed memory counters still need a
  controlled collection procedure; implementing the schema is not evidence
  that those measurements occurred.
- The pilot report can fail honestly. Failure analysis may change the v0
  strategy and does not authorize proceeding to v1.
- The final OptMem strategy remains open until the pilot compares the accepted
  active-projection design against observed correction quality, latency, and
  usefulness.

## Rollback or replacement

The pilot workspace is an evaluation artifact, not product memory and not a
public interchange format. A future evaluator may replace JSONL or the scoring
formula by incrementing its schema version and providing an explicit converter
for any evidence still needed. Removing the evaluator does not migrate SQLite
or OptMem state. The original private evidence can be retained for audit or
deleted with explicit user authorization.
