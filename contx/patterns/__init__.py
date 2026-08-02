"""Multi-event pattern detection."""

from contx.patterns.base import PatternEngine
from contx.patterns.temporal import (
    DEFAULT_CHANGE_RATIO,
    DEFAULT_MIN_PROJECT_EVENTS,
    DEFAULT_PATTERN_VALIDITY,
    DEFAULT_RESUMPTION_GAP,
    PATTERN_PROCESSING_VERSION,
    TemporalPatternEngine,
)

__all__ = [
    "DEFAULT_CHANGE_RATIO",
    "DEFAULT_MIN_PROJECT_EVENTS",
    "DEFAULT_PATTERN_VALIDITY",
    "DEFAULT_RESUMPTION_GAP",
    "PATTERN_PROCESSING_VERSION",
    "PatternEngine",
    "TemporalPatternEngine",
]
