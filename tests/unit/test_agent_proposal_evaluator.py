"""Dedicated local-model agent-proposal validation tests."""

import json
from collections.abc import Mapping

import pytest

from contx.errors import LocalModelNotInstalledError, LocalModelResponseError
from contx.memory_store import AgentProposalEvaluation, OllamaAgentProposalEvaluator
from contx.model_provider import DEFAULT_MODEL
from contx.model_provider.ollama import JsonObject

DIGEST = f"sha256:{'c' * 64}"


def test_proposal_evaluation_uses_strict_structured_loopback_contract() -> None:
    transport = RecordingTransport()
    evaluator = OllamaAgentProposalEvaluator(transport=transport)

    result = evaluator.evaluate(
        proposal="Atlas deploys locally.",
        reference=json.dumps({"type": "event", "summary": "Atlas deploys locally."}),
        active_memories=("Atlas uses Python.",),
        minimum_confidence=0.75,
    )

    assert result.decision == "accepted"
    assert result.reason_code == "supported_novel"
    assert result.confidence == 0.92
    assert evaluator.model_digest == DIGEST
    assert [call[:2] for call in transport.calls] == [
        ("GET", "/api/tags"),
        ("POST", "/api/chat"),
        ("POST", "/api/chat"),
    ]
    payload = transport.calls[1][2]
    assert payload is not None
    assert payload["think"] is False
    assert payload["options"] == {
        "temperature": 0,
        "seed": 0,
        "num_ctx": 8192,
        "num_predict": 128,
    }
    assert payload["format"] == {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["accepted", "rejected", "deferred"],
            },
            "reason_code": {
                "type": "string",
                "enum": [
                    "supported_novel",
                    "unsupported_by_reference",
                    "duplicate_active_memory",
                    "conflicts_with_active_memory",
                    "ambiguous_reference",
                ],
            },
            "fully_supported": {"type": "boolean"},
            "semantic_duplicate": {"type": "boolean"},
            "conflicts_with_active_memory": {"type": "boolean"},
            "reference_ambiguous": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
        "required": [
            "decision",
            "reason_code",
            "fully_supported",
            "semantic_duplicate",
            "conflicts_with_active_memory",
            "reference_ambiguous",
            "confidence",
        ],
        "additionalProperties": False,
    }
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "Atlas deploys locally." in str(messages[-1])
    assert "Atlas uses Python." in str(messages[-1])
    equivalence_payload = transport.calls[2][2]
    assert equivalence_payload is not None
    assert equivalence_payload["format"] == {
        "type": "object",
        "properties": {
            "equivalent": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
        "required": ["equivalent", "confidence"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            {
                "decision": "accepted",
                "reason_code": "supported_novel",
                "fully_supported": True,
                "semantic_duplicate": False,
                "conflicts_with_active_memory": False,
                "reference_ambiguous": False,
                "confidence": 0.5,
            },
            "confidence threshold",
        ),
        (
            {
                "decision": "accepted",
                "reason_code": "duplicate_active_memory",
                "fully_supported": True,
                "semantic_duplicate": True,
                "conflicts_with_active_memory": False,
                "reference_ambiguous": False,
                "confidence": 0.9,
            },
            "inconsistent",
        ),
        (
            {
                "decision": "rejected",
                "reason_code": "ambiguous_reference",
                "fully_supported": False,
                "semantic_duplicate": False,
                "conflicts_with_active_memory": False,
                "reference_ambiguous": True,
                "confidence": 0.9,
            },
            "inconsistent",
        ),
    ],
)
def test_proposal_evaluation_rejects_inconsistent_decisions(
    content: dict[str, object],
    message: str,
) -> None:
    evaluator = OllamaAgentProposalEvaluator(
        transport=RecordingTransport(content=json.dumps(content))
    )

    with pytest.raises(LocalModelResponseError, match=message):
        evaluator.evaluate(
            proposal="private proposal",
            reference=json.dumps({"summary": "private evidence"}),
            active_memories=(),
            minimum_confidence=0.75,
        )


