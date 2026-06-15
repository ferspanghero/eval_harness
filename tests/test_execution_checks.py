"""Tests for execution checks — running commands / held-out tests in the produced workspace.

These exercise the real subprocess path with trivial, fast commands (``python -c``), not mocks: the
``claude -p`` boundary is the only thing the harness mocks; a pytest/typecheck subprocess is just a
fast local process here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from eval_harness.deterministic import RunArtifacts, parse_check
from eval_harness.deterministic.checks import execution
from eval_harness.deterministic.checks.execution import (
    AcceptanceTest,
    CommandSucceeds,
    CoverageAtLeast,
    _parse_coverage_total,
)

PY = sys.executable


def artifacts(workspace: Path, *, fixture_dir: Path | None = None) -> RunArtifacts:
    return RunArtifacts(response="", workspace=workspace, fixture_dir=fixture_dir or workspace)


# --- CommandSucceeds ----------------------------------------------------------


def test_command_succeeds_passes_on_exit_zero(tmp_path: Path) -> None:
    # Arrange
    check = CommandSucceeds(description="ok", command=f'{PY} -c "import sys; sys.exit(0)"')

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is True


def test_command_succeeds_fails_on_nonzero_exit(tmp_path: Path) -> None:
    # Arrange
    check = CommandSucceeds(description="bad", command=f'{PY} -c "import sys; sys.exit(1)"')

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


def test_command_succeeds_fails_when_binary_is_missing(tmp_path: Path) -> None:
    # Arrange — a command that can't even launch must fail, not crash the sweep
    check = CommandSucceeds(description="missing", command="definitely-not-a-real-binary-xyz")

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


def test_command_fails_when_it_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange — a command that outlives the (shortened) timeout must fail, not hang the sweep
    monkeypatch.setattr(execution, "_TIMEOUT_SECONDS", 0.2)
    check = CommandSucceeds(description="slow", command=f'{PY} -c "import time; time.sleep(5)"')

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False
    assert "timed out" in result.evidence


def test_command_fails_on_an_unbalanced_quote(tmp_path: Path) -> None:
    # Arrange — a malformed command must fail its check, not crash the sweep (shlex ValueError)
    check = CommandSucceeds(description="bad", command='echo "unterminated')

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


def test_command_fails_on_an_empty_command(tmp_path: Path) -> None:
    # Arrange — an empty/whitespace command splits to no argv; must fail, not crash (IndexError)
    check = CommandSucceeds(description="empty", command="   ")

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


def test_command_runs_in_the_workspace(tmp_path: Path) -> None:
    # Arrange — the command's cwd must be the produced workspace
    (tmp_path / "marker.txt").write_text("hi")
    code = "import os, sys; sys.exit(0 if os.path.exists('marker.txt') else 1)"
    check = CommandSucceeds(description="cwd", command=f'{PY} -c "{code}"')

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is True


# --- CoverageAtLeast ----------------------------------------------------------


def test_parse_coverage_total_extracts_percentage() -> None:
    # Arrange
    output = "Name   Stmts   Miss  Cover\nTOTAL     10      1    90%\n"

    # Act, Assert
    assert _parse_coverage_total(output) == 90.0


def test_parse_coverage_total_is_none_when_absent() -> None:
    # Act, Assert
    assert _parse_coverage_total("nothing here") is None


def test_parse_coverage_total_skips_a_total_line_without_a_percentage() -> None:
    # Act, Assert — a "TOTAL" line that carries no percent isn't a coverage figure
    assert _parse_coverage_total("TOTAL has no percent\nother") is None


def test_coverage_at_least_fails_when_no_total_line(tmp_path: Path) -> None:
    # Arrange — command exits 0 but emits no coverage summary
    check = CoverageAtLeast(
        description="cov", command=f"{PY} -c \"print('ran but no coverage')\"", threshold=90
    )

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


def test_coverage_at_least_passes_when_threshold_met(tmp_path: Path) -> None:
    # Arrange
    check = CoverageAtLeast(
        description="cov", command=f"{PY} -c \"print('TOTAL 7 0 0 0 95%')\"", threshold=90
    )

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is True


def test_coverage_at_least_fails_when_below_threshold(tmp_path: Path) -> None:
    # Arrange
    check = CoverageAtLeast(
        description="cov", command=f"{PY} -c \"print('TOTAL 7 3 0 0 60%')\"", threshold=90
    )

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


def test_coverage_at_least_fails_when_command_errors(tmp_path: Path) -> None:
    # Arrange — a coverage command that exits non-zero can't be trusted to have measured anything
    check = CoverageAtLeast(
        description="cov", command=f'{PY} -c "import sys; sys.exit(2)"', threshold=90
    )

    # Act
    result = check.evaluate(artifacts(tmp_path))

    # Assert
    assert result.passed is False


# --- AcceptanceTest -----------------------------------------------------------


def test_acceptance_test_copies_held_out_test_and_passes(tmp_path: Path) -> None:
    # Arrange — the held-out test lives in the fixture dir (the agent never saw it)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "acc.py").write_text("import sys; sys.exit(0)\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    check = AcceptanceTest(
        description="acc", source="acc.py", dest="acceptance/acc.py",
        command=f"{PY} acceptance/acc.py",
    )

    # Act
    result = check.evaluate(artifacts(workspace, fixture_dir=fixture))

    # Assert
    assert result.passed is True
    # the held-out test is removed after running, so it can't pollute later checks (mypy/ruff/cov)
    assert not (workspace / "acceptance" / "acc.py").exists()


def test_acceptance_test_fails_when_produced_code_breaks_it(tmp_path: Path) -> None:
    # Arrange
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "acc.py").write_text("import sys; sys.exit(1)\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    check = AcceptanceTest(
        description="acc", source="acc.py", dest="acc.py", command=f"{PY} acc.py"
    )

    # Act
    result = check.evaluate(artifacts(workspace, fixture_dir=fixture))

    # Assert
    assert result.passed is False


def test_acceptance_test_fails_when_held_out_source_is_missing(tmp_path: Path) -> None:
    # Arrange
    workspace = tmp_path / "ws"
    workspace.mkdir()
    check = AcceptanceTest(
        description="acc", source="missing.py", dest="acc.py", command=f"{PY} acc.py"
    )

    # Act
    result = check.evaluate(artifacts(workspace, fixture_dir=tmp_path))

    # Assert
    assert result.passed is False


# --- registry -----------------------------------------------------------------


def test_parse_check_builds_command_succeeds() -> None:
    # Act
    check = parse_check({"type": "command_succeeds", "description": "d", "command": "true"})

    # Assert
    assert isinstance(check, CommandSucceeds)


def test_parse_check_builds_coverage_at_least() -> None:
    # Act
    check = parse_check(
        {"type": "coverage_at_least", "description": "d", "command": "c", "threshold": 90}
    )

    # Assert
    assert isinstance(check, CoverageAtLeast)
    assert check.threshold == 90.0


def test_parse_check_builds_acceptance_test() -> None:
    # Act
    check = parse_check(
        {"type": "acceptance_test", "description": "d", "source": "s", "dest": "x", "command": "c"}
    )

    # Assert
    assert isinstance(check, AcceptanceTest)
