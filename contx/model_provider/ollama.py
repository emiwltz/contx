"""Loopback-only Ollama adapter for the mandatory local multimodal model."""

from __future__ import annotations

import base64
import http.client
import json
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from contx.errors import (
    LocalModelNotInstalledError,
    LocalModelProtocolError,
    LocalModelResponseError,
    LocalModelUnavailableError,
)
from contx.model_provider.base import (
    LocalModelExecution,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    ModelInterpretation,
)
from contx.model_provider.endpoint import LoopbackHttpEndpoint
from contx.model_provider.prompt import SYSTEM_PROMPT
from contx.models import Clock, SystemClock

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024

JsonObject = dict[str, object]
_GRAMMAR_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "additionalProperties",
    }
)


class JsonTransport(Protocol):
    """Small JSON transport contract for an already-validated local endpoint."""

    @property
    def endpoint_url(self) -> str: ...

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> JsonObject: ...


class LoopbackJsonTransport:
    """Bounded JSON-over-HTTP transport that cannot follow redirects."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        connection_factory: Callable[..., http.client.HTTPConnection] | None = None,
    ) -> None:
        self._endpoint = LoopbackHttpEndpoint.parse(endpoint)
        if not 0.1 <= timeout_seconds <= 600:
            raise ValueError("local model timeout must be between 0.1 and 600 seconds")
        if not 1024 <= max_response_bytes <= 10 * 1024 * 1024:
            raise ValueError("local model response limit is outside safe bounds")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._connection_factory = connection_factory or http.client.HTTPConnection

    @property
    def endpoint_url(self) -> str:
        return self._endpoint.url

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> JsonObject:
        if method not in {"GET", "POST"} or not path.startswith("/api/"):
            raise ValueError("local model transport request is not allowed")
        encoded = (
            None
            if payload is None
            else json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        connection = self._connection_factory(
            self._endpoint.host,
            self._endpoint.port,
            timeout=self._timeout_seconds,
        )
        try:
            connection.request(
                method,
                path,
                body=encoded,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read(self._max_response_bytes + 1)
        except (OSError, TimeoutError, http.client.HTTPException):
            raise LocalModelUnavailableError(
                "Local model runtime is unavailable"
            ) from None
        finally:
            connection.close()
        if len(body) > self._max_response_bytes:
            raise LocalModelProtocolError("Local model response exceeded its limit")
        if not 200 <= response.status < 300:
            raise LocalModelUnavailableError(
                f"Local model runtime rejected the request (HTTP {response.status})"
            )
        try:
            decoded: object = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LocalModelProtocolError(
                "Local model runtime returned invalid JSON"
            ) from None
        if not isinstance(decoded, dict):
            raise LocalModelProtocolError(
                "Local model runtime returned an invalid JSON object"
            )
        return cast(JsonObject, decoded)


class _OllamaVersion(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    version: str = Field(min_length=1, max_length=64)


class _OllamaModelTag(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    name: str = Field(min_length=1, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    digest: str = Field(min_length=1, max_length=128)


class _OllamaTags(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    models: list[_OllamaModelTag]


class _OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    role: str
    content: str


class _OllamaChatResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    model: str = Field(min_length=1, max_length=255)
    message: _OllamaMessage
    done: bool
    total_duration: int | None = Field(default=None, ge=0)
    load_duration: int | None = Field(default=None, ge=0)
    prompt_eval_count: int | None = Field(default=None, ge=0)
    eval_count: int | None = Field(default=None, ge=0)


class OllamaModelProvider:
    """Interpret screenshots through one configured Ollama model on loopback."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout_seconds: float = 120.0,
        keep_alive: str = "5m",
        context_tokens: int = 8192,
        max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        clock: Clock | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        if not model.strip() or len(model) > 255 or any(c in model for c in "\r\n"):
            raise ValueError("local model name is invalid")
        if not keep_alive.strip() or len(keep_alive) > 32:
            raise ValueError("local model keep-alive value is invalid")
        if not 2048 <= context_tokens <= 32768:
            raise ValueError(
                "local model context must be between 2048 and 32768 tokens"
            )
        if not 1024 <= max_image_bytes <= 50 * 1024 * 1024:
            raise ValueError("local model image limit is outside safe bounds")
        self._model = model
        self._keep_alive = keep_alive
        self._context_tokens = context_tokens
        self._max_image_bytes = max_image_bytes
        self._clock = clock or SystemClock()
        self._transport = transport or LoopbackJsonTransport(
            endpoint,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        parsed_transport_endpoint = LoopbackHttpEndpoint.parse(
            self._transport.endpoint_url
        )
        configured_endpoint = LoopbackHttpEndpoint.parse(endpoint)
        if parsed_transport_endpoint != configured_endpoint:
            raise ValueError(
                "local model transport endpoint does not match configuration"
            )
        self._healthy_status: LocalModelRuntimeStatus | None = None

    def status(self) -> LocalModelRuntimeStatus:
        try:
            version = _OllamaVersion.model_validate(
                self._transport.request("GET", "/api/version")
            )
            tags = _OllamaTags.model_validate(
                self._transport.request("GET", "/api/tags")
            )
        except LocalModelUnavailableError:
            return self._unhealthy_status("runtime_unavailable")
        except (LocalModelProtocolError, ValidationError):
            return self._unhealthy_status("runtime_invalid_response")
        selected = next(
            (
                tag
                for tag in tags.models
                if tag.name == self._model or tag.model == self._model
            ),
            None,
        )
        status = LocalModelRuntimeStatus(
            endpoint=self._transport.endpoint_url,
            runtime_available=True,
            runtime_version=version.version,
            model=self._model,
            model_available=selected is not None,
            model_digest=None if selected is None else selected.digest,
            reason_code=None if selected is not None else "model_not_installed",
        )
        self._healthy_status = status if status.model_available else None
        return status

    def interpret(self, request: LocalModelRequest) -> LocalModelExecution:
        status = self._healthy_status or self.status()
        if not status.runtime_available or status.runtime_version is None:
            raise LocalModelUnavailableError("Local model runtime is unavailable")
        if not status.model_available or status.model_digest is None:
            raise LocalModelNotInstalledError(
                "The configured local multimodal model is not installed"
            )
        if len(request.image_bytes) > self._max_image_bytes:
            raise LocalModelResponseError("Local model image exceeds its safety limit")

        started_at = self._clock.now()
        response_payload = self._transport.request(
            "POST",
            "/api/chat",
            self._chat_payload(request),
        )
        ended_at = self._clock.now()
        try:
            response = _OllamaChatResponse.model_validate(response_payload)
            if (
                not response.done
                or response.message.role != "assistant"
                or response.model != self._model
            ):
                raise ValueError("incomplete local model response")
            interpretation = ModelInterpretation.model_validate_json(
                response.message.content,
                strict=True,
            )
        except (ValidationError, ValueError):
            raise LocalModelResponseError(
                "Local model returned an invalid structured interpretation"
            ) from None
        return LocalModelExecution(
            request_id=request.id,
            source_observation_ids=request.source_observation_ids,
            endpoint=self._transport.endpoint_url,
            runtime_version=status.runtime_version,
            model=response.model,
            model_digest=status.model_digest,
            image_sha256=request.image_sha256,
            interpretation=interpretation,
            started_at=started_at,
            ended_at=ended_at,
            wall_duration_ms=max(
                0,
                int((ended_at - started_at) / timedelta(milliseconds=1)),
            ),
            runtime_duration_ms=_nanoseconds_to_milliseconds(response.total_duration),
            load_duration_ms=_nanoseconds_to_milliseconds(response.load_duration),
            prompt_eval_count=response.prompt_eval_count,
            eval_count=response.eval_count,
        )

    def _chat_payload(self, request: LocalModelRequest) -> JsonObject:
        metadata = json.dumps(
            request.prompt_metadata(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Interpret this authorized local activity capture. "
                        f"Metadata JSON: {metadata}"
                    ),
                    "images": [base64.b64encode(request.image_bytes).decode("ascii")],
                },
            ],
            "stream": False,
            "format": _ollama_grammar_schema(),
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": self._context_tokens,
            },
            "keep_alive": self._keep_alive,
        }

    def _unhealthy_status(self, reason_code: str) -> LocalModelRuntimeStatus:
        self._healthy_status = None
        return LocalModelRuntimeStatus(
            endpoint=self._transport.endpoint_url,
            runtime_available=False,
            model=self._model,
            model_available=False,
            reason_code=reason_code,
        )


