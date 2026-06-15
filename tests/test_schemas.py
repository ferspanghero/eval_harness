"""Tests for the fixture + result schemas (adapted from skill-creator's evals/grading shapes)."""

from __future__ import annotations

import json

import pytest

from eval_harness import schemas
from eval_harness.deterministic.checks.response import ResponseContains, ResponseContainsAny

FIXTURE_JSON = json.dumps(
    {
        "target": "code-review",
        "evals": [
            {
                "id": 1,
                "name": "planted-sql-injection",
                "prompt": "Review app.py and report issues.",
                "files": ["files/app.py"],
                "checks": [
                    {
                        "type": "response_contains",
                        "description": "names the vulnerable function",
                        "value": "run_query",
                    },
                    {
                        "type": "response_contains_any",
                        "description": "flags the injection class",
                        "values": ["SQL injection", "parameterized"],
                    },
                ],
                "expectations": [
                    {
                        "text": "The review flags the SQL injection as Critical or High severity",
                        "gate": "unanimous",
                    },
                    {
                        "text": "The review does not invent critical issues that are not present",
                        "gate": "majority",
                    },
                ],
            }
        ],
    }
)


# --- Fixture / Eval parsing ---------------------------------------------------


def test_fixture_parses_target_and_evals() -> None:
    # Act
    fixture = schemas.Fixture.from_json(FIXTURE_JSON)

    # Assert
    assert fixture.target == "code-review"
    assert len(fixture.evals) == 1


def test_fixture_parses_system_prompt() -> None:
    # Arrange — a command fixture carries the autonomous directive for the runner
    raw = json.dumps(
        {
            "target": "dev-pipeline-v2",
            "system_prompt": "Run all six phases autonomously.",
            "evals": [{"id": 1, "name": "n", "prompt": "p"}],
        }
    )

    # Act
    fixture = schemas.Fixture.from_json(raw)

    # Assert
    assert fixture.system_prompt == "Run all six phases autonomously."


def test_fixture_defaults_system_prompt_to_none() -> None:
    # Act — a skill fixture omits it
    fixture = schemas.Fixture.from_json(FIXTURE_JSON)

    # Assert
    assert fixture.system_prompt is None


def test_eval_parses_core_fields() -> None:
    # Arrange
    fixture = schemas.Fixture.from_json(FIXTURE_JSON)

    # Act
    ev = fixture.evals[0]

    # Assert
    assert ev.id == 1
    assert ev.name == "planted-sql-injection"
    assert ev.files == ("files/app.py",)
    assert ev.expectations[0].text.startswith("The review flags")


def test_eval_parses_checks_into_typed_objects() -> None:
    # Arrange
    ev = schemas.Fixture.from_json(FIXTURE_JSON).evals[0]

    # Act
    contains, contains_any = ev.checks

    # Assert
    assert isinstance(contains, ResponseContains)
    assert contains.value == "run_query"
    assert isinstance(contains_any, ResponseContainsAny)
    assert contains_any.values == ("SQL injection", "parameterized")


def test_eval_defaults_optional_fields_to_empty() -> None:
    # Arrange
    minimal = json.dumps(
        {"target": "s", "evals": [{"id": 1, "name": "n", "prompt": "p"}]}
    )

    # Act
    ev = schemas.Fixture.from_json(minimal).evals[0]

    # Assert
    assert ev.files == ()
    assert ev.checks == ()
    assert ev.expectations == ()
    assert ev.output_files == ()


def test_eval_parses_output_files() -> None:
    # Arrange — a file-producing fixture declares which produced files the judge should grade
    raw = json.dumps(
        {
            "target": "create-readme",
            "evals": [
                {"id": 1, "name": "n", "prompt": "p", "output_files": ["README.md"]}
            ],
        }
    )

    # Act
    ev = schemas.Fixture.from_json(raw).evals[0]

    # Assert
    assert ev.output_files == ("README.md",)


def test_fixture_rejects_eval_without_id() -> None:
    # Arrange
    bad = json.dumps({"target": "s", "evals": [{"name": "n", "prompt": "p"}]})

    # Act, Assert
    with pytest.raises(KeyError):
        schemas.Fixture.from_json(bad)


# --- GradingResult parsing (judge output) -------------------------------------


def test_grading_result_parses_expectations_and_summary() -> None:
    # Arrange
    payload = {
        "expectations": [
            {"text": "flags injection", "passed": True, "evidence": "line 4"},
            {"text": "no hallucinated criticals", "passed": False, "evidence": "invented X"},
        ],
        "summary": {"passed": 1, "failed": 1, "total": 2, "pass_rate": 0.5},
    }

    # Act
    result = schemas.GradingResult.from_payload(payload)

    # Assert
    assert result.total == 2
    assert result.passed == 1
    assert result.pass_rate == pytest.approx(0.5)
    assert result.expectations[0].passed is True
    assert result.expectations[1].evidence == "invented X"


def test_grading_result_ignores_inconsistent_model_summary() -> None:
    # Arrange — the model's summary disagrees with its own expectations list
    payload = {
        "expectations": [
            {"text": "a", "passed": True, "evidence": "x"},
            {"text": "b", "passed": True, "evidence": "y"},
        ],
        "summary": {"passed": 0, "failed": 2, "total": 2, "pass_rate": 0.0},
    }

    # Act
    result = schemas.GradingResult.from_payload(payload)

    # Assert — counts come from the expectations, not the untrusted summary
    assert result.passed == 2
    assert result.pass_rate == pytest.approx(1.0)


