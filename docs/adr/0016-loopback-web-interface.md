# ADR 0016: Serve one same-origin loopback web interface

- **Status:** Accepted
- **Date:** 2026-08-02
- **Decision owner:** Emi

## Context

J6 must make CONTX understandable and controllable without a terminal. The
domain and application services already own collection controls, exclusions,
raw lifecycle, event and memory corrections, proposal adoption, and active
OptMem wake. The browser must not become another source of truth or create a
second semantic context path.

A local HTTP service is still a meaningful security boundary. A malicious web
page can try to send requests to loopback services even when it cannot read
their responses. Host-header attacks, cross-origin mutation, permissive content
types, guessed filesystem paths, and ambiguous destructive actions therefore
remain relevant without network exposure.

## Problem

CONTX needs a replaceable, versioned API and a usable local SPA while
preserving these properties:

- every product decision still runs through an application service;
- the service listens only on loopback by default;
- the UI and API share one origin in the installed path;
- no raw filesystem path is exposed to the browser;
- a missing local model is visible as degradation, never hidden by fallback;
- destructive operations identify their scope and require deliberate
  confirmation;
- the interface can be packaged with the Python application without a second
  runtime service.

## Options considered

1. FastAPI serves a versioned JSON API and a prebuilt React/Vite/TypeScript SPA
   from the same loopback process.
2. A Vite development server remains a permanent second local service and
   proxies to FastAPI.
3. Server-rendered HTML calls repositories directly.
4. A native AppKit or SwiftUI application replaces the planned web interface.

## Decision

Use option 1.

The contract is rooted at `/api/v1`. FastAPI exposes explicit response schemas
backed by a bounded `InspectionService`; mutations call the same application
services as the CLI and daemon. React/Vite/TypeScript produces static files
served by FastAPI. The Vite server exists only for local frontend development
and also binds to `127.0.0.1`.

The production command is `contx web`. It has no host option and always passes
the literal `127.0.0.1` host to Uvicorn. A bounded port option is allowed.

The HTTP boundary applies all of these controls:

- `TrustedHostMiddleware` accepts only literal loopback names plus the
  deterministic test host;
- browser requests with a foreign `Origin` or cross-site `Sec-Fetch-Site` are
  rejected;
- every mutation requires `Content-Type: application/json`;
- the default FastAPI strict content-type behavior remains enabled;
- CORS is not opened because the packaged frontend is same-origin;
- responses disable caching and set restrictive content, framing, referrer,
  and MIME-sniffing headers;
- unmatched `/api/` paths return an API 404 instead of the SPA fallback;
- raw-artifact views expose an observation UUID, type, size, and expiry, but no
  filesystem path and no raw-content route.

Daily pause, resume, and exclusion changes use the durable existing control
service. Event correction remains append-only. Memory correction still
requires the configured local model and creates a superseding OptMem entry.
Proposal adoption still requires explicit user action and local semantic
validation. Active wake is returned directly from the active OptMem projection.

Immediate raw purge requires the exact phrase `DELETE RAW ARTIFACTS` and is
recorded under a distinct `raw_purge_immediate` processing pipeline. Full
deletion requires `DELETE ALL CONTX DATA`, refuses a running collector, rejects
broad, nested, symlinked, or unsupported roots, closes SQLite, removes only the
configured cache, log, and application-support stores, then retires the live
API with HTTP 410. Implementing these paths does not authorize executing them
against real user data.

## Rationale

One same-origin process is the smallest architecture that meets the product
contract. It avoids CORS in normal use, removes a permanent Node runtime, keeps
HTTP serialization separate from domain records, and makes frontend
replacement possible without changing persistence or memory semantics.

Strict host, origin, fetch-site, and content-type checks address the browser
attack surface appropriate to an unauthenticated single-user loopback service.
They do not claim to protect against another process already executing as the
same operating-system user; that threat requires OS-level isolation outside
the v0 boundary.

Explicit read models prevent accidental serialization of private internal
fields. In particular, an `Observation` may hold an artifact path internally,
but the public view represents it only as `has_raw_artifact`. Model
transformation views intentionally expose validated local interpretation and
content-free provenance because inspection is a core product requirement.

## Consequences

- FastAPI and Uvicorn become runtime dependencies; httpx is development-only
  for the in-process API tests.
- React, React DOM, Vite, TypeScript, and type declarations become build-time
  web dependencies. The browser needs no Node runtime after the static build.
- The Python package must contain the verified static build output before J6
  can be marked complete.
- Local-model status uses a short preflight timeout so a missing Ollama process
  cannot stall the dashboard for the full inference timeout.
- Local model corrections, proposal validation, and a first active projection
  build can remain long-running synchronous actions in v0. Operational
  evidence may justify a job API later.
- Settings are inspectable but general configuration editing is deferred;
  daily safety controls are available directly.
- The API has no compatibility promise across pre-v1 major contract changes,
  but changes within `/api/v1` must remain explicit and tested.
- Dependency resolution, frontend type checking/building, FastAPI integration
  tests, packaged-static verification, and browser QA are mandatory before the
  J6 exit gate can pass.

## Rollback or replacement

Another frontend can replace the SPA by consuming `/api/v1`. FastAPI can be
replaced behind the same boundary if the replacement reproduces response
validation, loopback binding, request hardening, application-service routing,
and static-file isolation. Removing the web interface does not require a data
migration because it owns no product state.
