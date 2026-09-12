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
| Visible disabled application | Emi sees the control and opens its disabled-state menu; no collector or runtime mutation | Text diagnostic failed human observation; native window proposal pending |
| Stable private runtime | Accepted packaging decision covers Python, modules, native dependencies and OptMem | Pending decision and implementation |
| Installation and permissions | Verified signed installation and unchanged host/child permission attribution | Pending runtime checkpoint and explicit authorization |
| Short collection | Controlled metadata, one capture, then 15 minutes; exclusions, pause and stop verified | Pending |
| One-hour session | Inspectable complete pipeline, independent activity reference, resource measurements and defect review | Pending |

## Operating agreement

- Prepare implementation, synthetic tests and exact recovery before a live step.
- Obtain user observation for visible controls; process liveness is insufficient.
- Do not proceed through an unresolved privacy, lifecycle or control failure.
- If the prepared text diagnostic provides no useful new evidence, present a
  small native window/Dock alternative for approval instead of repeating menu
  variants. This alternative is not yet an accepted product change.
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

## Next action

Seek approval for an ordinary native window with Dock presence, retaining
`io.contx.desktop`, the Python control contract and existing collection logic.
The proposed first implementation displays state and existing pause/resume
controls, supports a clear quit action, and defines close as quit for the
supervised prototype so collection cannot be hidden by closing its window.
Replace the menu-only user surface rather than maintain two competing control
surfaces. This is a user-visible lifecycle decision, not yet accepted.

First validate the proposed window with collection disabled. Runtime packaging
still precedes any collector-child test. No installation, permission request
or real-data test is authorized by the diagnostic alone.
