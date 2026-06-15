"""Tests for the ``llm`` seam over headless ``claude -p``.

The subprocess boundary is the only thing faked — via an injected ``runner`` that returns a
real ``subprocess.CompletedProcess``. Everything else exercises real parsing/validation/retry.
"""

from __future__ import annotations

import json
import subprocess
import uuid

import pytest

from eval_harness import llm

# A complete success envelope, mirroring the real `claude -p --output-format json` output.
SUCCESS_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 2029,
    "num_turns": 1,
    "result": "the answer",
    "stop_reason": "end_turn",
    "session_id": "241a84ec-fe70-43c1-82bb-d7ca49f044c6",
    "total_cost_usd": 0.0130617,
    "usage": {
        "input_tokens": 10, "output_tokens": 38,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 7,
    },
}


def runner_returning(stdout: str, *, returncode: int = 0, stderr: str = "") -> llm.Runner:
    """Build a fake runner that yields a fixed CompletedProcess regardless of argv."""

    def _run(
        argv: list[str], cwd: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    return _run


def envelope_with(result_text: str) -> str:
    envelope = dict(SUCCESS_ENVELOPE)
    envelope["result"] = result_text

    return json.dumps(envelope)


# A minimal valid request reused by tests that don't care about the prompt's content.
REQ = llm.LLMRequest("p", model="claude-opus-4-8", effort="max")


# --- LLMRequest ---------------------------------------------------------------


def test_request_rejects_empty_prompt() -> None:
    # Act, Assert
    with pytest.raises(ValueError):
        llm.LLMRequest("   ", model="claude-opus-4-8", effort="max")


# --- _build_command --------------------------------------------------------------


def test_build_command_carries_prompt_and_json_output() -> None:
    # Act
    argv = llm._build_command(llm.LLMRequest("do a thing", model="claude-opus-4-8", effort="max"))

    # Assert
    assert "do a thing" in argv
    assert "--output-format" in argv and "json" in argv


def test_build_command_always_includes_model_and_effort() -> None:
    # Act
    argv = llm._build_command(llm.LLMRequest("p", model="claude-opus-4-8", effort="high"))

    # Assert — every claude -p call carries an explicit model and effort
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    assert argv[argv.index("--effort") + 1] == "high"


def test_build_command_includes_system_prompt_when_given() -> None:
    # Act
    argv = llm._build_command(
        llm.LLMRequest("p", model="claude-opus-4-8", effort="max", system_prompt="be terse")
    )

    # Assert
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "be terse"


def test_build_command_omits_system_prompt_when_unset() -> None:
    # Act
    argv = llm._build_command(llm.LLMRequest("p", model="claude-opus-4-8", effort="max"))

    # Assert — system prompt is the only optional flag; model/effort are always present
    assert "--append-system-prompt" not in argv


def test_build_command_appends_extra_args() -> None:
    # Arrange — the one-off flags the runner rides along through the one seam (e.g. --session-id)
    request = llm.LLMRequest(
        "p", model="claude-opus-4-8", effort="max",
        extra_args=(("--session-id", "abc"), ("--add-dir", "/extra")),
    )

    # Act
    argv = llm._build_command(request)

    # Assert — each (flag, value) pair is appended verbatim
    assert argv[argv.index("--session-id") + 1] == "abc"
    assert argv[argv.index("--add-dir") + 1] == "/extra"


def test_build_command_isolates_context_from_global_config() -> None:
    # Act
    argv = llm._build_command(llm.LLMRequest("p", model="claude-opus-4-8", effort="max"))

    # Assert — no global ~/.claude contamination (settings, hooks, global CLAUDE.md)
    assert argv[argv.index("--setting-sources") + 1] == "project,local"


def test_build_command_denies_all_tools_by_default() -> None:
    # Act — no tools declared
    argv = llm._build_command(llm.LLMRequest("p", model="claude-opus-4-8", effort="max"))

    # Assert — the secure default is an empty available toolset (`--tools ""`), no allowlist
    assert argv[argv.index("--tools") + 1] == ""
    assert "--allowedTools" not in argv


def test_build_command_bounds_and_allows_the_named_tools() -> None:
    # Arrange — one tools field drives both availability and auto-approval
    request = llm.LLMRequest("p", model="claude-opus-4-8", effort="max", tools=("Read", "Bash"))

    # Act
    argv = llm._build_command(request)

    # Assert — only these tools exist (--tools) and run unattended (--allowedTools)
    assert argv[argv.index("--tools") + 1] == "Read,Bash"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Bash"


# --- call ---------------------------------------------------------------------


def test_call_parses_text_and_metrics_from_success_envelope() -> None:
    # Arrange
    runner = runner_returning(json.dumps(SUCCESS_ENVELOPE))

    # Act
    response = llm.call(REQ,runner=runner)

    # Assert
    assert response.text == "the answer"
    assert response.cost_usd == pytest.approx(0.0130617)
    assert response.input_tokens == 10
    assert response.output_tokens == 38
    assert response.cache_read_tokens == 5
    assert response.cache_creation_tokens == 7
    assert response.duration_ms == 2029
    assert response.session_id == "241a84ec-fe70-43c1-82bb-d7ca49f044c6"


def test_call_raises_transport_error_on_nonzero_exit() -> None:
    # Arrange
    runner = runner_returning("boom", returncode=1, stderr="bad")

    # Act, Assert
    with pytest.raises(llm.LLMTransportError):
        llm.call(REQ,runner=runner)


def test_call_raises_transport_error_when_envelope_is_error() -> None:
    # Arrange
    envelope = dict(SUCCESS_ENVELOPE, is_error=True, subtype="error_during_execution")
    runner = runner_returning(json.dumps(envelope))

    # Act, Assert
    with pytest.raises(llm.LLMTransportError):
        llm.call(REQ,runner=runner)


def test_call_raises_parse_error_when_stdout_not_json() -> None:
    # Arrange
    runner = runner_returning("not json at all")

    # Act, Assert
    with pytest.raises(llm.LLMParseError):
        llm.call(REQ,runner=runner)


def test_call_raises_parse_error_when_result_key_missing() -> None:
    # Arrange
    envelope = {k: v for k, v in SUCCESS_ENVELOPE.items() if k != "result"}
    runner = runner_returning(json.dumps(envelope))

    # Act, Assert
    with pytest.raises(llm.LLMParseError):
        llm.call(REQ,runner=runner)


def test_call_raises_parse_error_when_json_is_not_an_object() -> None:
    # Arrange
    runner = runner_returning("42")

    # Act, Assert
    with pytest.raises(llm.LLMParseError):
        llm.call(REQ,runner=runner)


def test_call_tolerates_missing_optional_metrics() -> None:
    # Arrange
    runner = runner_returning(json.dumps({"result": "hi", "is_error": False}))

    # Act
    response = llm.call(REQ,runner=runner)

    # Assert
    assert response.text == "hi"
    assert response.cost_usd is None
    assert response.input_tokens is None


# --- call_json ----------------------------------------------------------------


def runner_sequence(result_texts: list[str]) -> tuple[llm.Runner, list[list[str]]]:
    """A runner that returns each result text in turn; records the argv of every call."""
    calls: list[list[str]] = []
    remaining = list(result_texts)

    def _run(
        argv: list[str], cwd: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        stdout = envelope_with(remaining.pop(0))

        return subprocess.CompletedProcess(argv, 0, stdout, "")

    return _run, calls


def test_call_json_parses_plain_json_object() -> None:
    # Arrange
    runner = runner_returning(envelope_with('{"passed": true, "evidence": "ok"}'))

    # Act
    parsed, response = llm.call_json(REQ,runner=runner)

    # Assert
    assert parsed == {"passed": True, "evidence": "ok"}
    assert response.text == '{"passed": true, "evidence": "ok"}'


def test_call_json_strips_fenced_code_block() -> None:
    # Arrange
    fenced = '```json\n{"passed": false}\n```'
    runner = runner_returning(envelope_with(fenced))

    # Act
    parsed, _ = llm.call_json(REQ,runner=runner)

    # Assert
    assert parsed == {"passed": False}


def test_call_json_retries_then_succeeds_on_malformed_json() -> None:
    # Arrange
    runner, calls = runner_sequence(["not json", '{"passed": true}'])

    # Act
    parsed, _ = llm.call_json(REQ,retries=1, runner=runner)

    # Assert
    assert parsed == {"passed": True}
    assert len(calls) == 2


def test_call_json_parse_error_includes_raw_model_output() -> None:
    # Arrange — so a later reader can see *what* the model actually said
    runner = runner_returning(envelope_with("totally not json <<<"))

    # Act, Assert
    with pytest.raises(llm.LLMParseError, match="totally not json"):
        llm.call_json(REQ, retries=0, runner=runner)


def test_call_json_raises_parse_error_after_exhausting_retries() -> None:
    # Arrange
    runner, calls = runner_sequence(["nope", "still nope"])

    # Act, Assert
    with pytest.raises(llm.LLMParseError):
        llm.call_json(REQ,retries=1, runner=runner)

    assert len(calls) == 2


def test_call_json_retries_when_validator_rejects_then_accepts() -> None:
    # Arrange
    runner, calls = runner_sequence(['{"passed": "maybe"}', '{"passed": true}'])

    def validate(value: object) -> bool:
        return isinstance(value, dict) and isinstance(value.get("passed"), bool)

    # Act
    parsed, _ = llm.call_json(REQ,validate=validate, retries=1, runner=runner)

    # Assert
    assert parsed == {"passed": True}
    assert len(calls) == 2


def test_call_json_raises_validation_error_when_all_attempts_rejected() -> None:
    # Arrange
    runner, _ = runner_sequence(['{"passed": "no"}', '{"passed": "still no"}'])

    # Act, Assert
    with pytest.raises(llm.LLMValidationError):
        llm.call_json(REQ,validate=lambda v: False, retries=1, runner=runner)


def test_call_json_makes_single_attempt_when_retries_zero() -> None:
    # Arrange
    runner, calls = runner_sequence(["not json"])

    # Act, Assert
    with pytest.raises(llm.LLMParseError):
        llm.call_json(REQ,retries=0, runner=runner)

    assert len(calls) == 1


def test_call_json_rejects_negative_retries() -> None:
    # Arrange
    runner = runner_returning(envelope_with('{"passed": true}'))

    # Act, Assert
    with pytest.raises(ValueError):
        llm.call_json(REQ,retries=-1, runner=runner)


def test_call_json_strips_single_line_fence() -> None:
    # Arrange
    runner = runner_returning(envelope_with('```json {"passed": true}```'))

    # Act
    parsed, _ = llm.call_json(REQ,runner=runner)

    # Assert
    assert parsed == {"passed": True}


def test_call_json_tolerates_unclosed_fence() -> None:
    # Arrange
    runner = runner_returning(envelope_with('```json\n{"ok": true}'))

    # Act
    parsed, _ = llm.call_json(REQ,runner=runner)

    # Assert
    assert parsed == {"ok": True}


def test_call_json_raises_parse_error_for_fenced_non_json() -> None:
    # Arrange
    runner = runner_returning(envelope_with("```\nno json here\n```"))

    # Act, Assert
    with pytest.raises(llm.LLMParseError):
        llm.call_json(REQ,retries=0, runner=runner)


def test_call_json_validation_error_names_the_rejected_value() -> None:
    # Arrange
    runner = runner_returning(envelope_with('{"passed": "maybe"}'))

    # Act, Assert
    with pytest.raises(llm.LLMValidationError, match="maybe"):
        llm.call_json(REQ,validate=lambda v: False, retries=0, runner=runner)


# --- session pinning (OBS1) ----------------------------------------------------


def runner_capturing(
    captured: list[list[str]], stdout: str, *, returncode: int = 0
) -> llm.Runner:
    """Build a fake runner that records each argv before yielding a fixed CompletedProcess."""

    def _run(
        argv: list[str], cwd: object = None, env: object = None
    ) -> subprocess.CompletedProcess[str]:
        captured.append(argv)

        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    return _run


def _argv_session_id(argv: list[str]) -> str:
    return argv[argv.index("--session-id") + 1]


def test_call_pins_a_generated_session_id() -> None:
    # Arrange
    captured: list[list[str]] = []
    runner = runner_capturing(captured, json.dumps(SUCCESS_ENVELOPE))

    # Act
    llm.call(REQ, runner=runner)

    # Assert — a fresh UUID is pinned so the call's transcript is locatable afterwards
    uuid.UUID(_argv_session_id(captured[0]))


def test_call_respects_a_caller_pinned_session_id() -> None:
    # Arrange
    captured: list[list[str]] = []
    runner = runner_capturing(captured, json.dumps(SUCCESS_ENVELOPE))
    request = llm.LLMRequest(
        "p", model="claude-opus-4-8", effort="max", extra_args=(("--session-id", "abc"),)
    )

    # Act
    llm.call(request, runner=runner)

    # Assert — the caller's id is kept, not doubled with a generated one
    assert captured[0].count("--session-id") == 1
    assert _argv_session_id(captured[0]) == "abc"


def test_call_transport_error_names_the_session() -> None:
    # Arrange
    captured: list[list[str]] = []
    runner = runner_capturing(captured, "", returncode=1)

    # Act
    with pytest.raises(llm.LLMTransportError) as exc_info:
        llm.call(REQ, runner=runner)

    # Assert — the failed call is locatable by the id embedded in the error
    assert _argv_session_id(captured[0]) in str(exc_info.value)


def test_call_error_envelope_names_the_session() -> None:
    # Arrange
    captured: list[list[str]] = []
    envelope = dict(SUCCESS_ENVELOPE) | {"is_error": True, "subtype": "error_during_execution"}
    runner = runner_capturing(captured, json.dumps(envelope))

    # Act
    with pytest.raises(llm.LLMTransportError) as exc_info:
        llm.call(REQ, runner=runner)

    # Assert
    assert _argv_session_id(captured[0]) in str(exc_info.value)


def test_call_falls_back_to_pinned_session_when_envelope_lacks_one() -> None:
    # Arrange
    captured: list[list[str]] = []
    envelope = {k: v for k, v in SUCCESS_ENVELOPE.items() if k != "session_id"}
    runner = runner_capturing(captured, json.dumps(envelope))

    # Act
    response = llm.call(REQ, runner=runner)

    # Assert — the response always knows which session ran it
    assert response.session_id == _argv_session_id(captured[0])
