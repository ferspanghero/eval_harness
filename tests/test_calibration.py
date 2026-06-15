"""Tests for the calibration tier — judge-vs-human agreement on frozen cases (llm seam faked)."""

from __future__ import annotations

from typing import Any

from eval_harness import calibration, llm
from eval_harness.schemas import CalibrationCase


def make_case(human_label: str = "pass", skill: str = "code-review") -> CalibrationCase:
    return CalibrationCase(
        skill=skill, task="Review.", expectation="flags it", output="Critical: ...",
        human_label=human_label,
    )


def fixed_grade(verdict: bool) -> calibration.GradeCaseFn:
    def _grade(case: CalibrationCase) -> bool:
        return verdict

    return _grade


def fake_response() -> llm.LLMResponse:
    return llm.LLMResponse(
        text="", cost_usd=None, input_tokens=None, output_tokens=None,
        cache_read_tokens=None, cache_creation_tokens=None,
        duration_ms=None, session_id=None, raw={},
    )


def test_calibrate_agrees_when_judge_matches_human() -> None:
    # Arrange, Act — human says pass, judge says pass
    report = calibration.calibrate((make_case("pass"),), grade_case=fixed_grade(True))

    # Assert
    assert report.agreement_rate == 1.0
    assert report.disagreements == ()


def test_calibrate_flags_disagreement() -> None:
    # Arrange, Act — human says fail, judge says pass
    report = calibration.calibrate((make_case("fail"),), grade_case=fixed_grade(True))

    # Assert
    assert report.agreement_rate == 0.0
    assert len(report.disagreements) == 1
    assert report.disagreements[0].judge_passed is True


def test_calibrate_computes_agreement_rate_across_cases() -> None:
    # Arrange — judge always passes; one human-pass (agree) and one human-fail (disagree)
    cases = (make_case("pass"), make_case("fail"))

    # Act
    report = calibration.calibrate(cases, grade_case=fixed_grade(True))

    # Assert
    assert (report.agreed, report.total) == (1, 2)
    assert report.agreement_rate == 0.5


def test_report_breaks_down_agreement_by_skill() -> None:
    # Arrange — judge always passes: a human-pass code-review case agrees, a human-fail
    # security-audit case disagrees
    cases = (make_case("pass", skill="code-review"), make_case("fail", skill="security-audit"))

    # Act
    report = calibration.calibrate(cases, grade_case=fixed_grade(True))

    # Assert — agreement is tallied per skill so coverage and bias can be read per cell
    by = report.by_skill()
    assert by["code-review"] == (1, 1)
    assert by["security-audit"] == (0, 1)


def test_calibrate_empty_is_zero() -> None:
    # Arrange, Act
    report = calibration.calibrate((), grade_case=fixed_grade(True))

    # Assert
    assert report.total == 0
    assert report.agreement_rate == 0.0


def test_judge_case_returns_real_grader_verdict_for_single_expectation() -> None:
    # Arrange — fake the llm seam: the real grader returns pass for the one expectation
    payload = {"expectations": [{"text": "flags it", "passed": True, "evidence": "line 4"}]}

    def fake_call_json(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        return payload, fake_response()

    # Act
    verdict = calibration.judge_case(
        make_case("pass"), model="m", effort="high", call_json=fake_call_json
    )

    # Assert
    assert verdict is True


def test_judge_case_threads_judges_into_grader_majority_vote() -> None:
    # Arrange — three grader runs vote pass/pass/fail; the seam is faked per call so we can both
    # count the runs and check the majority verdict wins
    verdicts = iter([True, True, False])
    calls = 0

    def fake_call_json(request: llm.LLMRequest, **kwargs: Any) -> tuple[Any, llm.LLMResponse]:
        nonlocal calls
        calls += 1
        payload = {"expectations": [{"text": "flags it", "passed": next(verdicts), "evidence": ""}]}
        return payload, fake_response()

    # Act — judges=3 must reach judge.grade, so the grader runs 3× and majority (pass) wins
    verdict = calibration.judge_case(
        make_case("pass"), model="m", effort="high", judges=3, call_json=fake_call_json
    )

    # Assert
    assert calls == 3
    assert verdict is True
