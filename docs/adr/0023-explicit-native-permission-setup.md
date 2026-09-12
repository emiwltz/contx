# ADR 0023: Add explicit permission setup to the native host

- **Status:** Accepted
- **Date:** 2026-09-12
- **Decision owner:** Emi
- **Amends:** ADR 0020 native permission API scope and ADR 0021 control surface

## Problem and evidence

The installed signed host passes disabled visibility and close checks, but has
no permission setup or child preflight entrypoint. Its source validator explicitly
forbids permission APIs. The earlier separate identity probe demonstrated granted
native/child Screen Recording preflight for that diagnostic app only; it cannot
establish the current production application's permission attribution.

Emi approved integrated permission controls in this task.

## Decision

Add explicit setup actions to the existing window: request Screen Recording,
request Accessibility, and verify access without reading content. Explain that
Screen Recording supports optional focused-window captures and Accessibility
supports optional window titles. Permission grants do not enable either feature.
No permission API runs automatically at startup or on status polling.

The signed native host requests one selected permission only after its matching
button is clicked. Verification checks native permission state and launches the
same sealed interpreter, daemon module and native-child environment used for
collection, with a fixed preflight-only argument. That argument is handled before
constructing any collection runtime. It reads permission booleans only, does not
request access, inspect windows, acquire collector leases, initialize databases,
read titles or capture pixels. Unknown arguments fail before collection starts.

Use bounded, schema-checked child output and a timeout. Verify the signed runtime
before dispatch. Report native and child results separately; denial, malformed
output, timeout or API failure must never be presented as success. Closing the
window must stop any owned preflight child as well as the collector.

For this initial setup, require disabled background collection and no live
collector before requesting or verifying access. Keep existing settings and
pause state unchanged. A future setup flow during collection requires its own
behavioral decision.

Rebuild and sign a new version before any permission request. Preserve the
installed app together with its referenced release for rollback. Test the final
installed version and do not rebuild between permission grant and child check.
The test proves preflight availability only; actual focused capture and title
access remain separate supervised checks.

## Alternatives and consequences

- A separate diagnostic app costs less integration work but cannot prove the
  production executable's attribution; it repeats an already bounded experiment.
- Manual System Settings changes alone do not prove that the collector child can
  use the grant and leave no first-class setup or verification flow in CONTX.
- Integrated actions add a small durable UI/launch boundary and regression tests,
  with no third-party dependency, persistence format change or collection scope
  increase. They require explicitly relaxing the native permission API guard.

## Verification before a live request

Test explicit action dispatch, no automatic prompting, disabled-state gating,
invalid arguments, child denied/granted/error output, timeout, signature refusal,
shutdown, and absence of collection/runtime initialization in the preflight path.
Compile the actual native source and validate a signed synthetic version before
selecting the new installed app. A live request requires Emi's action; do not
trigger it as part of build, installation or ordinary launch.

## API references

Apple documents a Boolean Screen Recording preflight and a distinct request API:
[CGPreflightScreenCaptureAccess](https://developer.apple.com/documentation/coregraphics/cgpreflightscreencaptureaccess())
and [CGRequestScreenCaptureAccess](https://developer.apple.com/documentation/coregraphics/cgrequestscreencaptureaccess()).
For Accessibility, prompting is asynchronous and does not change the immediate
returned trust state; recheck rather than equating a request with a grant:
[AXIsProcessTrustedWithOptions](https://developer.apple.com/documentation/applicationservices/1459186-axisprocesstrustedwithoptions).
