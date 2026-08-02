"""User-facing CONTX error types."""


class ContxError(Exception):
    """Base class for expected, actionable CONTX failures."""


class ConfigurationError(ContxError):
    """Raised when runtime paths or configuration are unsafe or invalid."""


class DatabaseError(ContxError):
    """Raised when persisted CONTX state is unavailable or incompatible."""


class PipelineError(ContxError):
    """Raised when one observable pipeline run cannot complete."""


class CollectorUnavailableError(ContxError):
    """Raised when a requested local collection capability is unavailable."""


class DaemonAlreadyRunningError(ContxError):
    """Raised when a second continuous collector cannot acquire its lease."""


class MemoryStoreError(ContxError):
    """Raised when final memory cannot be read or updated safely."""


class MemoryStoreUnavailableError(MemoryStoreError):
    """Raised when the configured final-memory backend is unavailable."""


class RawStoreError(ContxError):
    """Raised when temporary raw data cannot be stored or removed safely."""


class RawStoreFullError(RawStoreError):
    """Raised before a raw artifact would exceed the configured disk budget."""
