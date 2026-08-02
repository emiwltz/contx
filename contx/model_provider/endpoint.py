"""Strict loopback endpoint parsing for local model transports."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class LoopbackHttpEndpoint:
    """An HTTP endpoint whose host is a literal loopback IP address."""

    host: str
    port: int

    @classmethod
    def parse(cls, value: str) -> LoopbackHttpEndpoint:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("local model endpoint is invalid") from error
        if (
            parsed.scheme != "http"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.hostname is None
            or port is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "local model endpoint must be an HTTP loopback address with a port"
            )
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as error:
            raise ValueError(
                "local model endpoint host must be a literal loopback address"
            ) from error
        if not address.is_loopback:
            raise ValueError("local model endpoint must stay on loopback")
        return cls(host=address.compressed, port=port)

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"
