"""Tests for the analyzer — one LLM pass over a benchmark producing classified observation notes."""

from __future__ import annotations

from typing import Any

from eval_harness import analyzer, llm


def fake_response() -> llm.LLMResponse:
    return llm.LLMResponse(
        text="", cost_usd=0.05, input_tokens=50, output_tokens=30,
        cache_read_tokens=4, cache_creation_tokens=6,
        duration_ms=80, session_id="s", raw={},
    )


def call_returning(payload: dict[str, Any]) -> analyzer.CallJson:
    def _call(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        validate = kwargs.get("validate")
        if validate is not None:
            assert validate(payload)

        return payload, fake_response()

    return _call


BENCHMARK = {"target": "code-review", "summary": {"num_evals": 1}, "evals": []}


def test_analyze_returns_classified_notes() -> None:
    # Arrange
    payload = {
        "notes": [
            {"severity": "ok", "text": "skill adds value on injection detection"},
            {"severity": "warning", "text": "eval 3 is flaky"},
            {"severity": "issue", "text": "check A always passes — not discriminating"},
        ]
    }

    # Act
    notes = analyzer.analyze(
        BENCHMARK, model="claude-opus-4-8", effort="max", call_json=call_returning(payload)
    )

    # Assert
    assert [n.severity for n in notes] == ["ok", "warning", "issue"]
    assert notes[1].text == "eval 3 is flaky"


def test_analyze_prompt_embeds_the_benchmark() -> None:
    # Act
    prompt = analyzer._build_prompt(BENCHMARK)

    # Assert
    assert "code-review" in prompt


def test_analyze_returns_empty_list_when_no_notes() -> None:
    # Act
    notes = analyzer.analyze(
        BENCHMARK, model="claude-opus-4-8", effort="max", call_json=call_returning({"notes": []})
    )

    # Assert
    assert notes == []


def test_analyze_forwards_model_and_effort_to_request() -> None:
    # Arrange — the analyzer is another llm-seam call, so it must pass model + effort too
    captured: dict[str, object] = {}

    def capture(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        captured["model"] = request.model
        captured["effort"] = request.effort

        return {"notes": []}, fake_response()

    # Act
    analyzer.analyze(BENCHMARK, model="claude-opus-4-8", effort="high", call_json=capture)

    # Assert
    assert captured["model"] == "claude-opus-4-8"
    assert captured["effort"] == "high"


def test_notes_validator_rejects_malformed_payloads() -> None:
    # Act, Assert
    assert analyzer._valid_notes_payload(42) is False
    assert analyzer._valid_notes_payload({"notes": "nope"}) is False
    assert analyzer._valid_notes_payload({"notes": [{"text": "missing severity"}]}) is False
    assert analyzer._valid_notes_payload({"notes": [{"severity": "banana", "text": "x"}]}) is False
    assert analyzer._valid_notes_payload({"notes": [{"severity": "ok", "text": "fine"}]}) is True
