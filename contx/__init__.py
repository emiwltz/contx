"""CONTX package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("contx")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
