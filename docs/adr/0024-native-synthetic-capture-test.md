# ADR 0024: Offer a bounded synthetic capture from the installed host

- **Status:** Accepted
- **Date:** 2026-09-12
- **Decision owner:** Emi
- **Amends:** ADR 0023 setup actions

## Context and approval

Direct invocation of the sealed runtime captured a synthetic window successfully,
but did not establish capture through the installed native host. Emi explicitly
approved an integrated "Tester une capture fictive" action with a single capture,
automatic stop and visible result, followed by a new signed application version.

## Decision

Add an explicit setup button, available only with collection disabled and no live
collector. Check runtime integrity and disabled state before dispatch. Launch the
same private interpreter and daemon entrypoint as the collector with the dedicated
`--synthetic-capture` mode, handled before any daemon construction. The child also
checks host dispatch and disabled state before invoking the existing AppKit smoke.

The test creates two synthetic windows, requires its own process to be foreground
before reading the expected synthetic title, proves refusal after an intra-process
window switch, then captures exactly its primary window using the production
ScreenCaptureKit source. No permission request, background feature activation,
database initialization, model processing or LaunchAgent operation is involved.

The host bounds the command to 40 seconds, validates versioned output and the
expected artifact path, size and digest, and displays the image in a result sheet.
The image is created in a fresh private temporary directory and removed before
the preview appears; it is not stored in the observation database. Failure and
window-close paths stop the owned child and attempt temporary-file cleanup.
Cleanup failure is reported separately. Force-killing the host or a machine crash
can leave a synthetic temporary artifact; this is not a persistent capture store.

## Alternatives and limits

Enabling periodic screenshots and excluding currently open applications would
not confine capture if another application appeared. A separate diagnostic app
would not verify this host boundary. The integrated action is a repeatable setup
diagnostic with no new dependency or persisted data format.

Success proves this signed-host synthetic capture path. It does not yet validate
periodic screenshot planning, observation persistence, exclusions across user
applications, retention, or a fifteen-minute continuous collection session.

## Validation

Python tests cover dispatch, disabled/host guards, sanitized failure and rejection
of a foreign foreground process before title access. The compiled native probe
exercises success, invalid child output, enabled-collection refusal, timeout and
cancellation, including temporary-directory cleanup. Existing native permission
dispatch probes must continue passing. Installed application evidence is recorded
separately after building and signing the immutable release.
