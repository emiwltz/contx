# ADR 0014: Require explicit user adoption for agent proposals

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

Agents can identify durable context while working, but they must not own final
memory. The proposal inbox introduced for J5 already validates the submitting
role, requires a durable provenance reference, and keeps every submission
outside OptMem. It did not yet define how a user-approved proposal crosses the
final-memory boundary.

CONTX v0 uses upstream OptMem as an append-only semantic store and SQLite as a
structured provenance and lifecycle sidecar. The local model is mandatory for
semantic decisions, and the product intentionally has no deterministic
semantic-extraction or no-model path.

## Problem

The adoption path must remain simple and close to OptMem while preventing an
agent from silently promoting its own claims. It must verify support and
semantic duplication, preserve complete provenance, expose a human-readable
decision, and survive interruption between SQLite and OptMem without duplicate
memory or repeated model work.

## Options considered

1. Let a primary agent append a proposal directly after providing a reference.
2. Require an explicit user adoption command, validate the unchanged proposal
   with the mandatory local model, and use the existing staged SQLite-to-OptMem
   append protocol.
3. Add a separate canonical editing and approval interface before any proposal
   can reach OptMem.

## Decision

Use option 2 for v0.

`contx propose` only creates an inbox record. A pending proposal can cross the
final-memory boundary only when the user explicitly runs:

```text
contx proposals adopt <proposal-uuid>
```

At adoption time CONTX revalidates that the referenced event, active pattern,
or active memory still exists and remains eligible for durable memory. It then
sends the proposed line, a bounded representation of that reference, and a
bounded relevant selection of active memory to the configured loopback-only
Gemma model. The validator uses one strict support decision and, when an
otherwise accepted proposal has active-memory context, one focused structured
equivalence decision. The resulting outcomes are:

- `accepted / supported_novel`;
- `rejected / unsupported_by_reference`;
- `rejected / duplicate_active_memory`;
- `rejected / conflicts_with_active_memory`; or
- `deferred / ambiguous_reference`.

An accepted decision must meet the configured confidence threshold. Gemma does
not rewrite or extract a replacement line. CONTX appends the exact submitted
proposal, preserving a direct and inspectable relationship between the user's
choice, the proposal record, and OptMem output.

For an accepted proposal, SQLite stores:

- a deterministic accepted candidate sourced from the proposal;
- a pending memory link with transitive pattern, event, and observation
  provenance inherited from the referenced record;
- model endpoint, name, digest, prompt and output-schema versions;
- decision, reason code, confidence, timing, selected active-memory count, and
  content-free fingerprints of the reference and active-memory input.

The candidate, audit record, and pending link are committed before the OptMem
append. After the idempotent append succeeds, one SQLite transaction marks the
candidate stored, activates the memory link, and marks the proposal adopted.
If interruption occurs after the append, replay uses the staged candidate and
append key without invoking Gemma or writing another OptMem line.

Rejected and deferred model decisions are audited and perform no final-memory
write. `contx proposals reject` records an explicit user rejection without
invoking the model. Proposal state transitions are irreversible in v0.

Primary agents may list, inspect, and submit proposals, but they may not run
`proposals adopt`, `proposals reject`, or `correct` without an explicit user
instruction. Subagents may not use the memory commands or submit proposals.

## Rationale

This keeps the product close to OptMem's simple append-only behavior while
retaining the control boundary that motivated the proposal inbox. Appending the
unchanged line eliminates a second generative transformation, makes user review
meaningful, and avoids creating another semantic representation. The local
model performs the judgment that cannot be reduced safely to string rules:
support, ambiguity, semantic duplication, and conflict.

The two-phase persistence protocol reuses the proven correction and promotion
shape without introducing a second memory system. SQLite remains metadata and
provenance, not an alternate context builder.

Exact normalized equality is additionally enforced as a narrow structural
postcondition after the mandatory model call. This is not semantic extraction
or a no-model adoption path; it prevents an already-visible identical line
from being appended if the model fails the simplest duplicate check. Semantic
paraphrases and conflicts remain local-model decisions.

## Consequences

- Every adopted agent memory has an explicit user action and a local-model
  audit.
- Model unavailability blocks new adoption but does not affect existing memory
  or proposal inspection.
- The proposal line must already be autonomous, single-line, and within the
  OptMem byte limit; adoption does not repair it.
- Semantic duplicate checking is bounded. v0 selects at most 32 relevant active
  lines and 8 KiB for the model prompt, prioritizing exact and lexical overlap.
- A rejected or deferred proposal is terminal. A revised claim is submitted as
  a new proposal, keeping history clear.
- The database gains an adoption-build table linked to both the proposal and,
  for accepted decisions, its memory candidate.

## Revisit conditions

Reopen this decision if pilot evidence shows that users routinely need to edit
proposals before adoption, the bounded active-memory selection misses material
semantic duplicates, proposal text quality makes unchanged append unsuitable,
or the CLI approval step creates unacceptable friction. The first extension is
an explicit preview/edit-and-resubmit flow; it does not grant agents direct
write authority or make SQLite a second semantic context source.
