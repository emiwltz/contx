# ADR 0008: Use ApplicationServices for authorized window titles

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

CONTX v0 includes optional active-window titles because application identity
alone cannot distinguish projects, documents, tabs, or terminal sessions.
Titles remain disabled by default and require existing macOS Accessibility
permission before CONTX may read them.

ADR 0007 added the narrow PyObjC Quartz binding for idle detection and screen
capture. Verification against the locked environment showed that Quartz does
not expose the `AXUIElement` APIs. PyObjC documents those HIServices
definitions in the separate `pyobjc-framework-ApplicationServices` package.
Version 12.2.1 provides a CPython 3.12 Universal 2 wheel and aligns with the
locked Cocoa and Quartz bindings.

## Problem

The focused-window title path needs an owned and testable native boundary. It
must not introduce an automatic permission request, custom unsafe ABI code, or
a second process merely to read one authorized string.

## Options considered

1. Add `pyobjc-framework-ApplicationServices>=12.2,<13` and use the official
   PyObjC wrappers around the macOS Accessibility APIs.
2. Declare the ApplicationServices C ABI locally through `ctypes`.
3. Remove active-window titles from v0 and revise the product scope.

## Decision

Use option 1, as approved by Emi on 2026-08-02.

The collector calls the non-prompting trust preflight before accessing any
Accessibility element. It reads only the focused application, focused window,
and title attributes needed by the confirmed v0 contract. Titles remain
disabled unless explicitly configured.

The separate setup command `contx permissions request --accessibility` may
call the native prompt API only after this exact user action. The command
explains the purpose before requesting, collects no activity, performs one
request without retrying, and prints the System Settings path when access is
still missing. Screen Recording follows the same explicit setup contract
behind its own `--screen-recording` flag. Neither request is reachable from
collector startup, capability inspection, or background supervision.

This decision authorizes dependency installation, implementation, and
synthetic tests. It does not authorize requesting Accessibility permission or
reading a live window title; those remain action-time approval gates.

## Rationale

The maintained wrapper keeps Core Foundation ownership, pointer metadata, and
native signatures out of CONTX code. It stays inside the accepted single
Python/PyObjC process and adds only the framework that owns the required API.

Custom `ctypes` declarations would transfer ABI compatibility and memory
management risk into CONTX for no meaningful product benefit. Removing titles
would weaken project and document identification and would require an explicit
v0 scope change.

## Consequences

- ApplicationServices becomes a direct reviewed macOS dependency.
- Quartz remains responsible only for idle and screen-capture APIs.
- Capability detection loads Accessibility separately from Quartz.
- Missing permission produces a degraded capability and no attribute read.
- Permission prompts occur only through an explicitly selected setup command;
  denial never causes an automatic retry.
- Pause, inactive-session, and application exclusion checks still happen
  before title persistence or screenshot capture.
- Packaging must include three narrow PyObjC framework packages rather than
  the all-framework metapackage.

## Rollback or replacement

Remove the dependency if titles leave the accepted product scope. If a native
helper later owns Accessibility, replace the implementation behind the typed
title-probe contract and preserve the same preflight, exclusion, and degraded
capability behavior.

## Sources

- [PyObjC ApplicationServices API notes](https://pyobjc.readthedocs.io/en/latest/apinotes/ApplicationServices.html)
- [PyPI `pyobjc-framework-ApplicationServices` release files](https://pypi.org/project/pyobjc-framework-ApplicationServices/12.2.1/)
- [Apple Accessibility attribute API](https://developer.apple.com/documentation/applicationservices/1462060-axuielementcopyattributevalues)
