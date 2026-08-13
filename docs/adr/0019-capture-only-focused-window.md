# ADR 0019: Capture only the policy-authorized focused window

- **Status:** Accepted
- **Date:** 2026-08-13
- **Decision owner:** Emi

## Context

CONTX evaluates pause state, application identity, optional window title, and
exclusion rules before reading pixels. The first native screenshot source then
used `CGWindowListCreateImage` with the on-screen-only option and an infinite
rectangle. On a multi-display Mac, that operation can include every visible
display even though the authorization decision describes only the frontmost
application and focused window.

Application and title exclusions cannot protect unrelated windows visible on a
secondary display. The broad capture therefore violates the product's
content-minimization boundary even when the permitted application itself is
correctly identified.

The locked `pyobjc-framework-Quartz` dependency already exposes both
CoreGraphics window metadata and ScreenCaptureKit on the target Mac. AppKit's
frontmost `NSRunningApplication` also exposes its process identifier. No new
package or native helper is needed for a focused-window implementation.

## Problem

The native source must bind a policy-authorized metadata sample to exactly one
window, refuse ambiguous or changed context, and avoid adjacent display or
window pixels. The binding is necessarily time-sensitive because macOS does not
provide an atomic operation that evaluates CONTX policy and captures a window
in one system call.

## Options considered

1. Capture all on-screen displays and rely on active-application and title
   exclusions.
2. Resolve the frontmost normal window for the authorized application, match it
   to one ScreenCaptureKit `SCWindow`, and capture it through a desktop-
   independent single-window content filter.
3. Resolve one CoreGraphics window and keep using the deprecated
   `CGWindowListCreateImage` API for exact-window pixels.
4. Use a long-lived ScreenCaptureKit stream instead of the macOS 14 screenshot
   manager API.

## Decision

Use option 2 for v0, as approved by Emi.

The metadata sample carries an ephemeral positive process identifier in
addition to application name and bundle identifier. CONTX never persists that
process identifier. After the screenshot planner authorizes a sample, the
native source:

1. confirms existing Screen Recording permission without prompting;
2. confirms that the same process and application identity remain frontmost;
3. reads the CoreGraphics on-screen window list, which Apple orders front to
   back, and selects the first visible, non-transparent, positive-sized layer-0
   window owned by that process;
4. finds the same window ID, process ID, and bundle identifier in
   `SCShareableContent`;
5. creates `SCContentFilter(desktopIndependentWindow:)` for only that window;
6. excludes the selected window's shadow and bounds either pixel dimension to
   8192 while preserving aspect ratio;
7. revalidates the application, window ID, and, when title collection is
   enabled, the exact title immediately before capture;
8. uses `SCScreenshotManager` to read one image;
9. repeats the same context checks before encoding or persistence.

Missing process metadata, permission, APIs, or a five-second native timeout is
an explicit collector error. A missing normal window, missing exact shareable
match, or context change safely skips the current screenshot without stopping
metadata collection. The service applies its normal minimum-interval backoff
before retrying. A context change detected after the native callback discards
the image before PNG encoding and raw-store persistence.

`SCScreenshotManager` is available on macOS 14 and later. The v0 screenshot
capability therefore reports unavailable on older systems even though other
CONTX capabilities may continue to work. The minimum supported version for a
future distributed v1 remains a separate packaging decision.

This decision changes implementation only. It does not authorize a macOS
permission request, a live title read, a live screenshot, persistent
collection, LaunchAgent installation, or the real-data pilot.

## Rationale

The single-window filter makes the system capture object match the object that
CONTX authorized. It prevents a permitted frontmost application from becoming
a proxy authorization for unrelated displays or background windows.

PID, bundle, window ID, layer, visibility, and focus checks narrow both stale
metadata and process-relaunch races. The pre- and post-capture checks cannot
make a changing window atomic, but they prevent a switched application or
window from being persisted and make the residual race explicit and testable.

Keeping the already reviewed Quartz bridge avoids another dependency and
process boundary. ScreenCaptureKit avoids adding new code around Apple's
deprecated image API. A long-lived stream would support older systems but adds
stream lifecycle, frame delivery, interruption, and resource behavior that the
event-driven v0 capture path does not need.

## Consequences

- Multi-display pixels outside the selected window are no longer part of a
  screenshot artifact.
- A frontmost application without a normal on-screen window skips that
  screenshot with bounded retry cadence rather than taking a broad fallback or
  stopping metadata collection.
- Window metadata enumeration occurs only after policy authorization and
  existing permission preflight; the implementation does not inspect titles
  from the ScreenCaptureKit window list.
- Optional Accessibility title collection strengthens the race check when it
  is enabled. Without it, application and exact-window identity remain the
  available authorization boundary.
- The process ID is collection-ephemeral and does not alter persisted schemas,
  observations, replay identity, exports, or public API responses.
- The current five-second timeout and 8192-pixel bound are explicit v0 resource
  limits subject to target-Mac pilot evidence.
- The real smoke test must verify callback delivery from the AppKit collector
  run loop, focused-window selection, permission UX, and rejection after a
  deliberate focus change.

## Residual risk

The contents of one already-authorized window can change between the final
metadata check and native pixel acquisition without changing its process or
window ID. No reviewed public API combines CONTX's policy evaluation with an
atomic capture. The post-capture check prevents persistence after observable
focus or title changes, but cannot undo an in-memory native read. The bounded
live smoke and real pilot must treat any such mismatch as a privacy incident;
evidence of an unacceptable race requires a stricter native design before v0
completion.

## Rollback or replacement strategy

Screenshot collection can be disabled independently without changing stored
metadata, model, event, or memory contracts. Do not restore all-display capture
as a fallback.

If the live smoke reveals PyObjC callback deadlock, unreliable focus matching,
or unacceptable race behavior, replace only this `ScreenshotSource` with a
small signed Swift ScreenCaptureKit helper. The replacement must preserve the
same exact-window input contract, fail-closed checks, timeout, dimension bound,
no-prompt behavior, and post-capture discard rule.

## Sources

- [Apple `NSWorkspace.frontmostApplication`](https://developer.apple.com/documentation/appkit/nsworkspace/frontmostapplication)
- [Apple `NSRunningApplication.processIdentifier`](https://developer.apple.com/documentation/appkit/nsrunningapplication/processidentifier)
- [Apple CoreGraphics required window-list keys](https://developer.apple.com/documentation/coregraphics/required-window-list-keys)
- [Apple `SCWindow`](https://developer.apple.com/documentation/screencapturekit/scwindow)
- [Apple desktop-independent single-window content filter](https://developer.apple.com/documentation/screencapturekit/sccontentfilter/init%28desktopindependentwindow%3A%29)
- [Apple ScreenCaptureKit capture sample](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)
