"""Mandatory local model boundary."""

from contx.model_provider.base import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    LocalModelExecution,
    LocalModelRequest,
    LocalModelRuntimeStatus,
    ModelAttempt,
    ModelAttemptInvocation,
    ModelInterpretation,
    ModelProvider,
    ModelTransformation,
    ModelTransformationStatus,
    SensitiveCategory,
)
from contx.model_provider.endpoint import LoopbackHttpEndpoint
from contx.model_provider.ollama import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MODEL,
    LoopbackJsonTransport,
    OllamaModelProvider,
)

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_MODEL",
    "OUTPUT_SCHEMA_VERSION",
    "PROMPT_VERSION",
    "LocalModelExecution",
    "LocalModelRequest",
    "LocalModelRuntimeStatus",
    "LoopbackHttpEndpoint",
    "LoopbackJsonTransport",
    "ModelInterpretation",
    "ModelAttempt",
    "ModelAttemptInvocation",
    "ModelProvider",
    "ModelTransformation",
    "ModelTransformationStatus",
    "OllamaModelProvider",
    "SensitiveCategory",
]
