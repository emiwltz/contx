# ADR 0005: Start collection on demand before introducing a daemon

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

CONTX must eventually collect activity in the background, but it also promises
immediate pause, visible state, exclusions, and user control. The first
vertical slice does not yet have a menu-bar control or local web UI.

## Problem

Starting with an unattended daemon would create privacy and operational risk
before the user can reliably see, pause, or diagnose collection.

## Options considered

1. Start with explicit one-shot CLI execution, then add one supervised daemon
   after pause, exclusions, and visible state exist.
2. Install a launchd daemon in J0 and control it only through the CLI.
3. Build the web UI before validating the collection pipeline.

## Decision

Use option 1.

v0.0.1 exposes on-demand application services through the CLI. No launchd job
or persistent polling is installed. v0.1 introduces one supervised local
daemon only after pause, exclusions, and a visible control path are proven.

Live screenshots, persistent background collection, and new macOS permissions
require explicit action-time approval before activation.

## Rationale

The sequence validates the real platform boundary without running invisible
collection. The one-shot command and later daemon use the same application
services, so the first implementation is not a disposable pipeline.

## Consequences

- The first live collector reads only active application metadata on explicit
  invocation.
- Window titles are disabled by default in the first vertical slice.
- Screenshots and OCR are absent from v0.0.1.
- A focused experiment will decide whether the visible controller uses
  Python/PyObjC or a small Swift helper.

## Rollback or replacement

If one-shot execution cannot exercise a required macOS API faithfully, a
bounded foreground process may replace it through a superseding ADR. The
collector and application contracts remain stable so the daemon or helper can
be replaced independently.
