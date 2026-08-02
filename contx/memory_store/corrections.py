"""Local-only structured composition of explicit memory corrections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contx.errors import LocalModelNotInstalledError, LocalModelResponseError
from contx.model_provider import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    LoopbackHttpEndpoint,
    LoopbackJsonTransport,
)
from contx.model_provider.ollama import JsonObject, JsonTransport

MEMORY_CORRECTION_PROMPT_VERSION = "memory-correction-v1"
MEMORY_CORRECTION_OUTPUT_SCHEMA_VERSION = "memory-correction-output-v1"
DEFAULT_CORRECTION_OUTPUT_TOKENS = 128
DEFAULT_MAX_CORRECTION_PROMPT_BYTES = 16 * 1024
CORRECTION_PREFIX = "Correction: "

_SYSTEM_PROMPT = """You compose an explicit correction for private local memory.
Use only the supplied original memory and user-authorized replacement. Return
one autonomous current fact that makes the older conflicting claim obsolete.
Preserve the evidence language. Do not add a date, chronology, identifiers,
commentary, or facts that were not supplied. Return only the required JSON."""


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


class _CorrectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    memory: str = Field(min_length=1, max_length=280, repr=False)


class OllamaMemoryCorrectionComposer:
    """Compose one bounded correction through a literal loopback Ollama API."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 120.0,
        keep_alive: str = "5m",
        context_tokens: int = 8192,
        max_output_tokens: int = DEFAULT_CORRECTION_OUTPUT_TOKENS,
        max_prompt_bytes: int = DEFAULT_MAX_CORRECTION_PROMPT_BYTES,
        transport: JsonTransport | None = None,
    ) -> None:
        if not model.strip() or len(model) > 255 or any(c in model for c in "\r\n"):
            raise ValueError("local correction model name is invalid")
        if not keep_alive.strip() or len(keep_alive) > 32:
            raise ValueError("local correction keep-alive value is invalid")
        if not 2048 <= context_tokens <= 32768:
            raise ValueError("local correction context is outside safe bounds")
        if not 64 <= max_output_tokens <= 512:
            raise ValueError("local correction output limit is outside safe bounds")
        if not 1024 <= max_prompt_bytes <= 64 * 1024:
            raise ValueError("local correction prompt limit is outside safe bounds")
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
                "local correction transport endpoint does not match configuration"
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
        return MEMORY_CORRECTION_PROMPT_VERSION

    @property
    def output_schema_version(self) -> str:
        return MEMORY_CORRECTION_OUTPUT_SCHEMA_VERSION

    def compose(
        self,
        *,
        original: str,
        replacement: str,
        max_bytes: int,
    ) -> str:
        content_budget = max_bytes - len(CORRECTION_PREFIX.encode("utf-8"))
        if content_budget < 1:
            raise ValueError("correction byte limit cannot fit its explicit prefix")
        prompt = _correction_prompt(
            original=original,
            replacement=replacement,
            content_budget=content_budget,
        )
        if len(prompt.encode("utf-8")) > self._max_prompt_bytes:
            raise LocalModelResponseError(
                "Local memory correction prompt exceeded its safety limit"
            )
        self._require_model()
        payload = self._transport.request(
            "POST",
            "/api/chat",
            self._chat_payload(prompt),
        )
        try:
            response = _ChatResponse.model_validate(payload)
        except ValidationError:
            raise LocalModelResponseError(
                "Local memory correction returned an invalid response envelope"
            ) from None
        if (
            not response.done
            or response.message.role != "assistant"
            or response.model != self._model
        ):
            raise LocalModelResponseError(
                "Local memory correction response identity was invalid"
            )
        try:
            output = _CorrectionOutput.model_validate_json(
                response.message.content,
                strict=True,
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local memory correction returned invalid structured output"
            ) from None
        body = _normalize_body(output.memory)
        correction = CORRECTION_PREFIX + body
        if len(correction.encode("utf-8")) > max_bytes:
            raise LocalModelResponseError(
                "Local memory correction exceeded the backend byte limit"
            )
        return correction

    def _require_model(self) -> None:
        try:
            tags = _ModelTags.model_validate(
                self._transport.request("GET", "/api/tags")
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local memory correction returned invalid model metadata"
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
                "The configured local memory correction model is not installed"
            )
        self._model_digest = selected.digest

    def _chat_payload(self, prompt: str) -> Mapping[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": _correction_schema(),
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": self._context_tokens,
                "num_predict": self._max_output_tokens,
            },
            "keep_alive": self._keep_alive,
        }


def _correction_prompt(
    *,
    original: str,
    replacement: str,
    content_budget: int,
) -> str:
    return (
        f"Maximum output body: {content_budget} UTF-8 bytes. CONTX will prepend "
        f"{CORRECTION_PREFIX!r}.\n\n"
        f"Original memory:\n{original}\n\n"
        f"User-authorized current replacement:\n{replacement}"
    )


def _normalize_body(value: str) -> str:
    normalized = value.strip()
    if normalized.lower().startswith(CORRECTION_PREFIX.lower()):
        normalized = normalized[len(CORRECTION_PREFIX) :].lstrip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        raise LocalModelResponseError(
            "Local memory correction must be one non-empty line"
        )
    return normalized


def _correction_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {"memory": {"type": "string"}},
        "required": ["memory"],
        "additionalProperties": False,
    }
