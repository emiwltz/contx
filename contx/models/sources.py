"""Injectable time and identifier sources."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4


class Clock(Protocol):
    """Provide the current aware UTC time."""

    def now(self) -> datetime: ...


class IdentifierSource(Protocol):
    """Provide unique record identifiers."""

    def new(self) -> UUID: ...


class SystemClock:
    """Production wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdentifierSource:
    """Production UUID4 identifier source."""

    def new(self) -> UUID:
        return uuid4()
