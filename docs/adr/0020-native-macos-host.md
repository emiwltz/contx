# ADR 0020: Put the collector behind a signed native macOS host

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decision owner:** Emi

## Context

ADR 0007 kept the menu-bar controller in the Python collector until target-Mac
evidence justified a second native boundary. The PyObjC menu itself worked, but
two supervised activation attempts exposed a different constraint: a raw
Python LaunchAgent had no dedicated application identity and did not receive a
usable Screen Recording attribution. The exact collector remained fail-closed,
and both attempts were rolled back without retained raw data or backlog.

A separately authorized native identity probe then established a dedicated
LaunchServices identity without requesting permission or collecting activity.
A later, separately gated experiment used one unchanged ad-hoc-signed bundle
throughout the request and verification sequence. Both that native bundle and
its digest-pinned Python child observed the same existing Screen Recording
grant. Neither process read a title or pixel, and the experiment was completely
removed afterward.

The target Mac now has Xcode 26.6 and a valid Apple Development signing
identity. The product identity `CONTX` and bundle identifier
`io.contx.desktop` were approved for the local v0 prototype. Installation,
launch, permission requests, persistence, and collection remain separate
action-time gates.

## Problem

The v0 collector needs one stable macOS identity for its visible status,
privacy permissions, and supervised lifecycle without moving collection,
policy, persistence, or memory logic into a second implementation. The design
must also prevent an external or substituted Python command from being launched
under the trusted native identity.

The existing Python daemon owns its own `NSStatusItem`, so simply wrapping that
process would create two controls with potentially inconsistent state. The
native host also needs a narrow way to read and change durable pause state
without parsing human-oriented CLI output or coupling Swift directly to the
SQLite schema.

## Options considered

1. Keep launching the raw Python collector and grant Screen Recording to the
   shared interpreter identity.
2. Rewrite the collector and capture pipeline in Swift.
3. Add a signed native menu-bar host that launches the existing Python
   collector as a digest-pinned child and uses a versioned, content-free Python
   control command for status, pause, and resume.
4. Let a native host read and mutate the CONTX SQLite database directly.

## Decision

Use option 3.

`CONTX.app` is a minimal AppKit accessory application with bundle identifier
`io.contx.desktop`. It owns the only visible menu-bar item, verifies a sealed
runtime contract before launching any child, starts the existing collector as
one exact subprocess, and stops that child when the host terminates. It does
not read window metadata, pixels, raw observations, semantic events, or memory.

The runtime contract is a versioned property-list resource sealed by the app's
code signature. It contains absolute paths and SHA-256 digests for exactly two
commands:

- the collector entrypoint;
- an internal `contx-native-control` entrypoint.

The host revalidates the relevant executable before each use, passes a minimal
environment, rejects symlinks and changed bytes, bounds control-command runtime
and output, and never accepts an arbitrary executable or argument from a menu
action. The external Python environment remains development-only until a
separate packaging decision makes the runtime closure immutable enough for
activation.

`contx-native-control` exposes a versioned JSON status containing only
configuration, pause, and content-free process-health state. It delegates
pause and resume to the existing `CollectionControlService`. Resume fails
closed unless background collection is enabled and the collector lease proves
that the daemon is running. It never requests a privacy permission or reads
user activity.

When the collector sees the exact `CONTX_NATIVE_HOST=1` environment marker, it
keeps its AppKit run loop but uses a non-visible accessory controller instead
of creating a second status item. Direct Python execution keeps the existing
menu as a diagnostic fallback until native-host activation is accepted and
verified.

The managed continuous LaunchAgent invokes only
`~/Applications/CONTX.app/Contents/MacOS/CONTX`. It never invokes the raw
collector entrypoint. Read-only preflight may render this future absolute path
before installation, but staging and loading both revalidate the native host
and periodic processor immediately before changing state.

The native host build:

- uses the Swift compiler and system frameworks supplied by Xcode;
- adds no third-party package;
- requires an explicit Screen Recording usage description instead of inventing
  one;
- signs with an explicitly selected identity and hardened runtime;
- verifies the complete staged bundle before atomically publishing it;
- never launches, registers, installs, or requests permission as part of a
  build.

This decision authorizes repository implementation, Apple Development signing,
and synthetic tests only. It does not authorize:

- installing or launching `CONTX.app`;
- registering it with LaunchServices;
- installing or loading a LaunchAgent;
- requesting Accessibility or Screen Recording permission;
- reading live titles or pixels;
- enabling or resuming collection;
- starting the real-data pilot.

## Rationale

The native host gives TCC and LaunchServices the stable identity that the raw
LaunchAgent lacked while preserving the already tested Python collection and
privacy pipeline. A narrow control command keeps Swift independent of database
schema and Python independent of AppKit menu ownership. Digest pinning and a
minimal environment prevent the host from becoming a general-purpose launcher.

A Swift rewrite would duplicate the highest-risk policy and collection logic.
Direct SQLite access from Swift would create a second persistence contract and
make migrations unsafe. Granting the shared Python interpreter would broaden
permission attribution beyond CONTX and has already failed in the supervised
LaunchAgent context.

## Consequences

- Native menu state and Python collection run in separate processes, so their
  contract and failure behavior are explicit.
- The Python daemon still owns the audited session, exclusions, capture source,
  retention, purge, and lease.
- A development bundle can be compiled and signed before a packaging tool is
  selected, but it is not activation-ready while its Python runtime remains
  external and mutable.
- A final permission phrase, runtime packaging choice, authorized installation,
  and exact target-Mac launch test remain separate gates.
- The native host adds a small Swift source and build path, but no Xcode project
  or third-party runtime dependency is required for the first compile-only
  artifact.
- The old PyObjC status item remains available only when the collector is run
  outside the native-host contract.

## Rollback or replacement strategy

Before activation, rollback is deletion of generated build artifacts and
reversion of this repository implementation; no installed state exists.

After a future authorized installation, disabling and unloading the owned
LaunchAgent must occur before removing the app. The Python CLI remains the
independent pause and inspection fallback. Do not restore permission attribution
to a generic Python interpreter.

If external-runtime sealing proves too slow or too fragile, replace only the
packaging layer with an accepted self-contained Python packager. If the native
host lifecycle itself proves unreliable, retain the versioned control command
and replace the host without changing collection, persistence, or memory
contracts.

## Sources

- [Apple `NSStatusItem`](https://developer.apple.com/documentation/appkit/nsstatusitem)
- [Apple `Process`](https://developer.apple.com/documentation/foundation/process)
- [Apple code-signing certificates](https://developer.apple.com/documentation/technotes/tn3161-inside-code-signing-certificates)
- [Apple information property lists](https://developer.apple.com/documentation/bundleresources/information-property-list)
