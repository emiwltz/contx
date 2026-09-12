# ADR 0022: Build a private versioned prototype runtime

- **Status:** Accepted
- **Date:** 2026-09-12
- **Decision owner:** Emi

## Context and problem

The disabled native window checkpoint passed. Its current sealed contract hashes
only the collector and control entrypoint scripts, while those scripts use a
shared uv Python installation and import an editable repository checkout. This
is insufficient for accepting real collector-child activation under ADR 0020.

A disposable local feasibility experiment rebuilt the locked production package
against a private copy of the existing Python distribution. Imports, packaged
web assets, disabled isolated control and a synthetic OptMem round trip passed.
See `docs/evaluation/v0.1-private-runtime-feasibility.md` for limits.

## Options

1. Keep the editable environment. Lowest immediate cost, but code and interpreter
   can change outside the installed application. Does not meet the accepted gate.
2. Build a private versioned Python runtime and install CONTX as a non-editable
   wheel. Reuses the current toolchain, keeps failures inspectable, and requires
   explicit runtime inventory, integrity checks and launch wiring.
3. Adopt a self-contained application packager now. Potentially preferable for
   public distribution, but adds a packaging dependency and requires separate
   evaluation of native frameworks, signing, resources and process attribution.
   This option has not been experimentally compared in this checkpoint.

## Decision

Emi approved option 2 in this task. Use it for the private supervised prototype. No new production dependency
or shared Python modification is needed. Do not treat this as a public installer.

Accepted target layout:

- `~/Applications/CONTX.app`: signed window host and sealed runtime manifest.
- `~/Library/Application Support/CONTXRuntime/releases/<build-id>/python/`:
  private copy of the reviewed Python 3.12 distribution.
- `.../<build-id>/env/`: environment recreated at its final versioned path from
  `uv.lock`, with production dependencies and a non-editable CONTX wheel.
- `.../<build-id>/optmem/memo`: private copy of the reviewed checksum-pinned
  snapshot, never copied into tracked source or a public artifact.

Keep executable releases separate from existing CONTX data directories. Existing
configuration, database, raw data and logs retain their locations. Ollama and
model weights remain separately managed local dependencies.

## Required implementation before acceptance

- Record source commit, lockfile hash, Python identity and complete executable
  runtime inventory, including native modules, standard library and OptMem.
- Reject unresolved or external symlinks, missing/extra execution files and
  altered bytes at the verified release boundary. Seal the expected inventory
  through the native signature; do not trust a mutable adjacent manifest alone.
- Define validation frequency and measure its cost before accepting it. Hashing
  the complete environment on each one-second status poll is not an accepted
  design. Any cached validation needs explicit invalidation and failure tests.
- Use private version paths and read-only release files against accidental
  edits. This is not a security boundary against the account owner or an
  administrator; remaining concurrent-mutation assumptions must be documented.
- Launch control, collector and processor from the same verified release, with
  Python path/environment isolation. OptMem must use that private interpreter,
  not the generic `/usr/bin/env python3` resolution under the host's current PATH.
- Preserve disabled no-mutation behavior, owned-child shutdown, local-only
  processing and permission attribution. No real collector starts during build.
- Construct and verify a new version completely before selecting it. A failed
  build cannot replace an accepted version. Do not move a prepared venv to a
  different absolute path. Do not switch or remove a release in active use.
- Keep the prior complete release for explicit rollback; remove only owned,
  identified generated files. Runtime updates do not migrate user data.
- Test stale/changed interpreter, module, native library and OptMem, partial
  construction, changed paths, and rejected manifests before any live activation.

## Rationale and consequences

The experiment supports this bounded route without taking on public distribution
work. Expected runtime size is about 106 MiB for Python and installed production
packages before extra evidence/signatures; two retained versions roughly double
that amount. This is a measured feasibility figure, not a final disk guarantee.

Code, model processing and persistence boundaries remain intact. New work is the
private build/verification contract and exact launch wiring. Public redistribution
of OptMem remains subject to the existing provenance gate. No data migration,
permission grant, model download, background activation or login installation is
included in approving this packaging approach.

## Rollback or replacement

Until installation, discard only the isolated generated runtime. After a future
authorized installation, stop owned processes before selecting a prior verified
release. Keep all data. A future self-contained packager may replace this build
layer while retaining the native host/control and memory contracts.

## Implemented verification contract

The version-2 inventory is embedded inside the native signed resources. The
host validates its own complete resource signature with Security.framework
before trusting the inventory. All
runtime files, directories and internal symlinks are enumerated; regular files
are SHA-256 checked, permissions and ownership checked, and external links
rejected. Python and OptMem paths are fixed relative to the release root.
The old entrypoint-only version-1 contract is removed.

The native host hashes the full release before its first Python call, before
collector start/restart and before explicit control actions. Each one-second
status iteration checks every previously inventoried node and compares device, inode, size, mode,
mtime and ctime against that process's initial verified baseline. Directory
stamps detect additions/removals without repeated directory listing. Additions,
removals or stamp changes latch a failure, stop the owned collector and disable
further control execution. Repairing disk contents does not clear the latch;
a fresh verified host is required. This does not protect against a hostile
account owner who races mutations between checks or replaces the trusted app.
Runtime releases must not be modified while in use.

Native canonicalization uses POSIX realpath, matching Python's canonical paths.
Foundation's URL resolution was observed to rewrite /private/var to /var and
incorrectly reject a valid synthetic release; the native regression probe covers
that target-Mac path behavior.

The periodic LaunchAgent now invokes the same signed host with the sole internal
`--process-once` argument. It verifies the inventory, then execs private Python
with `-I -B -m contx.processing.entrypoint`, preserving launchd's owned PID and
signal delivery. It opens no AppKit window. Its bounded process assumes the
read-only release remains unchanged for that run; the next run verifies again.
OptMem is invoked explicitly with the current isolated Python interpreter,
ignoring its env-python shebang. The native environment fixes the OptMem path.

The builder requires a clean committed checkout, builds from git archive,
installs hash-locked production dependencies by copy and CONTX as a wheel,
seals release permissions and atomically publishes the complete manifest.
The release is built at its final version path; there is no relocation or
in-place overwrite. Any failed build removes only its newly created release.
Existing releases and all user data remain untouched. Selection of an installed
app/release is a later explicit installation action, performed with processes
stopped; this build tool has no live switching or deletion command.

The builder audits all Mach-O load commands, resolves private loader/rpath
references and rejects external non-system dependencies before sealing. A dylib's
LC_ID_DYLIB name is its identity, not a load dependency; original build-path IDs
are not mistaken for links to the shared interpreter. A separate isolated import
probe verifies actual loaded images on the target Mac.
