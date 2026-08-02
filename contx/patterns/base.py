"""Pattern detection contract."""

from datetime import datetime, timedelta
from typing import Protocol

from contx.models import ActivityTimeline, Pattern


class PatternEngine(Protocol):
    """Detect bounded multi-event inferences from one selected timeline."""

    @property
    def processing_version(self) -> str: ...

    @property
    def min_project_events(self) -> int: ...

    @property
    def resumption_gap(self) -> timedelta: ...

    @property
    def change_ratio(self) -> float: ...

    def detect(
        self,
        timeline: ActivityTimeline,
        *,
        comparison_boundary: datetime,
    ) -> tuple[Pattern, ...]: ...
