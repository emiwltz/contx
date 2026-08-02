"""Mandatory local model boundary."""

from contx.model_provider.base import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    LocalModelExecution,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    ModelInterpretation,
    ModelProvider,
    SensitiveCategory,
)
from contx.model_provider.endpoint import LoopbackHttpEndpoint
from contx.model_provider.ollama import (
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    LoopbackJsonTransport,
    OllamaModelProvider,
)

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MODEL",
    "OUTPUT_SCHEMA_VERSION",
    "PROMPT_VERSION",
    "LocalModelExecution",
    "LocalModelRequest",
    "LocalModelRuntimeStatus",
    "LoopbackHttpEndpoint",
    "LoopbackJsonTransport",
    "ModelInterpretation",
    "ModelProvider",
    "OllamaModelProvider",
    "SensitiveCategory",
]
