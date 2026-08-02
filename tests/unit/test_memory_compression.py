"""Local-only structured memory compression contracts."""

import json
from collections.abc import Mapping

import pytest

from contx.errors import LocalModelNotInstalledError, LocalModelResponseError
from contx.memory_store import MemoryCompressionRequest, OllamaMemoryCompressor
from contx.model_provider import DEFAULT_MODEL
from contx.model_provider.ollama import JsonObject

DIGEST = f"sha256:{'a' * 64}"


def test_compression_uses_bounded_structured_loopback_request() -> None:
    transport = RecordingTransport()
    compressor = OllamaMemoryCompressor(transport=transport)
    request = _request()

    summary = compressor.compress(request)

    assert summary == "Atlas now targets local execution only."
    assert compressor.model_digest == DIGEST
    assert [call[:2] for call in transport.calls] == [
        ("GET", "/api/tags"),
        ("POST", "/api/chat"),
    ]
    payload = transport.calls[-1][2]
    assert payload is not None
    assert payload["model"] == DEFAULT_MODEL
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["format"] == {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
        "additionalProperties": False,
    }
    options = payload["options"]
    assert isinstance(options, dict)
    assert options["num_predict"] == 128
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "Correction:" in str(messages[0])
    assert "never turn" in str(messages[0])
    assert "survive every merge-tree" in str(messages[0])
    assert "private-memory-evidence" not in repr(request)


def test_missing_model_and_invalid_private_output_fail_safely() -> None:
    missing = OllamaMemoryCompressor(
        transport=RecordingTransport(model_installed=False)
    )
    with pytest.raises(LocalModelNotInstalledError):
        missing.compress(_request())

    private_output = "private-invalid-compression-output"
    invalid = OllamaMemoryCompressor(
        transport=RecordingTransport(chat_content=private_output)
    )
    with pytest.raises(LocalModelResponseError) as caught:
        invalid.compress(_request())

    assert private_output not in str(caught.value)


def test_compression_rejects_backend_byte_overflow() -> None:
    compressor = OllamaMemoryCompressor(
        transport=RecordingTransport(chat_content=json.dumps({"summary": "é" * 141}))
    )

    with pytest.raises(LocalModelResponseError, match="byte limit"):
        compressor.compress(_request())


def test_compression_preserves_correction_authority_across_tree_merges() -> None:
    request = MemoryCompressionRequest(
        block="0-1",
        prompt=(
            "Compress memories #0-1.\n"
            "  #0 2026-08-02 Atlas targets staging.\n"
            "  #1 2026-08-02 Correction: Atlas targets local-only."
        ),
        max_bytes=280,
    )
    missing_marker = OllamaMemoryCompressor(
        transport=RecordingTransport(
            chat_content=json.dumps({"summary": "Atlas targets local-only."})
        )
    )

    assert (
        missing_marker.compress(request)
        == "Correction: Atlas targets local-only."
    )

    retained_transport = RecordingTransport(
        chat_content=json.dumps(
            {"summary": "Correction: Atlas targets local-only."}
        )
    )
    retained = OllamaMemoryCompressor(transport=retained_transport)
    assert retained.compress(request).startswith("Correction:")

    payload = retained_transport.calls[-1][2]
    assert payload is not None
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "Newest supplied correction" in str(messages[-1])
    assert "Correction: Atlas targets local-only." in str(messages[-1])


class RecordingTransport:
    endpoint_url = "http://127.0.0.1:11434"

    def __init__(
        self,
        *,
        model_installed: bool = True,
        chat_content: str | None = None,
    ) -> None:
        self.model_installed = model_installed
        self.chat_content = chat_content or json.dumps(
            {"summary": "Atlas now targets local execution only."}
        )
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
                "message": {"role": "assistant", "content": self.chat_content},
                "done": True,
            }
        raise AssertionError(f"unexpected path: {path}")


def _request() -> MemoryCompressionRequest:
    return MemoryCompressionRequest(
        block="0-1",
        prompt=(
            "Compress memories #0-1.\n"
            "#0 private-memory-evidence\n"
            "#1 corrected synthetic evidence"
        ),
        max_bytes=280,
    )