def test_exact_duplicate_policy_overrides_an_accepted_semantic_response() -> None:
    evaluator = OllamaAgentProposalEvaluator(transport=RecordingTransport())

    result = evaluator.evaluate(
        proposal="  Atlas USES local-only deployment. ",
        reference=json.dumps({"summary": "Atlas uses local-only deployment."}),
        active_memories=("Atlas uses local-only deployment.",),
        minimum_confidence=0.75,
    )

    assert result == AgentProposalEvaluation(
        decision="rejected",
        reason_code="duplicate_active_memory",
        confidence=1.0,
    )


def test_dedicated_semantic_equivalence_rejects_a_paraphrase() -> None:
    evaluator = OllamaAgentProposalEvaluator(
        transport=RecordingTransport(
            equivalence_content=json.dumps(
                {"equivalent": True, "confidence": 0.94}
            )
        )
    )

    result = evaluator.evaluate(
        proposal="Atlas deployment runs only on the local machine.",
        reference=json.dumps({"summary": "Atlas uses local-only deployment."}),
        active_memories=("Atlas uses local-only deployment.",),
        minimum_confidence=0.75,
    )

    assert result == AgentProposalEvaluation(
        decision="rejected",
        reason_code="duplicate_active_memory",
        confidence=0.94,
    )


def test_unsupported_reference_takes_precedence_over_model_reason_label() -> None:
    evaluator = OllamaAgentProposalEvaluator(
        transport=RecordingTransport(
            content=json.dumps(
                {
                    "decision": "rejected",
                    "reason_code": "conflicts_with_active_memory",
                    "fully_supported": False,
                    "semantic_duplicate": False,
                    "conflicts_with_active_memory": True,
                    "reference_ambiguous": False,
                    "confidence": 0.91,
                }
            )
        )
    )

    result = evaluator.evaluate(
        proposal="Atlas deploys to the public cloud.",
        reference=json.dumps({"summary": "Atlas deploys locally only."}),
        active_memories=(),
        minimum_confidence=0.75,
    )

    assert result == AgentProposalEvaluation(
        decision="rejected",
        reason_code="unsupported_by_reference",
        confidence=0.91,
    )


def test_missing_model_and_invalid_output_fail_without_echo() -> None:
    missing = OllamaAgentProposalEvaluator(
        transport=RecordingTransport(model_installed=False)
    )
    with pytest.raises(LocalModelNotInstalledError):
        missing.evaluate(
            proposal="proposal",
            reference=json.dumps({"summary": "evidence"}),
            active_memories=(),
            minimum_confidence=0.75,
        )

    private_output = "private-invalid-proposal-output"
    invalid = OllamaAgentProposalEvaluator(
        transport=RecordingTransport(content=private_output)
    )
    with pytest.raises(LocalModelResponseError) as caught:
        invalid.evaluate(
            proposal="proposal",
            reference=json.dumps({"summary": "evidence"}),
            active_memories=(),
            minimum_confidence=0.75,
        )
    assert private_output not in str(caught.value)


class RecordingTransport:
    endpoint_url = "http://127.0.0.1:11434"

    def __init__(
        self,
        *,
        model_installed: bool = True,
        content: str | None = None,
        equivalence_content: str | None = None,
    ) -> None:
        self.model_installed = model_installed
        self.content = content or json.dumps(
            {
                "decision": "accepted",
                "reason_code": "supported_novel",
                "fully_supported": True,
                "semantic_duplicate": False,
                "conflicts_with_active_memory": False,
                "reference_ambiguous": False,
                "confidence": 0.92,
            }
        )
        self.equivalence_content = equivalence_content or json.dumps(
            {"equivalent": False, "confidence": 0.93}
        )
        self._chat_count = 0
        self.calls: list[tuple[str, str, Mapping[str, object] | None]] = []

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> JsonObject:
        self.calls.append((method, path, payload))
        if path == "/api/tags":
            return {
                "models": (
                    [
                        {
                            "name": DEFAULT_MODEL,
                            "model": DEFAULT_MODEL,
                            "digest": DIGEST,
                        }
                    ]
                    if self.model_installed
                    else []
                )
            }
        if path == "/api/chat":
            self._chat_count += 1
            return {
                "model": DEFAULT_MODEL,
                "message": {
                    "role": "assistant",
                    "content": (
                        self.content
                        if self._chat_count == 1
                        else self.equivalence_content
                    ),
                },
                "done": True,
            }
        raise AssertionError(f"unexpected path: {path}")
