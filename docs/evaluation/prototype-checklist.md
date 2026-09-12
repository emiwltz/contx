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
| Installation and permissions | Verified signed installation and unchanged host/child permission attribution | Prepared installation and explicit authorization remain |
| Short collection | Controlled metadata, one capture, then 15 minutes; exclusions, pause and stop verified | Pending |
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

Prepare a concrete final installation location, permission wording and recovery
procedure for approval. Rebuild the release at that final absolute path; do not
relocate the temporary environment. See [v0.1-private-runtime-validation.md](v0.1-private-runtime-validation.md)
for completed evidence and limits. No real collection or installed LaunchAgent
has been started.
