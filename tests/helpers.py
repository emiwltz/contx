"""Deterministic test sources."""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class SequenceIdentifiers:
    def __init__(self, values: Iterable[UUID]) -> None:
        self._values = iter(values)

    def new(self) -> UUID:
        return next(self._values)
