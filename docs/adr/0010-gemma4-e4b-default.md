# ADR 0010: Select Gemma 4 E4B QAT as the v0 default local model

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

ADR 0009 requires one local multimodal model for v0 but intentionally leaves
the concrete model replaceable. The target machine is a MacBook Air M4 with
16 GB of unified memory. The model must interpret authorized screenshots
locally, return the strict CONTX schema reliably, classify protected content
conservatively, attribute ordinary project work, and remain usable alongside
normal development work.

The first Qwen 3 VL 4B baseline failed the fixed privacy and quality matrix.
Subsequent controlled comparisons evaluated Qwen 3 VL 8B, Gemma 3 4B QAT,
Gemma 4 12B, and Gemma 4 E4B QAT. Every comparison used synthetic pixels and
metadata only; no real display content was collected.

## Problem

CONTX needs a concrete default that makes fresh installations and evaluation
reproducible. Keeping the failed 4B baseline as the default would make the
nominal v0 path knowingly fail its own quality gate, while selecting the
largest candidate would exceed the target machine's practical latency budget.

## Options considered

1. Keep Qwen 3 VL 4B as the default and continue prompt tuning.
2. Select Qwen 3 VL 8B for more capacity at higher memory cost.
3. Select Gemma 3 4B QAT for lower resource use.
4. Select Gemma 4 12B for maximum local capacity among the tested candidates.
5. Select Gemma 4 E4B QAT as the tested quality/resource compromise.

## Decision

Use option 5. `gemma4:e4b-it-qat` is the default v0 Ollama model.

The evaluated artifact has digest
`ee665637121887cf3befff38abbb1be4ee117c7db867d97a67e29049ecd7e15f`.
CONTX keeps the model name configurable and records the exact runtime version,
model identity, digest, prompt version, and output-schema version for every
accepted interpretation. Existing configuration files are not silently
rewritten; the new default applies to newly initialized configuration.

The Ollama request disables model thinking and bounds output to 512 tokens.
The selected synthetic input profile remains 1280 by 720 because the compact
profile did not reduce prompt tokens materially and was less reliable on a
password-manager fixture.

The model is an external local runtime artifact. It is not bundled, downloaded,
or committed by CONTX.

## Rationale

With prompt `local-screen-v9`, Gemma 4 E4B QAT returned 16 valid responses and
passed all 16 fixed sensitivity, category, and project-attribution oracles. It
also passed repeated targeted checks for the password-manager, ordinary-project,
and visual prompt-injection fixtures. A persistent synthetic pipeline run built
one provenance-backed event successfully.

The model produced a 17.1-second cold project-fixture interpretation and
6.9-to-8.4-second warm interpretations in the focused benchmark. The complete
privacy matrix had a 21.2-second median and 24.8-second maximum. A sampled cold
run peaked near 6.07 GiB resident memory. This is materially more practical on
the target Mac than Gemma 4 12B, which exceeded the 120-second timeout in both
cold and warm attempts.

Gemma 3 4B QAT was faster but unstable at the safety boundary: its strongest
matrix attempts still missed at least one protected or ordinary control, and
repeated ordinary-control results varied. Qwen 3 VL 8B also missed a visual
prompt-injection classification and did not establish an acceptable full
matrix result. Additional prompt tuning of the rejected models would have
increased delivery cost without better evidence than the passing E4B result.

Gemma 4 is published under Apache License 2.0 according to its model card and
the installed Ollama artifact's license metadata. This is compatible with the
CONTX project license, although any future redistribution must still carry the
required notices.

## Consequences

- A fresh CONTX runtime expects `gemma4:e4b-it-qat` to be installed in Ollama.
- A configured model remains replaceable; configuration and provenance prevent
  a replacement from being mistaken for the evaluated artifact.
- Model output reproduction of protected local pixels is measured but is not a
  release blocker because v0 neither sends content remotely nor implements a
  masking path. Sensitivity classification and the durable-memory promotion
  gate are the security boundary.
- The 16-fixture result is a synthetic acceptance result, not proof of perfect
  classification on real activity. Corrections, replay, and the controlled
  pilot remain necessary.
- The measured process CPU sample does not quantify total GPU or system energy.
  Privileged energy measurement was not authorized; long-duration resource
  impact remains a pilot measurement.
- Model files continue to consume external local disk space and are outside
  CONTX backup, export, and deletion ownership.

## Rollback or replacement

Change the configured model only after running the same versioned fixture
matrix and persistent-pipeline proof, recording the exact model digest, and
comparing quality, latency, memory, and operational behavior. A replacement
must not add a remote user-content path or bypass strict output validation and
memory-promotion policy. Supersede this ADR if the project changes the default
rather than documenting that decision only in configuration.
