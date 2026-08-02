# OptMem provenance record

## Component

- **Name:** OptMem
- **Creator:** Victor Taelin
- **Upstream repository:** <https://github.com/VictorTaelin/OptMem>
- **Development snapshot:** `1fb164cf39028047781f72ac3bb1e5a691c1dcb0`
- **Reviewed executable SHA-256:**
  `3dc120d01be3115ef6267eab4103e7909fc830d6227b549f20991ba999ee9ffb`
- **Snapshot commit date:** 2026-07-30
- **Recorded by CONTX:** 2026-08-02
- **Current local location:** `optmem/`
- **Tracked by CONTX:** No; the local reference clone is ignored

## Intended use

CONTX may use, adapt, and evolve the complete OptMem implementation as its
initial final-memory engine. All access remains behind the CONTX `MemoryStore`
contract so that the backend can be evaluated, updated, or replaced without
coupling the rest of the product to its storage internals.

Under ADR 0015, CONTX uses two explicitly separated identities from the same
reviewed executable: `memory/` is the append-only historical source for writes,
`recall`, and `zoom`; `memory-active/` contains bounded content-addressed
generations rebuilt from exact active SQLite-backed text for `wake`. No OptMem
source modification is required by this projection strategy.

## Verification performed

- The local clone is clean at the recorded upstream commit.
- The adapter refuses to execute a development file that does not match the
  reviewed SHA-256 digest.
- The local source tree contains no `LICENSE` or `COPYING` file.
- The public upstream repository viewed on 2026-08-02 contains no displayed
  license file.
- `https://raw.githubusercontent.com/VictorTaelin/OptMem/main/LICENSE` returned
  `404 Not Found` on 2026-08-02.
- The upstream test suite passed locally under Python 3.12 and Python 3.14:
  `109099 passed, 0 failed`.

## Authorization state

Emi has authorized development use and modification and states that he knows
the repository creator. This is sufficient for the planned private local
development workflow, but it is not yet redistributable permission that
downstream users can verify from a release artifact.

Before the OptMem source is copied into the tracked CONTX tree or bundled in a
public distribution, obtain one of:

1. an upstream open-source license file covering the imported commit; or
2. a written grant from the rights holder permitting use, modification, and
   redistribution under terms compatible with CONTX and its users.

Record the evidence here and preserve the corresponding license text in the
source and binary distributions.

## Planned import procedure

After the authorization gate is satisfied:

1. verify the upstream commit and clean worktree;
2. copy the reviewed snapshot to `third_party/optmem/`;
3. preserve the upstream license, notices, repository URL, and commit;
4. exclude generated caches and animation build artifacts unless required;
5. run the upstream tests before and after import;
6. keep CONTX-specific modifications in reviewable commits;
7. update this record and `THIRD_PARTY_NOTICES.md`;
8. validate the OptMem adapter and replay suite.

## Update procedure

Every upstream update must record old and new commits, review the upstream
diff, re-check licensing, run upstream and CONTX integration tests, and verify
that memory identity, log format, wake output, locking, and recovery behavior
remain compatible.
