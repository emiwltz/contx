# First supervised prototype

## Objective

Run a voluntary, supervised session of approximately one hour on the target Mac,
inspect its observations, timeline, memory decisions and `wake`, and compare
the results with an independent account of the activity. A justified decision
to retain no new memory is valid; do not weaken promotion rules for the demo.

Passing this checkpoint does not establish controlled-day J1 acceptance,
48-hour retention in live operation, or the 7–14-day J7 pilot benefit.

## Delivery checkpoints

| Checkpoint | Acceptance evidence | Status |
| --- | --- | --- |
| Visible disabled application | Emi sees the control and opens its disabled-state menu; no collector or runtime mutation | Passed: visible disabled window and Dock, red-close exit, unchanged runtime |
| Stable private runtime | Accepted packaging decision covers Python, modules, native dependencies and OptMem | Passed in isolation: sealed release, native corruption refusal and signed dispatch |
| Installation and permissions | Verified signed installation and unchanged host/child permission attribution | Installed disabled launch and close passed; native and child granted preflight observed |
| Short collection | Controlled metadata, one capture, then 15 minutes; exclusions, pause and stop verified | Metadata controls and manual Finder/Zen sequence observed; sealed-runtime synthetic capture passed; native-host capture and 15-minute session pending |
| One-hour session | Inspectable complete pipeline, independent activity reference, resource measurements and defect review | Pending |

## Operating agreement

- Prepare implementation, synthetic tests and exact recovery before a live step.
- Obtain user observation for visible controls; process liveness is insufficient.
- Do not proceed through an unresolved privacy, lifecycle or control failure.
- Use the accepted ADR 0021 window/Dock control; its disabled visibility and
  close behavior have passed direct user observation.
- Keep installation, permissions and collection separate from the disabled
  visual diagnostic. Existing ADR 0020 runtime packaging gates still apply.
- After each checkpoint, record observed results, remaining uncertainty and the
  next action here. Keep private runtime fingerprints and generated bundles out
  of Git.

## 2026-09-12 preparation and disabled text diagnostic

Emi authorized proceeding with the prepared visual test after reviewing the
prototype sequence. Live preflight established disabled background collection,
disabled titles and screenshots, effective pause, no collector lease, and no
loaded collector or processor LaunchAgent. Xcode and one valid Apple Development
identity were available. Sandboxed identity/process/lease checks were restricted;
the corresponding narrowly scoped unsandboxed checks succeeded.

The 15 focused native bundle, native control and child-controller tests passed.
A fresh temporary `CONTX TEST` bundle compiled, was signed, and passed strict
signature verification. The normal launch started the exact native host; the
first live check confirmed disabled state, no collector lease, and identical
runtime file contents and metadata. The observer checks only the label and
menu, without choosing a mutating command.

The bounded harness checks the disabled state and runtime fingerprint throughout
the observation window, then terminates the exact host and unregisters only the
temporary bundle. Emi disabled Hidden Bar and still observed no CONTX label.
The human visibility gate failed. This does not establish a root cause or
validate real collection, regardless of AppKit's successful attachment checks.

The test was stopped after that observation. Final checks confirmed the exact
host had stopped, runtime contents and metadata remained unchanged, collection
remained disabled and effectively paused, and no collector lease was live.
The temporary LaunchServices registration was verified absent and the generated
application was removed. Only private diagnostic evidence remains in temporary
storage. Hidden Bar was changed by Emi, not by the harness.

## 2026-09-12 accepted window implementation

Emi approved a normal window, Dock presence and close-as-quit. ADR 0021 records
that decision. The native status-item path and its text-diagnostic build option
are removed. Existing controls now live in a regular AppKit window. Red close,
Quit and Cmd-Q terminate the app through its existing owned-child shutdown hook.
The collector LaunchAgent restarts abnormal exits but not successful voluntary
exits, avoiding an immediate restart after closing the window.

A compiled probe using the actual Swift termination hook stopped a disposable
`/bin/sleep` child, tolerated repeated shutdown, and cleared an already-exited
child. It opened no window, read no CONTX data and launched no real collector.

The signed temporary app launched in disabled state with no runtime mutation.
Emi confirmed seeing the window and supplied a screenshot showing the complete
layout, `Collection disabled`, disabled collection buttons and an enabled Quit
button. Emi also confirmed Dock presence and that the red close button made the
window disappear. The harness observed host exit before its own termination
step, then verified disabled state, no live collector lease and unchanged runtime
contents/metadata. The temporary LaunchServices registration was verified absent
and the generated application was removed. No user screenshot is stored in Git.

Repository validation: 479 pytest tests passed (one existing third-party
Starlette/httpx deprecation warning); Ruff checks and formatting passed; mypy
reported no errors in 127 source files. The removed test covered only the removed
text-diagnostic build option.

## Private runtime feasibility

An isolated private Python plus non-editable production install passed import,
web-asset, disabled-control and synthetic OptMem checks. See
[v0.1-private-runtime-feasibility.md](v0.1-private-runtime-feasibility.md).
Emi accepted ADR 0022. Full inventory signing, private Python/OptMem launch,
periodic processor verification and immutable version construction are implemented
and verified through synthetic/native validation, including signed dispatch.

## Next action

The approved [installation plan](v0.1-installation-plan.md) has been executed
through the first disabled launch. The final signature and inventory pass and
runtime data is unchanged. Emi confirmed visibility and red-close disappearance; exact host exit and
unchanged runtime state passed. Accepted ADR 0023 adds explicit permission setup. The updated signed app is
installed with previous versions retained; its disabled state and unchanged
data checks pass. Emi supplied granted Screen Recording and Accessibility preflight results for
both native host and collector child. Post-check state and data remain unchanged.
Prepare the bounded metadata-only collection experiment next. See [setup validation](v0.1-native-permission-setup-validation.md). No real collection or installed LaunchAgent has been started.

## First metadata-only session

See [metadata trial](v0.1-metadata-only-trial.md). The isolated session recorded
application durations without titles or images, paused successfully and stopped
at the five-minute supervision bound. Normal data remained unchanged. Next verify
manual close with a live collector and confirm the user's chosen applications
before advancing to the controlled capture.

## Autonomous Computer Use follow-up

[Control trial](v0.1-computer-use-control-trial.md): two UI-triggered red closes
while collecting stopped both native host and child before supervisor cleanup.
Timed pause and resume also passed. The automated foreground-app comparison is
inconclusive: targeting Finder/Spotify through accessibility did not produce the
expected segments. No captures or model processing were enabled. Both trials
ended disabled with unchanged normal data.

## Manual foreground follow-up

With no Computer Use interactions during the session, the expected main Finder
then Zen sequence appeared as approximately 26-second and 30-second segments.
Pause and user closure stopped the collector before cleanup; normal data remained
unchanged, with no titles/images or model processing. See the
[metadata trial](v0.1-metadata-only-trial.md). Prepare a controlled synthetic-window
capture next; the automated foreground mismatch remains unexplained.
