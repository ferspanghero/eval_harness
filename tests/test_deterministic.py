"""Tests for the deterministic tier — self-contained Check classes over RunArtifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_harness.deterministic import (
    CheckResult,
    DeterministicResult,
    RunArtifacts,
    evaluate,
    parse_check,
)
from eval_harness.deterministic.checks.files import FileContains, FileExists
from eval_harness.deterministic.checks.response import ResponseContains, ResponseContainsAny


def artifacts(*, response: str = "", workspace: Path | None = None) -> RunArtifacts:
    return RunArtifacts(response=response, workspace=workspace or Path("."))


# --- ResponseContains ---------------------------------------------------------


def test_response_contains_passes_case_insensitively() -> None:
    # Arrange
    check = ResponseContains(description="names fn", value="run_query")

    # Act
    result = check.evaluate(artifacts(response="Issue in RUN_QUERY at line 4"))

    # Assert
    assert result.passed is True


def test_response_contains_fails_when_absent() -> None:
    # Arrange
    check = ResponseContains(description="names fn", value="run_query")

    # Act
    result = check.evaluate(artifacts(response="no mention here"))

    # Assert
    assert result.passed is False


# --- ResponseContainsAny ------------------------------------------------------


def test_response_contains_any_passes_when_one_matches() -> None:
    # Arrange
    check = ResponseContainsAny(description="class", values=("parameterized", "SQL injection"))

    # Act
    result = check.evaluate(artifacts(response="This is a SQL Injection risk."))

    # Assert
    assert result.passed is True


def test_response_contains_any_fails_when_none_match() -> None:
    # Arrange
    check = ResponseContainsAny(description="class", values=("parameterized", "SQL injection"))

    # Act
    result = check.evaluate(artifacts(response="looks fine to me"))

    # Assert
    assert result.passed is False


# --- file checks --------------------------------------------------------------


def test_file_exists_passes_when_file_present(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / "README.md").write_text("hi")
    check = FileExists(description="readme", path="README.md")

    # Act
    result = check.evaluate(artifacts(workspace=tmp_path))

    # Assert
    assert result.passed is True


def test_file_exists_fails_when_file_missing(tmp_path: Path) -> None:
    # Arrange
    check = FileExists(description="readme", path="README.md")

    # Act
    result = check.evaluate(artifacts(workspace=tmp_path))

    # Assert
    assert result.passed is False


def test_file_contains_passes_when_substring_present(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / "out.txt").write_text("the answer is 42")
    check = FileContains(description="ans", path="out.txt", value="42")

    # Act
    result = check.evaluate(artifacts(workspace=tmp_path))

    # Assert
    assert result.passed is True


def test_file_contains_fails_when_file_missing(tmp_path: Path) -> None:
    # Arrange
    check = FileContains(description="ans", path="nope.txt", value="42")

    # Act
    result = check.evaluate(artifacts(workspace=tmp_path))

    # Assert
    assert result.passed is False


def test_file_check_ignores_unused_response_slice(tmp_path: Path) -> None:
    # Arrange — a file check reads only the workspace; an unrelated response must not affect it
    (tmp_path / "README.md").write_text("hi")
    check = FileExists(description="readme", path="README.md")

    # Act
    result = check.evaluate(artifacts(response="irrelevant noise", workspace=tmp_path))

    # Assert
    assert result.passed is True


# --- registry / parse_check ---------------------------------------------------


def test_parse_check_dispatches_on_type_to_response_contains() -> None:
    # Arrange
    data = {"type": "response_contains", "description": "d", "value": "run_query"}

    # Act
    check = parse_check(data)

    # Assert
    assert isinstance(check, ResponseContains)
    assert check.value == "run_query"


def test_parse_check_dispatches_on_type_to_file_exists() -> None:
    # Arrange
    data = {"type": "file_exists", "description": "d", "path": "README.md"}

    # Act
    check = parse_check(data)

    # Assert
    assert isinstance(check, FileExists)
    assert check.path == "README.md"


def test_parse_check_dispatches_on_type_to_file_contains() -> None:
    # Arrange
    data = {"type": "file_contains", "description": "d", "path": "out.txt", "value": "42"}

    # Act
    check = parse_check(data)

    # Assert
    assert isinstance(check, FileContains)
    assert check.path == "out.txt"
    assert check.value == "42"


def test_parse_check_raises_on_unknown_type() -> None:
    # Act, Assert
    with pytest.raises(ValueError):
        parse_check({"type": "teleport", "description": "d"})


def test_parse_check_raises_value_error_on_missing_type() -> None:
    # Act, Assert — a check definition with no "type" is malformed; a ValueError, not a KeyError
    with pytest.raises(ValueError):
        parse_check({"description": "d"})


def test_parse_check_raises_value_error_naming_a_missing_required_field() -> None:
    # Arrange — response_contains requires a "value"
    data = {"type": "response_contains", "description": "d"}

    # Act, Assert — the error names the missing field rather than surfacing a bare KeyError
    with pytest.raises(ValueError, match="value"):
        parse_check(data)


# --- evaluate aggregation -----------------------------------------------------


def test_evaluate_aggregates_all_checks() -> None:
    # Arrange
    checks = (
        ResponseContains(description="present", value="found"),
        ResponseContains(description="absent", value="missing"),
    )

    # Act
    result = evaluate(checks, artifacts(response="found it"))

    # Assert
    assert result.total == 2
    assert result.passed == 1
    assert result.all_passed is False


# --- DeterministicResult / CheckResult (moved here from schemas) ---------------


def test_deterministic_result_aggregates_checks() -> None:
    # Arrange
    checks = (
        CheckResult(description="a", passed=True, evidence="ok"),
        CheckResult(description="b", passed=False, evidence="missing"),
    )

    # Act
    result = DeterministicResult.from_checks(checks)

    # Assert
    assert result.passed == 1
    assert result.total == 2
    assert result.all_passed is False


def test_deterministic_result_all_passed_when_every_check_passes() -> None:
    # Arrange
    checks = (CheckResult(description="a", passed=True, evidence="ok"),)

    # Act
    result = DeterministicResult.from_checks(checks)

    # Assert
    assert result.all_passed is True
