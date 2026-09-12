# ADR 0021: Use a native window for supervised prototype control

- **Status:** Accepted
- **Date:** 2026-09-12
- **Decision owner:** Emi
- **Amends:** ADR 0020 control surface and collector LaunchAgent restart policy

## Context and problem

Two disabled symbol-menu tests and one disabled text-label test failed human
visibility on the target Mac. In the text test, Emi disabled Hidden Bar and
still observed no control. Host liveness and unchanged runtime data were proven;
the rendering/placement root cause remains unresolved.

## Options considered

1. Continue menu-bar diagnostics without a new evidence-based hypothesis.
2. Use a normal native window and Dock presence for the supervised prototype.
3. Rewrite the Python collection pipeline in Swift.

## Decision

Emi approved option 2 and the explicit close-as-quit behavior. Replace the native
status item with a normal AppKit window and regular application activation
policy. Keep `io.contx.desktop`, signing, the sealed command contract, the
content-free Python control service and the Python collector unchanged.

Show collection state, the existing pause/resume/restart actions and Quit.
Disabled or unavailable states disable mutating controls. The red close button,
Quit button, Cmd-Q and application menu Quit share normal application shutdown;
the termination hook stops the owned collector. Closing does not merely hide
the application. Minimizing leaves a visible Dock entry; reopening brings back
the control window.

The collector LaunchAgent uses `KeepAlive = { SuccessfulExit = false }`, as
specified in the target Mac's `launchd.plist(5)` manual. A successful intentional
exit must not immediately relaunch the host. Abnormal exits retain restart
behavior. This manifest is tested synthetically; no LaunchAgent is installed or
loaded by this change. The periodic processor remains a separate job: quitting
the collector application does not disable processing of an existing backlog.

Remove the superseded native status-item text diagnostic build option. This is
an internal development interface with no published compatibility contract.
The standalone Python diagnostic controller is outside this native host change.

## Rationale

An ordinary window provides directly observable control while preserving the
already implemented collection and privacy boundaries. Rewriting collection
would not address the observed display failure and would duplicate audited
logic. Keeping two native control surfaces would add avoidable state and tests.

## Consequences

- The supervised prototype occupies a normal window and Dock entry.
- Quit stops collection; it does not uninstall CONTX or disable a future login
  launch. Persistent activation and shutdown remain separately evaluated.
- No new dependency, permission, persisted format or model path is introduced.
- The external development runtime is still not accepted for collector-child
  activation. Packaging and permission attribution gates from ADR 0020 remain.
- Compile and synthetic shutdown checks do not prove real collector operation.
  A disabled human-observed window/close test precedes any collection test.

## Rollback or replacement

Stop the exact test host, unregister and remove only the generated temporary
bundle. Preserve runtime data and record the outcome. Further control-surface
changes require a new accepted decision; do not silently restore an invisible
native menu or broaden permission attribution to generic Python.
