"""Ollama provider contract tests without network or real user data."""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

import pytest

from contx.errors import (
    LocalModelNotInstalledError,
    LocalModelResponseError,
    LocalModelUnavailableError,
)
from contx.model_provider import (
    DEFAULT_MODEL,
    LocalModelRequest,
    OllamaModelProvider,
)
from contx.model_provider.ollama import JsonObject
from contx.models import ActivityState
from tests.helpers import FixedClock

NOW = datetime(2026, 8, 2, 18, 0, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\nsynthetic-provider-fixture"
DIGEST = f"sha256:{'a' * 64}"


def test_status_proves_runtime_model_and_digest() -> None:
    transport = RecordingTransport()
    provider = _provider(transport)

    status = provider.status()

    assert status.runtime_available
    assert status.runtime_version == "0.32.5"
    assert status.model_available
    assert status.model == DEFAULT_MODEL
    assert status.model_digest == DIGEST
    assert [call[:2] for call in transport.calls] == [
        ("GET", "/api/version"),
        ("GET", "/api/tags"),
    ]


def test_interpretation_uses_image_and_strict_json_schema_locally() -> None:
    transport = RecordingTransport()
    provider = _provider(transport)

    execution = provider.interpret(_request())

    method, path, payload = transport.calls[-1]
    assert method == "POST"
    assert path == "/api/chat"
    assert payload is not None
    assert payload["model"] == DEFAULT_MODEL
    assert payload["stream"] is False
    assert payload["think"] is False
    assert isinstance(payload["format"], dict)
    assert payload["format"]["additionalProperties"] is False  # type: ignore[index]
    assert "$defs" not in payload["format"]
    assert "pattern" not in json.dumps(payload["format"])
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[1]["images"]  # type: ignore[index]
    options = payload["options"]
    assert isinstance(options, dict)
    assert options["num_predict"] == 512
    assert execution.model_digest == DIGEST
    assert execution.interpretation.projects == ("CONTX",)
    assert execution.runtime_duration_ms == 12
    assert "synthetic-provider-fixture" not in repr(execution)


def test_missing_model_fails_before_chat() -> None:
    transport = RecordingTransport(model_installed=False)
    provider = _provider(transport)

    with pytest.raises(LocalModelNotInstalledError, match="not installed"):
        provider.interpret(_request())

    assert not any(path == "/api/chat" for _, path, _ in transport.calls)


def test_unavailable_runtime_returns_safe_status_and_safe_error() -> None:
    transport = RecordingTransport(unavailable=True)
    provider = _provider(transport)

    status = provider.status()
    with pytest.raises(LocalModelUnavailableError) as caught:
        provider.interpret(_request())

    assert not status.runtime_available
    assert status.reason_code == "runtime_unavailable"
    assert "private-runtime-detail" not in str(caught.value)


def test_invalid_model_content_is_rejected_without_echoing_it() -> None:
    private_content = "private-invalid-model-content"
    transport = RecordingTransport(chat_content=private_content)
    provider = _provider(transport)

    with pytest.raises(LocalModelResponseError) as caught:
        provider.interpret(_request())

    assert private_content not in str(caught.value)


def test_invalid_model_field_name_is_not_echoed_in_safe_diagnostic() -> None:
    private_field = "private-generated-field-name"
    payload = json.loads(RecordingTransport().chat_content)
    payload[private_field] = "synthetic-private-value"
    transport = RecordingTransport(chat_content=json.dumps(payload))
    provider = _provider(transport)

    with pytest.raises(LocalModelResponseError) as caught:
        provider.interpret(_request())

    assert private_field not in str(caught.value)
    assert "field:extra_forbidden" in str(caught.value)


def test_response_from_another_model_identity_is_rejected() -> None:
    transport = RecordingTransport(response_model="other-local-model")
    provider = _provider(transport)

    with pytest.raises(LocalModelResponseError, match="identity or completion"):
        provider.interpret(_request())


@pytest.mark.parametrize("max_output_tokens", (127, 2049))
def test_output_token_limit_is_bounded(max_output_tokens: int) -> None:
    with pytest.raises(ValueError, match="output limit"):
        OllamaModelProvider(
            transport=RecordingTransport(),
            clock=FixedClock(NOW),
            max_output_tokens=max_output_tokens,
        )


class RecordingTransport:
    endpoint_url = "http://127.0.0.1:11434"

    def __init__(
        self,
        *,
        model_installed: bool = True,
        unavailable: bool = False,
        chat_content: str | None = None,
        response_model: str = DEFAULT_MODEL,
    ) -> None:
        self.model_installed = model_installed
        self.unavailable = unavailable
        self.chat_content = chat_content or json.dumps(
            {
                "summary": "Editing the CONTX local model boundary.",
                "activity_type": "coding",
                "observed_facts": ["Source code and tests are visible."],
                "inferred_context": ["The CONTX project is being developed."],
                "projects": ["CONTX"],
                "entities": ["Ollama"],
                "sensitivity": "personal",
                "sensitive_categories": [],
                "confidence": 0.91,
                "memory_relevance": 0.82,
            }
        )
        self.response_model = response_model
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> JsonObject:
        self.calls.append((method, path, payload))
        if self.unavailable:
            raise LocalModelUnavailableError("private-runtime-detail")
        if path == "/api/version":
            return {"version": "0.32.5"}
        if path == "/api/tags":
            return {
                "models": (
                    [
                        {
                            "name": DEFAULT_MODEL,
                            "model": DEFAULT_MODEL,
                            "digest": DIGEST,
                        }
                    ]
                    if self.model_installed
                    else []
                )
            }
        if path == "/api/chat":
            return {
                "model": self.response_model,
                "message": {"role": "assistant", "content": self.chat_content},
                "done": True,
                "total_duration": 12_800_000,
                "load_duration": 2_100_000,
                "prompt_eval_count": 120,
                "eval_count": 80,
            }
        raise AssertionError(f"unexpected path: {path}")


def _provider(transport: RecordingTransport) -> OllamaModelProvider:
    return OllamaModelProvider(
        transport=transport,
        clock=FixedClock(NOW),
    )


def _request() -> LocalModelRequest:
    return LocalModelRequest(
        id=UUID(int=1),
        source_observation_ids=(UUID(int=2),),
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW,
        activity_state=ActivityState.ACTIVE,
        app_name="Synthetic Editor",
        app_bundle_id="com.example.editor",
        window_title="CONTX local model implementation",
        image_sha256=hashlib.sha256(PNG).hexdigest(),
        image_bytes=PNG,
    )
