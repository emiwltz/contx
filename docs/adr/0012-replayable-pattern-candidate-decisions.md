# ADR 0012: Preserve pattern, candidate, and decision replays as snapshots

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

J3 produces immutable, correction-aware activity-timeline snapshots from
semantic events created by the mandatory local multimodal model. J4 must infer
multi-event regularities, turn useful inferences into memory candidates, and
compare candidate policies without erasing the evidence or the result of an
earlier policy.

The v0 specification requires conservative support for project recurrence,
project resumption, temporal changes, validity, fusion, scoring, deferral,
rejection, explicit provenance, and transparent decisions. The score weights
and thresholds are hypotheses that must remain replaceable after synthetic and
real evaluation.

## Problem

A mutable "current pattern" or candidate status cannot explain what a former
algorithm concluded. It also cannot compare two thresholds over exactly the
same candidate set. Creating one candidate for every pattern would retain
history but generate redundant memories and obscure that several inferences
describe one project.

Pattern detection also needs a clear relationship with the local-model
requirement. Deterministic rules must not become an alternate path that infers
semantics directly from screenshots or metadata.

## Options considered

1. Mutate current pattern and candidate rows whenever rules or thresholds
   change.
2. Store immutable patterns but overwrite one terminal state on each candidate.
3. Persist each stage as a versioned processing-run snapshot, fuse compatible
   patterns into candidates, and append a separate decision for every policy
   replay.

## Decision

Use option 3.

The J4 processing graph is:

```text
selected activity-timeline run
    -> pattern run + PatternBuild
        -> immutable Pattern rows + event links
            -> candidate run + CandidateBuild
                -> fused MemoryCandidate rows + pattern links
                    -> evaluation run + CandidateEvaluationBuild
                        -> append-only CandidateDecision rows
```

Each build stores its source processing-run identifier and all content-free
parameters required to interpret that replay. Same-version pattern and
candidate replays reuse deterministic records and link them to the new run. A
changed processing or scoring version produces new identities without deleting
the old ones. Evaluation decisions include their run identity so separate
threshold runs coexist even when they evaluate the same candidate.

The first pattern engine detects:

- project recurrence with at least two distinct events;
- project resumption after a configurable inactivity gap, initially 24 hours;
- activity increase, decrease, or new repeated activity across an explicit
  comparison boundary, initially using a 1.5 ratio;
- a default 30-day validity period.

Patterns cannot contain fewer than two unique source events. Their confidence
uses the least confident supporting event, their sensitivity uses the most
restrictive supporting event, and every pattern has foreign-key-backed event
provenance. Expiration is resolved against time without mutating the historical
pattern row.

Candidate production groups patterns by case-insensitive project identity and
fuses all compatible patterns into one short autonomous candidate. The
candidate cites every source pattern, and those patterns retain the underlying
events. The initial score is explicit and persisted by version:

```text
+ 0.22 utility
+ 0.18 importance
+ 0.16 durability
+ 0.14 novelty
+ 0.15 recurrence
+ 0.15 confidence
- 0.15 ambiguity
- 0.20 redundancy
- 0.10 sensitivity risk
```

The initial worker policy accepts candidates at `0.65` or above. It rejects
missing multi-event pattern provenance, sensitivity that is ineligible for
durable memory, excessive redundancy, and below-threshold scores. It defers
insufficient confidence and excessive ambiguity. The ordered reason code is
stored with every non-accepted decision. Policy runs persist the acceptance,
minimum-confidence, maximum-ambiguity, and maximum-redundancy thresholds.

These deterministic rules operate only on the semantic event layer already
produced by the mandatory local LLM. They are aggregation and policy logic,
not a fallback semantic extractor and not a replacement for the model.

## Rationale

Snapshot provenance makes rule and threshold comparisons auditable and
reversible. A fused project candidate prevents several correlated patterns from
becoming several redundant memories, while the pattern links preserve the
reasons for the fusion. Conservative confidence and sensitivity propagation
prevent aggregation from laundering uncertainty or sensitivity.

Append-only decisions separate the identity of a proposal from a particular
policy's verdict. This is necessary for calibration: the same candidate can be
accepted at one threshold and rejected at another without contradictory
in-place mutation.

## Consequences

- Callers must select explicit pattern, candidate, and evaluation runs.
- The database stores additional snapshot links and build metadata; old and new
  versions intentionally coexist.
- Candidate rows keep the legacy mutable status only for the v0.0.1 vertical
  slice. J4 policy comparisons use `CandidateDecision` and do not mutate it.
- Sensitive content is not redacted before the local LLM, but candidates marked
  sensitive or forbidden remain ineligible for durable memory.
- J4 records accepted decisions but does not append them to `MemoryStore`; the
  complete provenance-aware memory lifecycle is J5.
- Initial weights and thresholds are synthetic-evaluation baselines, not stable
  product contracts.

## Rollback or replacement strategy

A replacement engine or scoring policy receives a new version and writes a new
snapshot against the same upstream run. It can be evaluated beside the former
version before selection. Tables can be removed only through an explicit data
migration after their historical provenance is exported or intentionally
discarded. No in-place rewrite of pattern, candidate, or decision history is
required.
