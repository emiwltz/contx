"""Run a disposable end-to-end agent-proposal adoption validation."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from contx.application import AgentProposalAdoptionService, AgentProposalService
from contx.db import create_database_engine, session_scope, upgrade_database
from contx.db.repositories import PipelineRepository
from contx.memory_store import OllamaAgentProposalEvaluator, RecordingMemoryStore
from contx.model_provider import DEFAULT_ENDPOINT, DEFAULT_MODEL
from contx.models import (
    AgentProposalStatus,
    AgentRole,
    EpistemicStatus,
    Event,
    EventType,
    Observation,
    ProposalReferenceType,
    Sensitivity,
    SourceType,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
OBSERVATION_ID = UUID("e0000000-0000-0000-0000-000000000001")
EVENT_ID = UUID("e0000000-0000-0000-0000-000000000002")
PROPOSAL_IDS = (
    UUID("e0000000-0000-0000-0000-000000000003"),
    UUID("e0000000-0000-0000-0000-000000000004"),
    UUID("e0000000-0000-0000-0000-000000000005"),
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SequenceIdentifiers:
    def __init__(self, values: Iterable[UUID]) -> None:
        self._values = iter(values)

    def new(self) -> UUID:
        return next(self._values)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate proposal adoption with synthetic evidence only."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="contx-proposal-validation-") as root:
        database_path = Path(root) / "contx.db"
        upgrade_database(database_path)
        engine = create_database_engine(database_path)
        memory = RecordingMemoryStore()
        evaluator = OllamaAgentProposalEvaluator(
            model=arguments.model,
            endpoint=arguments.endpoint,
        )
        try:
            _seed_reference(engine)
            proposal_service = AgentProposalService(
                engine=engine,
                clock=FixedClock(),
                identifiers=SequenceIdentifiers(PROPOSAL_IDS),
            )
            adoption_service = AgentProposalAdoptionService(
                engine=engine,
                memory_store=memory,
                evaluator=evaluator,
                clock=FixedClock(),
            )
            novel = adoption_service.adopt(
                proposal_id=_submit(
                    proposal_service,
                    "Atlas uses local-only deployment.",
                )
            )
            duplicate = adoption_service.adopt(
                proposal_id=_submit(
                    proposal_service,
                    "Atlas deployment runs only on the local machine.",
                )
            )
            unsupported = adoption_service.adopt(
                proposal_id=_submit(
                    proposal_service,
                    "Atlas deploys to the public cloud.",
                )
            )
            if (
                novel.proposal.status is not AgentProposalStatus.ADOPTED
                or duplicate.proposal.status is not AgentProposalStatus.REJECTED
                or unsupported.proposal.status is not AgentProposalStatus.REJECTED
                or len(memory.entries) != 1
            ):
                raise RuntimeError("Synthetic proposal adoption matrix failed")
            print(
                json.dumps(
                    {
                        "model": evaluator.model,
                        "model_digest": evaluator.model_digest,
                        "novel": {
                            "status": novel.proposal.status.value,
                            "decision": novel.build.decision.value,
                            "reason": novel.build.reason_code.value,
                            "confidence": novel.build.confidence,
                        },
                        "semantic_duplicate": {
                            "status": duplicate.proposal.status.value,
                            "decision": duplicate.build.decision.value,
                            "reason": duplicate.build.reason_code.value,
                            "confidence": duplicate.build.confidence,
                        },
                        "unsupported": {
                            "status": unsupported.proposal.status.value,
                            "decision": unsupported.build.decision.value,
                            "reason": unsupported.build.reason_code.value,
                            "confidence": unsupported.build.confidence,
                        },
                        "final_memory_entries": len(memory.entries),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        finally:
            engine.dispose()
    return 0


def _submit(service: AgentProposalService, text: str) -> UUID:
    return service.submit(
        agent_id="synthetic-primary-agent",
        agent_role=AgentRole.PRIMARY,
        text=text,
        reference_type=ProposalReferenceType.EVENT,
        reference_id=EVENT_ID,
    ).id


def _seed_reference(engine: Engine) -> None:
    observation = Observation(
        id=OBSERVATION_ID,
        idempotency_key="6" * 64,
        source_type=SourceType.SYNTHETIC,
        captured_at=NOW,
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=48),
        created_at=NOW,
    )
    event = Event(
        id=EVENT_ID,
        idempotency_key="7" * 64,
        lineage_key="8" * 64,
        type=EventType.PROJECT_WORK,
        summary="Atlas uses local-only deployment.",
        facts={"deployment": "local-only"},
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=10),
        valid_from=NOW,
        valid_until=NOW + timedelta(days=1),
        epistemic_status=EpistemicStatus.OBSERVED,
        confidence=0.98,
        sensitivity=Sensitivity.PERSONAL,
        projects=("Atlas",),
        source_observation_ids=(observation.id,),
        processing_version="synthetic-proposal-validation-v1",
        created_at=NOW,
        updated_at=NOW,
    )
    with session_scope(engine) as database_session:
        repository = PipelineRepository(database_session)
        repository.save_observation(observation)
        repository.save_event(event)


if __name__ == "__main__":
    raise SystemExit(main())