def test_grading_result_recomputes_summary_when_absent() -> None:
    # Arrange
    payload = {
        "expectations": [
            {"text": "a", "passed": True, "evidence": "x"},
            {"text": "b", "passed": True, "evidence": "y"},
            {"text": "c", "passed": False, "evidence": "z"},
        ]
    }

    # Act
    result = schemas.GradingResult.from_payload(payload)

    # Assert
    assert (result.passed, result.failed, result.total) == (2, 1, 3)
    assert result.pass_rate == pytest.approx(2 / 3)


# --- CalibrationCase parsing --------------------------------------------------


def test_calibration_case_parses_and_maps_pass_label() -> None:
    # Arrange
    raw = {
        "skill": "code-review",
        "task": "Review app.py.",
        "expectation": "flags the SQL injection",
        "output": "Critical: SQL injection in run_query.",
        "human_label": "pass",
    }

    # Act
    case = schemas.CalibrationCase.from_dict(raw)

    # Assert
    assert case.skill == "code-review"
    assert case.expectation == "flags the SQL injection"
    assert case.human_passed is True


def test_calibration_case_fail_label_is_not_passed() -> None:
    # Arrange, Act
    case = schemas.CalibrationCase.from_dict(
        {"skill": "s", "task": "t", "expectation": "e", "output": "o", "human_label": "fail"}
    )

    # Assert
    assert case.human_passed is False


def test_calibration_case_rejects_invalid_label() -> None:
    # Act, Assert — only "pass"/"fail" are valid human verdicts
    with pytest.raises(ValueError):
        schemas.CalibrationCase.from_dict(
            {"skill": "s", "task": "t", "expectation": "e", "output": "o", "human_label": "maybe"}
        )


# --- Expectation (per-expectation gate) ---------------------------------------


def test_expectation_from_dict_reads_text_and_gate() -> None:
    # Act — the gate is explicit; both fields are required
    exp = schemas.Expectation.from_dict({"text": "names the vuln", "gate": "unanimous"})

    # Assert
    assert exp.text == "names the vuln"
    assert exp.gate == "unanimous"


def test_expectation_from_dict_rejects_bare_string() -> None:
    # Act, Assert — a bare string omits the gate; that's malformed config, not a default
    with pytest.raises(ValueError):
        schemas.Expectation.from_dict("The review flags the injection")


def test_expectation_from_dict_rejects_missing_gate() -> None:
    # Act, Assert — an object without an explicit gate is malformed config
    with pytest.raises(ValueError):
        schemas.Expectation.from_dict({"text": "names the vuln"})


def test_expectation_rejects_unknown_gate() -> None:
    # Act, Assert — only "majority"/"unanimous" are valid gates
    with pytest.raises(ValueError):
        schemas.Expectation(text="t", gate="supermajority")


def test_majority_gate_passes_on_two_of_three() -> None:
    # Arrange
    exp = schemas.Expectation(text="t", gate="majority")

    # Act, Assert — a majority of votes passes, below-majority fails
    assert exp.passes(2, 3) is True
    assert exp.passes(1, 3) is False


def test_unanimous_gate_requires_all_votes() -> None:
    # Arrange
    exp = schemas.Expectation(text="t", gate="unanimous")

    # Act, Assert — only all-pass clears a unanimous gate; a mere majority fails it
    assert exp.passes(3, 3) is True
    assert exp.passes(2, 3) is False


def test_eval_parses_explicit_expectation_gates() -> None:
    # Arrange — every expectation states its gate explicitly
    raw = json.dumps(
        {
            "target": "code-review",
            "evals": [
                {
                    "id": 1, "name": "n", "prompt": "p",
                    "expectations": [
                        {"text": "quality is fine", "gate": "majority"},
                        {"text": "names the vuln", "gate": "unanimous"},
                    ],
                }
            ],
        }
    )

    # Act
    ev = schemas.Fixture.from_json(raw).evals[0]

    # Assert
    assert ev.expectations[0].gate == "majority"
    assert ev.expectations[1].gate == "unanimous"
    assert ev.expectations[1].text == "names the vuln"


def test_eval_rejects_bare_string_expectation() -> None:
    # Arrange — a fixture that omits the gate (bare string) is malformed and must fail to load
    raw = json.dumps(
        {
            "target": "s",
            "evals": [{"id": 1, "name": "n", "prompt": "p", "expectations": ["bare string"]}],
        }
    )

    # Act, Assert
    with pytest.raises(ValueError):
        schemas.Fixture.from_json(raw)


# --- ExpectationResult.color (vote strength, orthogonal to pass/fail) ---------


def test_expectation_result_color_green_when_unanimous() -> None:
    # Arrange, Act — every judge agreed
    result = schemas.ExpectationResult(
        text="t", passed=True, evidence="", pass_votes=3, total_votes=3
    )

    # Assert
    assert result.color == "green"


def test_expectation_result_color_yellow_on_majority_not_unanimous() -> None:
    # Arrange, Act — a 2/3 split: a majority, but not all
    result = schemas.ExpectationResult(
        text="t", passed=True, evidence="", pass_votes=2, total_votes=3
    )

    # Assert
    assert result.color == "yellow"


def test_expectation_result_color_red_below_majority() -> None:
    # Arrange, Act — fewer than a majority passed
    result = schemas.ExpectationResult(
        text="t", passed=False, evidence="", pass_votes=1, total_votes=3
    )

    # Assert
    assert result.color == "red"


def test_expectation_result_color_without_votes_follows_passed() -> None:
    # Arrange — a single ballot (not aggregated) has no vote counts; it colors from its boolean
    passed = schemas.ExpectationResult(text="t", passed=True, evidence="")
    failed = schemas.ExpectationResult(text="t", passed=False, evidence="")

    # Act, Assert
    assert passed.color == "green"
    assert failed.color == "red"
