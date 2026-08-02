"""Collector contract."""

from typing import Protocol

from contx.models import Observation


class Collector(Protocol):
    """Collect one bounded batch of observations."""

    def collect(self) -> tuple[Observation, ...]: ...
