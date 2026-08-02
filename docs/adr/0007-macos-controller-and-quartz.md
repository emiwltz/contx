# ADR 0007: Keep the v0.1 macOS controller in Python and add Quartz

- **Status:** Proposed
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

CONTX v0.1 needs a visible menu-bar control, continuous application and system
state collection, idle detection, permission preflights, and later selective
screen capture. The current runtime already uses Python 3.12 and
`pyobjc-framework-Cocoa` 12.2.1 for AppKit.

A focused target-Mac prototype created an accessory `NSApplication`, added a
`CONTX` `NSStatusItem` with a menu, read the configured menu state, and removed
the item cleanly. It required a GUI user session but no helper installation,
permission request, or persistent process.

The current environment does not contain the Quartz bindings. As a result,
CONTX can detect Cocoa application and workspace-notification APIs, but it
truthfully reports idle detection as unavailable. The sensitive window-title
and screenshot features remain disabled before their permission preflights are
called.

Apple documents the required public APIs:

- `NSStatusItem` for menu-bar status and actions;
- `NSWorkspace` notifications for application activation, session changes,
  sleep, and wake;
- `CGEventSourceSecondsSinceLastEventType` for elapsed time since user input.

PyObjC documents `pyobjc-framework-Quartz` as the wrapper that exposes
CoreGraphics through the top-level `Quartz` package. Version 12.2.1 has a
CPython 3.12 Universal 2 wheel for macOS 10.13 or later, so it aligns with the
already locked Cocoa bridge and does not require the all-framework PyObjC
metapackage.

## Problem

The controller and native system probe must be selected before their packaging,
run-loop, supervision, permission, and failure boundaries become part of v0.1.
The choice must avoid an unnecessary second application while not hiding the
risks of running native UI callbacks inside Python.

## Options considered

1. Keep the menu-bar controller in the supervised Python process using the
   existing PyObjC Cocoa bridge, and add only `pyobjc-framework-Quartz` at the
   same 12.2 compatibility line.
2. Build a small Swift menu-bar helper and define an IPC contract between the
   helper and the Python daemon.
3. Keep the PyObjC menu-bar but call CoreGraphics through custom `ctypes`
   declarations instead of adding the official Quartz bindings.

## Proposed decision

Use option 1 for v0.1.

Add the narrow `pyobjc-framework-Quartz>=12.2,<13` dependency. Keep one Python
process responsible for the collector run loop and menu-bar control, with the
CLI as an independent fallback using the same database-backed control service.

This decision authorizes implementation and synthetic or non-prompting
capability tests only. It does not authorize:

- enabling persistent background collection;
- installing a launchd agent or another system helper;
- requesting Accessibility or Screen Recording permission;
- collecting live window titles or screenshots;
- starting the real-data pilot.

Those remain separate action-time approval gates.

## Rationale

The prototype removed the main reason to introduce Swift now: PyObjC can create
the required minimal status surface with a dependency already present in the
runtime. Adding the matching Quartz wrapper uses maintained public bindings and
keeps CoreGraphics signatures out of CONTX-owned native ABI code.

A Swift helper would add a second build system, process lifecycle, IPC protocol,
signing surface, failure mode, and packaging path before evidence shows that
they are necessary. Custom `ctypes` would save one direct package but transfer
API-signature and platform-compatibility maintenance into CONTX.

## Consequences

- The menu-bar and collector share one failure domain in v0.1.
- The daemon lease, audited runner, bounded segments, and CLI control remain the
  non-UI safety foundation.
- A Python or native callback crash can remove the visible menu item together
  with collection; the future supervisor must restart both, while CLI status
  continues to distinguish a live daemon from stale configuration.
- Packaging retains Python and PyObjC as target-Mac runtime requirements.
- Quartz becomes a direct reviewed dependency, while the broad PyObjC
  metapackage remains excluded.
- No permission prompt is issued automatically. Missing permission produces a
  visible degraded capability instead.

## Rollback or replacement

If the target-Mac soak test shows unacceptable callback crashes, event-loop
latency, resource use, signing friction, or menu-bar unreliability, replace only
the controller with a signed Swift helper. The helper must call the same pause,
resume, exclusion, capability, and status contracts and must not gain direct
access to raw data or semantic memory.

The Quartz dependency can be removed if a native helper takes ownership of all
CoreGraphics operations and an explicit versioned IPC boundary replaces those
calls.

## Sources

- [Apple `NSStatusItem` documentation](https://developer.apple.com/documentation/appkit/nsstatusitem)
- [Apple `NSWorkspace` documentation](https://developer.apple.com/documentation/appkit/nsworkspace)
- [Apple idle-time API documentation](https://developer.apple.com/documentation/coregraphics/cgeventsource/secondssincelasteventtype(_:eventtype:))
- [PyObjC Quartz API notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html)
- [PyPI `pyobjc-framework-Quartz` release files](https://pypi.org/project/pyobjc-framework-Quartz/)
