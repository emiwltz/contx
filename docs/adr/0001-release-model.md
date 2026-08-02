# ADR 0001: Define the v0 and v1 release model

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

The founding specification used "V1" for the first complete product while its
last milestone described a daily-usable `0.1` release. That made the boundary
between a functional pilot product and a release-ready product ambiguous.

## Problem

Milestone gates, version labels, documentation, and acceptance claims need one
shared meaning. Without it, a narrow prototype could be presented as v0, or a
complete pilot could remain indefinitely described as unfinished V1 work.

## Options considered

1. Define v0 as Milestones 0 through 7 and v1 as Milestone 8 hardening.
2. Keep all Milestones 0 through 8 under the original V1 label.
3. Introduce a different roadmap and discard the existing milestones.

## Decision

v0 spans Milestones 0 through 7. It ends with a 7- to 14-day real pilot and
must satisfy the functional product acceptance criteria.

v1 is Milestone 8. It addresses pilot findings and adds release readiness:
installation, upgrade, uninstall, backup, restore, recovery, distribution,
security hardening, licensing, and durable public documentation.

The first vertical slice is labelled v0.0.1. Intermediate v0 labels remain
development milestones and do not create a stable public API contract.

Persisted user data is protected by explicit migrations from the first schema
despite the pre-v1 software label.

## Rationale

This separates product usefulness from public operational maturity. It gives
v0 a measurable end state without claiming production readiness before a real
pilot and hardening cycle.

## Consequences

- The former "V1 acceptance criteria" become the v0 acceptance criteria.
- Specification tags use `[DÉCISION v0]` for the initial product.
- J8 becomes the v1 milestone and cannot be used to hide incomplete v0
  behavior.
- A failed pilot does not automatically advance to v1.

## Rollback or replacement

A different release model requires a superseding ADR, updated specification,
changelog entry, and revised milestone acceptance mapping. It does not require
rewriting persisted data unless the replacement also changes data contracts.
