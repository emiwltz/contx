"""Event construction contract."""

from typing import Protocol

from contx.models import Event, Observation


class EventBuilder(Protocol):
    """Build bounded interpretations from persisted observations."""

    def build(self, observations: tuple[Observation, ...]) -> tuple[Event, ...]: ...
