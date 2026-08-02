"""Final-memory contracts and implementations."""

from contx.memory_store.base import (
    AgentProposalEvaluation,
    AgentProposalEvaluator,
    MemoryAppendResult,
    MemoryCompressionRequest,
    MemoryCompressor,
    MemoryCorrectionComposer,
    MemoryMaintenance,
    MemoryStore,
    MemoryWake,
)
from contx.memory_store.compression import (
    MEMORY_COMPRESSION_PROMPT_VERSION,
    OllamaMemoryCompressor,
)
from contx.memory_store.corrections import (
    MEMORY_CORRECTION_OUTPUT_SCHEMA_VERSION,
    MEMORY_CORRECTION_PROMPT_VERSION,
    OllamaMemoryCorrectionComposer,
)
from contx.memory_store.optmem import OptMemAdapter, resolve_optmem_executable
from contx.memory_store.proposals import (
    AGENT_PROPOSAL_OUTPUT_SCHEMA_VERSION,
    AGENT_PROPOSAL_PROMPT_VERSION,
    OllamaAgentProposalEvaluator,
)
from contx.memory_store.recording import RecordingMemoryStore

__all__ = [
    "AgentProposalEvaluation",
    "AgentProposalEvaluator",
    "AGENT_PROPOSAL_OUTPUT_SCHEMA_VERSION",
    "AGENT_PROPOSAL_PROMPT_VERSION",
    "MemoryAppendResult",
    "MemoryCompressionRequest",
    "MemoryCompressor",
    "MemoryCorrectionComposer",
    "MemoryMaintenance",
    "MemoryStore",
    "MemoryWake",
    "MEMORY_COMPRESSION_PROMPT_VERSION",
    "MEMORY_CORRECTION_PROMPT_VERSION",
    "MEMORY_CORRECTION_OUTPUT_SCHEMA_VERSION",
    "OllamaMemoryCorrectionComposer",
    "OllamaAgentProposalEvaluator",
    "OllamaMemoryCompressor",
    "OptMemAdapter",
    "RecordingMemoryStore",
    "resolve_optmem_executable",
]
