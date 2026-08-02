"""Local-only semantic validation for explicit agent-proposal adoption."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contx.errors import LocalModelNotInstalledError, LocalModelResponseError
from contx.memory_store.base import AgentProposalEvaluation
from contx.model_provider import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    LoopbackHttpEndpoint,
    LoopbackJsonTransport,
)
from contx.model_provider.ollama import JsonObject, JsonTransport

AGENT_PROPOSAL_PROMPT_VERSION = "agent-proposal-adoption-v2"
AGENT_PROPOSAL_OUTPUT_SCHEMA_VERSION = "agent-proposal-adoption-output-v2"
DEFAULT_PROPOSAL_OUTPUT_TOKENS = 128
DEFAULT_MAX_PROPOSAL_PROMPT_BYTES = 32 * 1024

_SYSTEM_PROMPT = """You validate one agent-authored line before it is appended
unchanged to private local memory. The supplied reference is the only source of
truth. Evaluate the required booleans before choosing the decision. A proposal
is a semantic duplicate when it teaches no new durable fact compared with an
active-memory line, even if synonyms, grammar, or word order differ. For
example, 'deploys locally only' and 'deployment runs only on the local machine'
are duplicates. An exact or semantic duplicate MUST be rejected with
duplicate_active_memory, and a contradiction MUST be rejected with
conflicts_with_active_memory. That reason applies only to a supplied active
memory line. A proposal contradicted by its supporting reference is
unsupported_by_reference. Otherwise, accept only when every substantive claim
is directly supported and the line is useful as durable autonomous context.
Reject unsupported claims. Defer only when the reference is genuinely
ambiguous. Do not rewrite the proposal. Return only the required JSON."""

_EQUIVALENCE_SYSTEM_PROMPT = """Decide only whether the proposed memory and
any active-memory line communicate the same durable fact. Ignore synonyms,
grammar, and word order. Equivalent paraphrases MUST return true. For example,
'deploys locally only' and 'deployment runs only on the local machine' are
equivalent. Return only the required JSON."""


class _ModelTag(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    digest: str = Field(min_length=1, max_length=128)


class _ModelTags(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    models: list[_ModelTag]


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    role: str
    content: str = Field(repr=False)


class _ChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    model: str = Field(min_length=1, max_length=255)
    message: _Message
    done: bool


class _EvaluationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision: Literal["accepted", "rejected", "deferred"]
    reason_code: Literal[
        "supported_novel",
        "unsupported_by_reference",
        "duplicate_active_memory",
        "conflicts_with_active_memory",
        "ambiguous_reference",
    ]
    fully_supported: bool
    semantic_duplicate: bool
    conflicts_with_active_memory: bool
    reference_ambiguous: bool
    confidence: float = Field(ge=0.0, le=1.0)


class _EquivalenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    equivalent: bool
    confidence: float = Field(ge=0.0, le=1.0)


class OllamaAgentProposalEvaluator:
    """Validate one proposal through a literal loopback Ollama API."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 120.0,
        keep_alive: str = "5m",
        context_tokens: int = 8192,
        max_output_tokens: int = DEFAULT_PROPOSAL_OUTPUT_TOKENS,
        max_prompt_bytes: int = DEFAULT_MAX_PROPOSAL_PROMPT_BYTES,
        transport: JsonTransport | None = None,
    ) -> None:
        if not model.strip() or len(model) > 255 or any(c in model for c in "\r\n"):
            raise ValueError("local proposal model name is invalid")
        if not keep_alive.strip() or len(keep_alive) > 32:
            raise ValueError("local proposal keep-alive value is invalid")
        if not 2048 <= context_tokens <= 32768:
            raise ValueError("local proposal context is outside safe bounds")
        if not 64 <= max_output_tokens <= 512:
            raise ValueError("local proposal output limit is outside safe bounds")
        if not 4096 <= max_prompt_bytes <= 64 * 1024:
            raise ValueError("local proposal prompt limit is outside safe bounds")
        self._model = model
        self._keep_alive = keep_alive
        self._context_tokens = context_tokens
        self._max_output_tokens = max_output_tokens
        self._max_prompt_bytes = max_prompt_bytes
        self._transport = transport or LoopbackJsonTransport(
            endpoint,
            timeout_seconds=timeout_seconds,
        )
        if LoopbackHttpEndpoint.parse(self._transport.endpoint_url) != (
            LoopbackHttpEndpoint.parse(endpoint)
        ):
            raise ValueError(
                "local proposal transport endpoint does not match configuration"
            )
        self._model_digest: str | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> Literal["ollama"]:
        return "ollama"

    @property
    def endpoint(self) -> str:
        return self._transport.endpoint_url

    @property
    def model_digest(self) -> str | None:
        return self._model_digest

    @property
    def prompt_version(self) -> str:
        return AGENT_PROPOSAL_PROMPT_VERSION

    @property
    def output_schema_version(self) -> str:
        return AGENT_PROPOSAL_OUTPUT_SCHEMA_VERSION

    def evaluate(
        self,
        *,
        proposal: str,
        reference: str,
        active_memories: tuple[str, ...],
        minimum_confidence: float,
    ) -> AgentProposalEvaluation:
        if not 0.5 <= minimum_confidence <= 1.0:
            raise ValueError("proposal confidence threshold is invalid")
        prompt = _proposal_prompt(
            proposal=proposal,
            reference=reference,
            active_memories=active_memories,
            minimum_confidence=minimum_confidence,
        )
        if len(prompt.encode("utf-8")) > self._max_prompt_bytes:
            raise LocalModelResponseError(
                "Local proposal validation prompt exceeded its safety limit"
            )
        self._require_model()
        payload = self._transport.request(
            "POST",
            "/api/chat",
            self._chat_payload(prompt, schema=_evaluation_schema()),
        )
        try:
            response = _ChatResponse.model_validate(payload)
        except ValidationError:
            raise LocalModelResponseError(
                "Local proposal validation returned an invalid response envelope"
            ) from None
        if (
            not response.done
            or response.message.role != "assistant"
            or response.model != self._model
        ):
            raise LocalModelResponseError(
                "Local proposal validation response identity was invalid"
            )
        try:
            output = _EvaluationOutput.model_validate_json(
                response.message.content,
                strict=True,
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local proposal validation returned invalid structured output"
            ) from None
        if _has_exact_duplicate(proposal, active_memories):
            return AgentProposalEvaluation(
                decision="rejected",
                reason_code="duplicate_active_memory",
                confidence=1.0,
            )
        if (
            output.decision == "rejected"
            and not output.fully_supported
            and not output.reference_ambiguous
        ):
            return AgentProposalEvaluation(
                decision="rejected",
                reason_code="unsupported_by_reference",
                confidence=output.confidence,
            )
        _validate_decision(output, minimum_confidence=minimum_confidence)
        if (
            not active_memories
            and output.reason_code
            in {"duplicate_active_memory", "conflicts_with_active_memory"}
        ):
            raise LocalModelResponseError(
                "Local proposal validation cited unavailable active memory"
            )
        if output.fully_supported and active_memories:
            equivalence = self._evaluate_equivalence(
                proposal=proposal,
                active_memories=active_memories,
                minimum_confidence=minimum_confidence,
            )
            if equivalence.equivalent:
                return AgentProposalEvaluation(
                    decision="rejected",
                    reason_code="duplicate_active_memory",
                    confidence=equivalence.confidence,
                )
        return AgentProposalEvaluation(
            decision=output.decision,
            reason_code=output.reason_code,
            confidence=output.confidence,
        )

    def _require_model(self) -> None:
        try:
            tags = _ModelTags.model_validate(
                self._transport.request("GET", "/api/tags")
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local proposal validation returned invalid model metadata"
            ) from None
        selected = next(
            (
                tag
                for tag in tags.models
                if tag.name == self._model or tag.model == self._model
            ),
            None,
        )
        if selected is None:
            raise LocalModelNotInstalledError(
                "The configured local proposal validation model is not installed"
            )
        self._model_digest = selected.digest

    def _evaluate_equivalence(
        self,
        *,
        proposal: str,
        active_memories: tuple[str, ...],
        minimum_confidence: float,
    ) -> _EquivalenceOutput:
        prompt = json.dumps(
            {
                "proposed_memory": proposal,
                "active_memories": list(active_memories),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(prompt.encode("utf-8")) > self._max_prompt_bytes:
            raise LocalModelResponseError(
                "Local proposal equivalence prompt exceeded its safety limit"
            )
        payload = self._transport.request(
            "POST",
            "/api/chat",
            self._chat_payload(
                prompt,
                schema=_equivalence_schema(),
                system_prompt=_EQUIVALENCE_SYSTEM_PROMPT,
            ),
        )
        try:
            response = _ChatResponse.model_validate(payload)
        except ValidationError:
            raise LocalModelResponseError(
                "Local proposal equivalence returned an invalid response envelope"
            ) from None
        if (
            not response.done
            or response.message.role != "assistant"
            or response.model != self._model
        ):
            raise LocalModelResponseError(
                "Local proposal equivalence response identity was invalid"
            )
        try:
            output = _EquivalenceOutput.model_validate_json(
                response.message.content,
                strict=True,
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local proposal equivalence returned invalid structured output"
            ) from None
        if output.confidence < minimum_confidence:
            raise LocalModelResponseError(
                "Local proposal equivalence was below the confidence threshold"
            )
        return output

    def _chat_payload(
        self,
        prompt: str,
        *,
        schema: JsonObject,
        system_prompt: str = _SYSTEM_PROMPT,
    ) -> Mapping[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": schema,
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": self._context_tokens,
                "num_predict": self._max_output_tokens,
            },
            "keep_alive": self._keep_alive,
        }


def _proposal_prompt(
    *,
    proposal: str,
    reference: str,
    active_memories: tuple[str, ...],
    minimum_confidence: float,
) -> str:
    payload = {
        "proposal_appended_unchanged": proposal,
        "supporting_reference": json.loads(reference),
        "active_memory_for_duplicate_and_conflict_check": list(active_memories),
        "minimum_confidence_for_acceptance": minimum_confidence,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_decision(
    output: _EvaluationOutput,
    *,
    minimum_confidence: float,
) -> None:
    expected = {
        ("accepted", "supported_novel"): (
            True,
            False,
            False,
            False,
        ),
        ("rejected", "unsupported_by_reference"): (
            False,
            False,
            False,
            False,
        ),
        ("rejected", "duplicate_active_memory"): (
            True,
            True,
            False,
            False,
        ),
        ("rejected", "conflicts_with_active_memory"): (
            True,
            False,
            True,
            False,
        ),
        ("deferred", "ambiguous_reference"): (
            False,
            False,
            False,
            True,
        ),
    }
    actual = (
        output.fully_supported,
        output.semantic_duplicate,
        output.conflicts_with_active_memory,
        output.reference_ambiguous,
    )
    if expected.get((output.decision, output.reason_code)) != actual:
        raise LocalModelResponseError(
            "Local proposal validation decision and reason were inconsistent"
        )
    if output.decision == "accepted" and output.confidence < minimum_confidence:
        raise LocalModelResponseError(
            "Local proposal validation accepted below the confidence threshold"
        )


def _has_exact_duplicate(proposal: str, active_memories: tuple[str, ...]) -> bool:
    normalized = " ".join(proposal.casefold().split())
    return any(
        " ".join(memory.casefold().split()) == normalized
        for memory in active_memories
    )


def _evaluation_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["accepted", "rejected", "deferred"],
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "supported_novel",
                    "unsupported_by_reference",
                    "duplicate_active_memory",
                    "conflicts_with_active_memory",
                    "ambiguous_reference",
                ],
            },
            "fully_supported": {"type": "boolean"},
            "semantic_duplicate": {"type": "boolean"},
            "conflicts_with_active_memory": {"type": "boolean"},
            "reference_ambiguous": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
        "required": [
            "decision",
            "reason_code",
            "fully_supported",
            "semantic_duplicate",
            "conflicts_with_active_memory",
            "reference_ambiguous",
            "confidence",
        ],
        "additionalProperties": False,
    }


def _equivalence_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "equivalent": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
        "required": ["equivalent", "confidence"],
        "additionalProperties": False,
    }
