"""Tests for the judge — LLM grader over artifact content via the llm seam.

The llm seam (call_json) is injected with a fake; no real model calls. We exercise prompt
assembly, payload→GradingResult parsing, the empty-expectations short-circuit, and majority vote.
"""

from __future__ import annotations

from typing import Any

from eval_harness import judge, llm
from eval_harness.schemas import Eval, Expectation, GradingResult


def make_eval(expectations: tuple[str, ...], *, gate: str = "majority") -> Eval:
    return Eval(
        id=1, name="n", prompt="Review app.py.",
        expectations=tuple(Expectation(text=t, gate=gate) for t in expectations),
    )


def grade(ev: Eval, output_text: str, **kwargs: Any) -> GradingResult:
    """judge.grade with default model/effort — the seam is faked, so values are irrelevant here."""
    kwargs.setdefault("model", "claude-opus-4-8")
    kwargs.setdefault("effort", "max")

    return judge.grade(ev, output_text, **kwargs)


def fake_response() -> llm.LLMResponse:
    return llm.LLMResponse(
        text="", cost_usd=0.1, input_tokens=10, output_tokens=20,
        cache_read_tokens=5, cache_creation_tokens=7,
        duration_ms=100, session_id="s", raw={},
    )


def fixed_call_json(payload: dict[str, Any]) -> judge.CallJson:
    """A call_json seam that always returns the given payload (validator is still applied)."""

    def _call(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        validate = kwargs.get("validate")
        if validate is not None:
            assert validate(payload)

        return payload, fake_response()

    return _call


def sequence_call_json(payloads: list[dict[str, Any]]) -> judge.CallJson:
    """A call_json seam returning successive payloads — for multi-run (majority) tests."""
    remaining = list(payloads)

    def _call(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        return remaining.pop(0), fake_response()

    return _call


def flaky_call_json(actions: list[Any]) -> judge.CallJson:
    """A call_json seam where each successive action is a payload dict OR an exception to raise."""
    remaining = list(actions)

    def _call(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        action = remaining.pop(0)
        if isinstance(action, Exception):
            raise action

        return action, fake_response()

    return _call


def payload(*verdicts: bool) -> dict[str, Any]:
    return {
        "expectations": [
            {"text": f"exp{i}", "passed": v, "evidence": f"e{i}"} for i, v in enumerate(verdicts)
        ]
    }


# --- prompt assembly ----------------------------------------------------------


def test_grading_validator_requires_the_expected_expectation_count() -> None:
    # Arrange
    validate = judge._grading_validator(2)

    # Act, Assert — exactly 2 expectations accepted; wrong count or non-dict rejected
    assert validate({"expectations": [{"text": "a"}, {"text": "b"}]}) is True
    assert validate({"expectations": [{"text": "a"}]}) is False
    assert validate(42) is False


def test_build_grader_prompt_includes_task_expectations_and_output() -> None:
    # Arrange
    ev = make_eval(("flags injection", "no hallucinated criticals"))

    # Act
    prompt = judge._build_grader_prompt(ev, output_text="REVIEW BODY")

    # Assert
    assert "Review app.py." in prompt
    assert "flags injection" in prompt
    assert "no hallucinated criticals" in prompt
    assert "REVIEW BODY" in prompt


def test_build_grader_prompt_without_files_is_byte_identical() -> None:
    # Arrange — text skills (no produced files) must grade exactly as before
    ev = make_eval(("flags injection",))

    # Act
    prompt = judge._build_grader_prompt(ev, output_text="REVIEW BODY", produced_files=())

    # Assert — the OUTPUT section is just the final message; no file wrapping leaks in
    assert prompt.endswith("## OUTPUT\nREVIEW BODY\n")
    assert "### File" not in prompt


def test_build_grader_prompt_includes_produced_file_contents() -> None:
    # Arrange — a file-producing skill: the judge must see the file body, not just the summary
    ev = make_eval(("README accurately describes the project",))
    produced = (("README.md", "# Slugify\n\nTurns text into URL-safe slugs."),)

    # Act
    prompt = judge._build_grader_prompt(
        ev, output_text="I wrote a README.", produced_files=produced
    )

    # Assert — both the summary and the actual file content reach the grader, labelled by path
    assert "I wrote a README." in prompt
    assert "README.md" in prompt
    assert "Turns text into URL-safe slugs." in prompt


def test_build_grader_prompt_shows_placeholder_for_missing_file() -> None:
    # Arrange — a declared file the skill never produced (content is None)
    ev = make_eval(("README exists and describes the project",))

    # Act
    prompt = judge._build_grader_prompt(
        ev, output_text="done", produced_files=(("README.md", None),)
    )

    # Assert — the grader sees a placeholder (so content expectations fail) rather than crashing
    assert "README.md" in prompt
    assert judge.FILE_NOT_PRODUCED in prompt


def test_grade_threads_produced_files_into_prompt() -> None:
    # Arrange
    ev = make_eval(("a",))
    captured: dict[str, str] = {}

    def capture(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        captured["prompt"] = request.prompt

        return payload(True), fake_response()

    # Act
    judge.grade(
        ev, "summary", produced_files=(("README.md", "FILE CONTENT"),),
        model="claude-opus-4-8", effort="max", call_json=capture,
    )

    # Assert — grade forwards produced-file content all the way into the grader prompt
    assert "FILE CONTENT" in captured["prompt"]


# --- grading ------------------------------------------------------------------


def test_grade_forwards_model_and_effort_to_request() -> None:
    # Arrange
    ev = make_eval(("a",))
    captured: dict[str, object] = {}

    def capture(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        captured["effort"] = request.effort
        captured["model"] = request.model

        return payload(True), fake_response()

    # Act — grade pins nothing of its own; it forwards what the caller (the CLI) supplies
    judge.grade(ev, "out", model="claude-opus-4-8", effort="high", call_json=capture)

    # Assert
    assert captured["model"] == "claude-opus-4-8"
    assert captured["effort"] == "high"


def test_grade_parses_payload_into_result() -> None:
    # Arrange
    ev = make_eval(("a", "b"))

    # Act
    result = grade(ev, "out", call_json=fixed_call_json(payload(True, False)))

    # Assert
    assert result.total == 2
    assert result.passed == 1
    assert result.expectations[0].passed is True


def test_grade_short_circuits_when_no_expectations() -> None:
    # Arrange
    ev = make_eval(())
    called = False

    def must_not_call(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        nonlocal called
        called = True

        return {}, fake_response()

    # Act
    result = grade(ev, "out", call_json=must_not_call)

    # Assert
    assert result.total == 0
    assert called is False


def test_grade_majority_passes_expectation_won_by_two_of_three() -> None:
    # Arrange
    ev = make_eval(("a",))
    runs = [payload(True), payload(False), payload(True)]

    # Act
    result = grade(ev, "out", judges=3, call_json=sequence_call_json(runs))

    # Assert
    assert result.expectations[0].passed is True
    assert result.passed == 1


def test_grade_unanimous_gate_fails_on_two_of_three() -> None:
    # Arrange — a unanimous-gated expectation must NOT pass on a mere 2/3 majority
    ev = make_eval(("a",), gate="unanimous")
    runs = [payload(True), payload(False), payload(True)]

    # Act
    result = grade(ev, "out", judges=3, call_json=sequence_call_json(runs))

    # Assert
    assert result.expectations[0].passed is False
    assert result.passed == 0


def test_grade_records_vote_tally_and_color_on_aggregate() -> None:
    # Arrange — a 2/3 majority-gated expectation passes, but the raw tally + yellow color persist
    ev = make_eval(("a",))
    runs = [payload(True), payload(False), payload(True)]

    # Act
    result = grade(ev, "out", judges=3, call_json=sequence_call_json(runs))

    # Assert — the raw 2/3 tally is retained so color/audit can read it
    assert result.expectations[0].pass_votes == 2
    assert result.expectations[0].total_votes == 3
    assert result.expectations[0].color == "yellow"


def test_grade_majority_fails_expectation_lost_by_two_of_three() -> None:
    # Arrange
    ev = make_eval(("a",))
    runs = [payload(False), payload(True), payload(False)]

    # Act
    result = grade(ev, "out", judges=3, call_json=sequence_call_json(runs))

    # Assert
    assert result.expectations[0].passed is False
    assert result.passed == 0


def test_grade_majority_counts_a_judge_error_as_a_fail_vote() -> None:
    # Arrange — judges=3: two graders pass, the middle one errors. The error is one fail vote,
    # so the expectation is still carried 2/3 → pass (one bad judge can't sink the vote).
    ev = make_eval(("a",))
    actions = [payload(True), llm.LLMParseError("boom"), payload(True)]

    # Act
    result = grade(ev, "out", judges=3, call_json=flaky_call_json(actions))

    # Assert
    assert result.expectations[0].passed is True
    assert result.passed == 1


def test_grade_counts_total_judge_outage_as_fail_without_raising() -> None:
    # Arrange — every judge errors; the case must fail (all fail votes), not abort the run
    ev = make_eval(("a",))
    actions = [llm.LLMParseError("boom")] * 3

    # Act — must not raise
    result = grade(ev, "out", judges=3, call_json=flaky_call_json(actions))

    # Assert
    assert result.expectations[0].passed is False
    assert result.passed == 0
    assert result.total == 1


def test_grade_single_judge_error_is_a_fail_not_a_raise() -> None:
    # Arrange — judges=1, the only grader call errors
    ev = make_eval(("a",))

    # Act — counts as a fail, does not propagate the LLMError
    result = grade(ev, "out", judges=1, call_json=flaky_call_json([llm.LLMParseError("boom")]))

    # Assert
    assert result.expectations[0].passed is False


def test_grade_majority_evidence_comes_from_a_judge_matching_the_verdict() -> None:
    # Arrange — the first judge errors (a fail vote), the next two pass; the merged verdict is
    # pass, so its cited evidence must come from a passing judge, not the error-vote placeholder
    ev = make_eval(("a",))
    actions = [llm.LLMParseError("boom"), payload(True), payload(True)]

    # Act
    result = grade(ev, "out", judges=3, call_json=flaky_call_json(actions))

    # Assert
    assert result.expectations[0].passed is True
    assert result.expectations[0].evidence == "e0"


def test_grade_rejects_non_positive_judges() -> None:
    # Arrange
    ev = make_eval(("a",))

    # Act, Assert
    try:
        grade(ev, "out", judges=0, call_json=fixed_call_json(payload(True)))
    except ValueError:
        return

    raise AssertionError("expected ValueError for judges=0")


# --- ballots (OBS1) -------------------------------------------------------------


def test_grade_attaches_one_ballot_per_judge() -> None:
    # Arrange
    ev = make_eval(("exp0",))
    call_json = sequence_call_json([payload(True), payload(False), payload(True)])

    # Act
    result = grade(ev, "out", judges=3, call_json=call_json)

    # Assert — every judge's own votes survive aggregation, the dissent included
    assert [b.judge for b in result.ballots] == [1, 2, 3]
    assert [b.expectations[0].passed for b in result.ballots] == [True, False, True]
    assert result.ballots[1].expectations[0].evidence == "e0"
    assert all(b.error is None for b in result.ballots)


def test_grade_ballot_records_call_audit_metadata() -> None:
    # Arrange
    ev = make_eval(("exp0",))

    # Act
    result = grade(ev, "out", call_json=fixed_call_json(payload(True)))

    # Assert — session + spend ride on the ballot (the response is no longer discarded)
    ballot = result.ballots[0]
    assert ballot.session_id == "s"
    assert ballot.cost_usd == 0.1
    assert ballot.duration_ms == 100
    assert (ballot.input_tokens, ballot.output_tokens) == (10, 20)
    assert (ballot.cache_read_tokens, ballot.cache_creation_tokens) == (5, 7)


def test_grade_errored_ballot_keeps_the_error_without_metrics() -> None:
    # Arrange
    ev = make_eval(("exp0",))
    call_json = flaky_call_json(
        [llm.LLMTransportError("boom (session sid-x)"), payload(True), payload(True)]
    )

    # Act
    result = grade(ev, "out", judges=3, call_json=call_json)

    # Assert — the failed call is recorded as such, with no fabricated metadata
    errored = result.ballots[0]
    assert errored.error is not None and "boom" in errored.error
    assert errored.session_id is None and errored.cost_usd is None
    assert errored.expectations[0].evidence.startswith("judge error:")
    assert [b.error for b in result.ballots[1:]] == [None, None]


def test_grade_no_expectations_returns_no_ballots() -> None:
    # Arrange
    ev = make_eval(())

    # Act, Assert
    assert grade(ev, "out", call_json=fixed_call_json({"expectations": []})).ballots == ()
