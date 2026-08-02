# ADR 0009: Require a local multimodal model in v0

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

The founding specification allowed a deterministic no-model backend, a
standalone OCR stage, local models, and a future optional remote provider. It
also required deterministic secret detection and redaction before any outbound
model call.

Emi clarified that CONTX is intended to depend on a local LLM: the product does
not need a parallel deterministic extraction pipeline or secret-masking stage
for v0. Raw observations and model processing remain on the user's Mac.

The target machine is a MacBook Air M4 with 16 GB of unified memory. At the time
of this decision, the inspected Ollama inventory contained only cloud aliases
and no suitable local vision model. ADR 0010 records the later evidence-based
selection of the concrete v0 default.

## Problem

The semantic pipeline needs one authoritative interpretation boundary. Keeping
both deterministic extraction and mandatory model extraction would duplicate
behavior, complicate provenance, and optimize for a product mode that Emi does
not want. Conversely, removing redaction is safe only while user content never
crosses a remote boundary.

## Options considered

1. Require a local multimodal LLM for extraction and interpretation, and
   prohibit remote model providers in v0.
2. Keep a deterministic/OCR pipeline as a fully functional fallback beside an
   optional local model.
3. Use a remote model after deterministic secret masking.

## Decision

Use option 1, as decided by Emi on 2026-08-02.

The v0 processing path requires a local model. Screenshots and permitted local
metadata may be supplied directly to a local multimodal model through a typed
`ModelProvider`. Model output must be validated against strict structured
schemas and retain source and processing provenance.

V0 does not implement deterministic semantic extraction or secret redaction.
It also does not implement a remote provider or any outbound user-content
transport. Loopback communication with a model runtime on the same Mac is
local processing, not a remote model call.

Deterministic code remains required for collection controls, exclusions,
retention, schema validation, idempotency, migrations, and tests. This decision
removes only the parallel deterministic interpretation and redaction path.

## Rationale

One mandatory interpretation path is easier to evaluate and improve than two
semantically competing pipelines. A vision-language model can jointly read
screen content and reason about application, window, time, and project context
without a standalone production OCR dependency.

Keeping processing local preserves the core privacy property even though v0
does not mask secrets before inference. Prohibiting remote content transport is
therefore a load-bearing consequence, not a preference.

## Consequences

- The product cannot complete semantic processing when the configured local
  model is unavailable.
- Collection may continue only within the bounded raw-retention window; it
  must expose processing backlog and must never extend raw retention to wait
  for a model.
- A local multimodal model and runtime become installation and resource
  requirements.
- Model prompts, validated outputs, versions, latency, and source provenance
  become inspectable transformation evidence.
- Standalone OCR may be evaluated later as an optimization, not as a required
  alternative interpretation path.
- Secrets can be present in local model inputs. They must not be logged,
  transmitted remotely, or promoted deliberately into durable memory.
- Any future remote provider requires a new ADR and a security boundary before
  it receives user content. This ADR does not authorize one.

## Rollback or replacement

The `ModelProvider` contract keeps the runtime and model replaceable. If a
later pilot proves that a local model cannot meet accuracy or resource targets,
Emi must explicitly choose whether to change models, add local preprocessing,
or reconsider the prohibition on remote processing. No fallback may silently
send content or bypass provenance validation.