def _nanoseconds_to_milliseconds(value: int | None) -> int | None:
    return None if value is None else value // 1_000_000


def _ollama_grammar_schema() -> JsonObject:
    """Keep runtime-supported grammar while Pydantic enforces full constraints."""
    source = cast(JsonObject, ModelInterpretation.model_json_schema())
    raw_definitions = source.get("$defs")
    definitions = (
        cast(dict[str, object], raw_definitions)
        if isinstance(raw_definitions, dict)
        else {}
    )
    return _simplify_schema_object(source, definitions)


def _simplify_schema_object(
    source: Mapping[str, object],
    definitions: Mapping[str, object],
) -> JsonObject:
    reference = source.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        referenced = definitions.get(reference.removeprefix("#/$defs/"))
        if not isinstance(referenced, dict):
            raise RuntimeError("local model output schema has an invalid reference")
        return _simplify_schema_object(
            cast(dict[str, object], referenced),
            definitions,
        )

    simplified: JsonObject = {}
    for key, value in source.items():
        if key not in _GRAMMAR_SCHEMA_KEYS:
            continue
        if key == "properties":
            if not isinstance(value, dict):
                raise RuntimeError("local model output schema properties are invalid")
            simplified[key] = {
                name: _simplify_schema_object(
                    cast(dict[str, object], definition),
                    definitions,
                )
                for name, definition in value.items()
                if isinstance(name, str) and isinstance(definition, dict)
            }
        elif key == "items":
            if not isinstance(value, dict):
                raise RuntimeError("local model output schema items are invalid")
            simplified[key] = _simplify_schema_object(
                cast(dict[str, object], value),
                definitions,
            )
        else:
            simplified[key] = value
    return simplified
