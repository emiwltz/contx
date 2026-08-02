"""User-facing CONTX error types."""


class ContxError(Exception):
    """Base class for expected, actionable CONTX failures."""


class ConfigurationError(ContxError):
    """Raised when runtime paths or configuration are unsafe or invalid."""


class DatabaseError(ContxError):
    """Raised when persisted CONTX state is unavailable or incompatible."""
