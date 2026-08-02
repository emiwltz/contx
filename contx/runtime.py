"""Shared factories for configured local runtime boundaries."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from contx.application.memory_projection import ActiveMemoryProjectionService
from contx.memory_store import (
    AgentProposalEvaluator,
    MemoryCorrectionComposer,
    MemoryStore,
    OllamaAgentProposalEvaluator,
    OllamaMemoryCompressor,
    OllamaMemoryCorrectionComposer,
    OptMemAdapter,
    resolve_optmem_executable,
)
from contx.model_provider import OllamaModelProvider
from contx.raw_store import FilesystemRawStore
from contx.settings import AppSettings, ModelSettings, RuntimePaths


def build_raw_store(paths: RuntimePaths, settings: AppSettings) -> FilesystemRawStore:
    return FilesystemRawStore(
        paths.raw,
        disk_budget_bytes=settings.collection.raw_disk_budget_mb * 1024 * 1024,
    )


def build_memory_store(
    memory_directory: Path,
    *,
    wake_budget_bytes: int = 20_000,
) -> MemoryStore:
    return OptMemAdapter(
        executable=resolve_optmem_executable(),
        memory_directory=memory_directory,
        wake_budget_bytes=wake_budget_bytes,
    )


def build_memory_compressor(settings: ModelSettings) -> OllamaMemoryCompressor:
    return OllamaMemoryCompressor(
        model=settings.model_name,
        endpoint=settings.endpoint,
        timeout_seconds=settings.timeout_seconds,
        keep_alive=settings.keep_alive,
        context_tokens=settings.context_tokens,
    )


def build_active_memory_projection_service(
    *,
    engine: Engine,
    paths: RuntimePaths,
    settings: AppSettings,
) -> ActiveMemoryProjectionService:
    return ActiveMemoryProjectionService(
        engine=engine,
        projection_root=paths.memory_projection,
        memory_store_factory=lambda memory_directory: build_memory_store(
            memory_directory,
            wake_budget_bytes=settings.memory.wake_budget_bytes,
        ),
        compressor=build_memory_compressor(settings.model),
    )


def build_local_model_provider(
    settings: ModelSettings,
    *,
    timeout_seconds: float | None = None,
) -> OllamaModelProvider:
    return OllamaModelProvider(
        model=settings.model_name,
        endpoint=settings.endpoint,
        timeout_seconds=(
            settings.timeout_seconds if timeout_seconds is None else timeout_seconds
        ),
        keep_alive=settings.keep_alive,
        context_tokens=settings.context_tokens,
        max_output_tokens=settings.max_output_tokens,
        max_image_bytes=settings.max_image_mb * 1024 * 1024,
        max_response_bytes=settings.max_response_kb * 1024,
    )


def build_memory_correction_composer(
    settings: ModelSettings,
) -> MemoryCorrectionComposer:
    return OllamaMemoryCorrectionComposer(
        model=settings.model_name,
        endpoint=settings.endpoint,
        timeout_seconds=settings.timeout_seconds,
        keep_alive=settings.keep_alive,
        context_tokens=settings.context_tokens,
    )


def build_agent_proposal_evaluator(
    settings: ModelSettings,
) -> AgentProposalEvaluator:
    return OllamaAgentProposalEvaluator(
        model=settings.model_name,
        endpoint=settings.endpoint,
        timeout_seconds=settings.timeout_seconds,
        keep_alive=settings.keep_alive,
        context_tokens=settings.context_tokens,
    )
