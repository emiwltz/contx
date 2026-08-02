# ADR 0004: Use native macOS runtime data locations

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

CONTX stores durable structured state, temporary raw artifacts, logs, memory,
configuration, and exports. The README initially proposed one `~/.contx/`
directory, while the product targets macOS and requires different retention
and backup behavior for raw and durable data.

## Problem

A single directory makes it easier to accidentally back up raw captures,
confuse cache deletion with memory deletion, or leave state behind during
uninstall. Tests also need complete isolation from real user data.

## Options considered

1. Use native macOS Application Support, Caches, and Logs directories.
2. Keep all runtime data under `~/.contx/`.
3. Make every path mandatory configuration with no production default.

## Decision

Use option 1.

Defaults:

```text
~/Library/Application Support/CONTX/
    config.toml
    contx.db
    memory/
    exports/

~/Library/Caches/CONTX/
    raw/
    processing/

~/Library/Logs/CONTX/
```

One explicit development override redirects all roots to an isolated location.
Tests always use temporary roots.

## Rationale

The split reflects lifecycle: durable state belongs in Application Support,
temporary raw material belongs in Caches, and diagnostic output belongs in
Logs. It also makes backup, purge, export, and uninstall behavior explicit.

## Consequences

- Directories and state files use restrictive local-user permissions.
- Raw artifacts are excluded from durable export and backup paths by design.
- The application maintains one authoritative path resolver.
- Full deletion can enumerate known CONTX-owned roots before taking action.
- Moving a production root later requires a migration.

## Rollback or replacement

A future layout change requires a migration that detects the old layout,
moves data atomically where possible, preserves permissions, reports partial
failure, and remains idempotent on retry.
