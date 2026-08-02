"""Loopback and bounded-response guarantees for the Ollama transport."""

import json

import pytest

from contx.errors import LocalModelProtocolError, LocalModelUnavailableError
from contx.model_provider import LoopbackHttpEndpoint, LoopbackJsonTransport


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://127.0.0.1:11434",
        "http://localhost:11434",
        "http://192.168.1.10:11434",
        "http://127.0.0.1",
        "http://user@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?target=remote",
    ),
)
def test_endpoint_rejects_every_non_literal_or_non_loopback_shape(
    endpoint: str,
) -> None:
    with pytest.raises(ValueError, match="local model endpoint"):
        LoopbackHttpEndpoint.parse(endpoint)


def test_endpoint_normalizes_ipv4_and_ipv6_loopback() -> None:
    assert (
        LoopbackHttpEndpoint.parse("http://127.0.0.1:11434/").url
        == "http://127.0.0.1:11434"
    )
    assert LoopbackHttpEndpoint.parse("http://[::1]:11434").url == "http://[::1]:11434"


def test_transport_returns_one_bounded_json_object() -> None:
    connection = FakeConnection(200, json.dumps({"version": "0.32.5"}).encode())
    transport = LoopbackJsonTransport(
        "http://127.0.0.1:11434",
        connection_factory=lambda *_args, **_kwargs: connection,  # type: ignore[arg-type]
    )

    result = transport.request("GET", "/api/version")

    assert result == {"version": "0.32.5"}
    assert connection.requested == (
        "GET",
        "/api/version",
        None,
        {"Content-Type": "application/json"},
    )
    assert connection.closed


def test_transport_does_not_follow_redirect_or_echo_response_body() -> None:
    private_body = b"private-response-content"
    connection = FakeConnection(302, private_body)
    transport = LoopbackJsonTransport(
        "http://127.0.0.1:11434",
        connection_factory=lambda *_args, **_kwargs: connection,  # type: ignore[arg-type]
    )

    with pytest.raises(LocalModelUnavailableError) as caught:
        transport.request("POST", "/api/chat", {"content": "private-request"})

    assert "302" in str(caught.value)
    assert private_body.decode() not in str(caught.value)
    assert connection.requested is not None
    assert connection.requested[1] == "/api/chat"


def test_transport_rejects_oversized_or_invalid_json_without_echoing_it() -> None:
    oversized = FakeConnection(200, b"x" * 1025)
    transport = LoopbackJsonTransport(
        "http://127.0.0.1:11434",
        max_response_bytes=1024,
        connection_factory=lambda *_args, **_kwargs: oversized,  # type: ignore[arg-type]
    )
    with pytest.raises(LocalModelProtocolError, match="exceeded"):
        transport.request("GET", "/api/version")

    private_invalid_json = b"private-invalid-json"
    invalid = FakeConnection(200, private_invalid_json)
    transport = LoopbackJsonTransport(
        "http://127.0.0.1:11434",
        connection_factory=lambda *_args, **_kwargs: invalid,  # type: ignore[arg-type]
    )
    with pytest.raises(LocalModelProtocolError) as caught:
        transport.request("GET", "/api/version")
    assert private_invalid_json.decode() not in str(caught.value)


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


class FakeConnection:
    def __init__(self, status: int, body: bytes) -> None:
        self._response = FakeResponse(status, body)
        self.requested: tuple[str, str, bytes | None, dict[str, str]] | None = None
        self.closed = False

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        self.requested = (method, path, body, headers)

    def getresponse(self) -> FakeResponse:
        return self._response

    def close(self) -> None:
        self.closed = True
