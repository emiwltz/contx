"""Dedicated local-model memory correction composition tests."""

import json
from collections.abc import Mapping

import pytest

from contx.errors import LocalModelNotInstalledError, LocalModelResponseError
from contx.memory_store import OllamaMemoryCorrectionComposer
from contx.model_provider import DEFAULT_MODEL
from contx.model_provider.ollama import JsonObject

DIGEST = f"sha256:{'b' * 64}"


def test_correction_uses_structured_loopback_and_explicit_protocol_prefix() -> None:
    transport = RecordingTransport()
    composer = OllamaMemoryCorrectionComposer(transport=transport)

    correction = composer.compose(
        original="Atlas deploys to staging.",
        replacement="Atlas deploys locally only.",
        max_bytes=280,
    )

    assert correction == "Correction: Atlas deploys locally only."
    assert composer.model_digest == DIGEST
    assert [call[:2] for call in transport.calls] == [
        ("GET", "/api/tags"),
        ("POST", "/api/chat"),
    ]
    payload = transport.calls[-1][2]
    assert payload is not None
    assert payload["think"] is False
    assert payload["format"] == {
        "type": "object",
        "properties": {"memory": {"type": "string"}},
        "required": ["memory"],
        "additionalProperties": False,
    }
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "Atlas deploys to staging." in str(messages[-1])
    assert "Atlas deploys locally only." in str(messages[-1])
    assert "2026-" not in str(messages)


def test_existing_prefix_is_not_duplicated_and_byte_limit_is_enforced() -> None:
    prefixed = OllamaMemoryCorrectionComposer(
        transport=RecordingTransport(
            content=json.dumps({"memory": "Correction: Atlas is local."})
        )
    )
    assert (
        prefixed.compose(
            original="Atlas is remote.",
            replacement="Atlas is local.",
            max_bytes=280,
        )
        == "Correction: Atlas is local."
    )

    overflowing = OllamaMemoryCorrectionComposer(
        transport=RecordingTransport(content=json.dumps({"memory": "é" * 135}))
    )
    with pytest.raises(LocalModelResponseError, match="byte limit"):
        overflowing.compose(
            original="Atlas is remote.",
            replacement="Atlas is local.",
            max_bytes=280,
        )


def test_missing_model_and_invalid_private_output_fail_without_echo() -> None:
    missing = OllamaMemoryCorrectionComposer(
        transport=RecordingTransport(model_installed=False)
    )
    with pytest.raises(LocalModelNotInstalledError):
        missing.compose(
            original="old",
            replacement="new",
            max_bytes=280,
        )

    private_output = "private-invalid-correction-output"
    invalid = OllamaMemoryCorrectionComposer(
        transport=RecordingTransport(content=private_output)
    )
    with pytest.raises(LocalModelResponseError) as caught:
        invalid.compose(
            original="old",
            replacement="new",
            max_bytes=280,
        )
    assert private_output not in str(caught.value)


class RecordingTransport:
    endpoint_url = "http://127.0.0.1:11434"

    def __init__(
        self,
        *,
        model_installed: bool = True,
        content: str | None = None,
    ) -> None:
        self.model_installed = model_installed
        self.content = content or json.dumps({"memory": "Atlas deploys locally only."})
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> JsonObject:
        self.calls.append((method, path, payload))
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
                "model": DEFAULT_MODEL,
                "message": {"role": "assistant", "content": self.content},
                "done": True,
            }
        raise AssertionError(f"unexpected path: {path}")
