"""Local-only Ollama compression for OptMem maintenance prompts."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contx.errors import (
    LocalModelNotInstalledError,
    LocalModelResponseError,
)
from contx.memory_store.base import MemoryCompressionRequest
from contx.model_provider import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    LoopbackHttpEndpoint,
    LoopbackJsonTransport,
)
from contx.model_provider.ollama import JsonObject, JsonTransport

MEMORY_COMPRESSION_PROMPT_VERSION = "memory-compression-v1"
DEFAULT_COMPRESSION_OUTPUT_TOKENS = 128
DEFAULT_MAX_COMPRESSION_PROMPT_BYTES = 16 * 1024

_SYSTEM_PROMPT = """You maintain a private local memory compression tree.
Compress only the supplied memory evidence into one autonomous line.
Preserve facts with lasting effect, prefer a newer explicit correction over an
older conflicting claim, drop transient detail, and invent nothing. Return only
the required JSON object. Do not add commentary, Markdown, or identifiers."""


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


class _CompressionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    summary: str = Field(min_length=1, max_length=280, repr=False)


class OllamaMemoryCompressor:
    """Generate bounded OptMem tree summaries through literal loopback only."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 120.0,
        keep_alive: str = "5m",
        context_tokens: int = 8192,
        max_output_tokens: int = DEFAULT_COMPRESSION_OUTPUT_TOKENS,
        max_prompt_bytes: int = DEFAULT_MAX_COMPRESSION_PROMPT_BYTES,
        transport: JsonTransport | None = None,
    ) -> None:
        if not model.strip() or len(model) > 255 or any(c in model for c in "\r\n"):
            raise ValueError("local compression model name is invalid")
        if not keep_alive.strip() or len(keep_alive) > 32:
            raise ValueError("local compression keep-alive value is invalid")
        if not 2048 <= context_tokens <= 32768:
            raise ValueError("local compression context is outside safe bounds")
        if not 64 <= max_output_tokens <= 512:
            raise ValueError("local compression output limit is outside safe bounds")
        if not 1024 <= max_prompt_bytes <= 64 * 1024:
            raise ValueError("local compression prompt limit is outside safe bounds")
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
                "local compression transport endpoint does not match configuration"
            )
        self._model_digest: str | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def model_digest(self) -> str | None:
        return self._model_digest

    def compress(self, request: MemoryCompressionRequest) -> str:
        if len(request.prompt.encode("utf-8")) > self._max_prompt_bytes:
            raise LocalModelResponseError(
                "Local memory compression prompt exceeded its safety limit"
            )
        self._require_model()
        payload = self._transport.request(
            "POST",
            "/api/chat",
            self._chat_payload(request),
        )
        try:
            response = _ChatResponse.model_validate(payload)
        except ValidationError:
            raise LocalModelResponseError(
                "Local memory compressor returned an invalid response envelope"
            ) from None
        if (
            not response.done
            or response.message.role != "assistant"
            or response.model != self._model
        ):
            raise LocalModelResponseError(
                "Local memory compressor response identity or completion was invalid"
            )
        try:
            output = _CompressionOutput.model_validate_json(
                response.message.content,
                strict=True,
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local memory compressor returned an invalid structured summary"
            ) from None
        if "\n" in output.summary or "\r" in output.summary:
            raise LocalModelResponseError(
                "Local memory compressor returned a multiline summary"
            )
        if len(output.summary.strip().encode("utf-8")) > request.max_bytes:
            raise LocalModelResponseError(
                "Local memory compressor exceeded the backend byte limit"
            )
        return output.summary.strip()

    def _require_model(self) -> None:
        try:
            tags = _ModelTags.model_validate(
                self._transport.request("GET", "/api/tags")
            )
        except ValidationError:
            raise LocalModelResponseError(
                "Local memory compressor returned invalid model metadata"
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
                "The configured local memory compression model is not installed"
            )
        self._model_digest = selected.digest

    def _chat_payload(self, request: MemoryCompressionRequest) -> Mapping[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Backend byte limit: {request.max_bytes}.\n\n{request.prompt}"
                    ),
                },
            ],
            "stream": False,
            "think": False,
            "format": _summary_schema(),
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": self._context_tokens,
                "num_predict": self._max_output_tokens,
            },
            "keep_alive": self._keep_alive,
        }


def _summary_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
            }
        },
        "required": ["summary"],
        "additionalProperties": False,
    }
