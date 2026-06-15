"""Tests for the orchestration + CLI: run -> deterministic -> (gated) judge -> benchmark."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

import pytest

from eval_harness import analyzer, calibration, cli, llm
from eval_harness.deterministic.checks.response import ResponseContains
from eval_harness.runner import RunError, RunResult
from eval_harness.schemas import (
    Eval,
    Expectation,
    ExpectationResult,
    Fixture,
    GradingResult,
    JudgeBallot,
    ProducedFiles,
)


def run_fn_returning(output_text: str, *, workspace: Path = Path(".")) -> cli.RunFn:
    def _run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        return RunResult(
            eval_id=ev.id, target=target, workspace=workspace, output_text=output_text,
            session_id="s",
            cost_usd=0.1, input_tokens=10, output_tokens=10,
            cache_read_tokens=5, cache_creation_tokens=5, duration_ms=100,
        )

    return _run


def capturing_grade_fn(produced: list[ProducedFiles]) -> cli.GradeFn:
    """A passing grade_fn that records the produced-file contents the orchestrator handed it."""

    def _grade(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        produced.append(produced_files)

        return GradingResult.from_payload(
            {"expectations": [{"text": "e", "passed": True, "evidence": ""}]}
        )

    return _grade


def recording_grade_fn(passed: int, total: int) -> tuple[cli.GradeFn, list[int]]:
    calls: list[int] = []

    def _grade(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        calls.append(ev.id)
        payload = {"expectations": [
            {"text": f"e{i}", "passed": i < passed, "evidence": ""} for i in range(total)
        ]}

        return GradingResult.from_payload(payload)

    return _grade, calls


def recording_effort_grade_fn() -> tuple[cli.GradeFn, list[str | None]]:
    """A passing grade_fn that records the effort it was handed, for threading assertions."""
    received: list[str | None] = []

    def _grade(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        received.append(effort)

        return GradingResult.from_payload(
            {"expectations": [{"text": "flags it", "passed": True, "evidence": ""}]}
        )

    return _grade, received


def fixture_with_check() -> Fixture:
    ev = Eval(
        id=1, name="planted", prompt="Review.",
        checks=(ResponseContains(description="d", value="run_query"),),
        expectations=(Expectation("flags it", gate="majority"),),
    )

    return Fixture(target="code-review", evals=(ev,))


# --- system_prompt threading (R2) ---------------------------------------


def test_run_evals_threads_fixture_system_prompt_to_run_fn() -> None:
    # Arrange — a command fixture's autonomous directive must reach the runner via run_fn
    received: list[str | None] = []

    def recording_run(
        ev: Eval,
        fixture_dir: Path,
        target: str,
        target_path: Path,
        system_prompt: str | None,
        model: str,
        effort: str,
    ) -> RunResult:
        received.append(system_prompt)

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    fixture = Fixture(
        target="dev-pipeline-v2",
        system_prompt="RUN AUTONOMOUSLY",
        evals=(
            Eval(
                id=1, name="n", prompt="p",
                checks=(ResponseContains(description="d", value="ok"),),
            ),
        ),
    )

    # Act
    cli.run_evals(
        fixture, Path("."), mode="deterministic", judges=1, model="m", effort="e",
        run_fn=recording_run,
    )

    # Assert
    assert received == ["RUN AUTONOMOUSLY"]


# --- effort -------------------------------------------------------------


def test_run_evals_passes_effort_to_grader() -> None:
    # Arrange
    grade_fn, received = recording_effort_grade_fn()

    # Act — judge mode so the grader always runs
    cli.run_evals(
        fixture_with_check(), Path("."), mode="judge", judges=1, model="opus", effort="high",
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert received == ["high"]


def test_main_passes_effort_flag(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_fixture(tmp_path)
    grade_fn, received = recording_effort_grade_fn()

    # Act
    cli.main(
        ["run", "judge", "--target", str(fixture_dir), "--effort", "high",
         "--out", str(tmp_path / "b")],
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert received == ["high"]


def test_main_model_defaults_to_opus_for_run_and_judge(tmp_path: Path) -> None:
    # Arrange — capture the model handed to both the runner and the judge
    fixture_dir = write_fixture(tmp_path)
    run_models: list[str | None] = []
    grade_models: list[str | None] = []

    def run_fn(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        run_models.append(model)

        return run_fn_returning("issue in run_query")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    def grade_fn(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        grade_models.append(model)

        return GradingResult.from_payload(
            {"expectations": [{"text": "flags it", "passed": True, "evidence": ""}]}
        )

    # Act — no --model flag, so the CLI default applies
    cli.main(
        ["run", "judge", "--target", str(fixture_dir), "--out", str(tmp_path / "b")],
        run_fn=run_fn, grade_fn=grade_fn,
    )

    # Assert — the one shared default reaches both consumers
    assert run_models == ["claude-opus-4-8"]
    assert grade_models == ["claude-opus-4-8"]


def test_main_effort_defaults_to_xhigh(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_fixture(tmp_path)
    grade_fn, received = recording_effort_grade_fn()

    # Act — no --effort flag
    cli.main(
        ["run", "judge", "--target", str(fixture_dir), "--out", str(tmp_path / "b")],
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert received == ["xhigh"]


# --- produced files fed to the judge (J3) -------------------------------------


def fixture_producing(output_files: tuple[str, ...]) -> Fixture:
    ev = Eval(
        id=1, name="readme", prompt="Write a README.",
        output_files=output_files,
        expectations=(Expectation("describes the project", gate="majority"),),
    )

    return Fixture(target="create-readme", evals=(ev,))


def test_run_evals_reads_output_files_and_passes_to_grader(tmp_path: Path) -> None:
    # Arrange — the skill produced README.md in the run workspace; the eval declares it as output
    (tmp_path / "README.md").write_text("# Slugify\n\nURL-safe slugs.")
    produced: list[ProducedFiles] = []

    # Act — judge mode so the grader runs; the orchestrator reads declared files from the workspace
    cli.run_evals(
        fixture_producing(("README.md",)), Path("."), mode="judge",
        judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("I wrote a README.", workspace=tmp_path),
        grade_fn=capturing_grade_fn(produced),
    )

    # Assert — the judge received the file's actual content read from the workspace
    assert produced == [(("README.md", "# Slugify\n\nURL-safe slugs."),)]


def test_run_evals_passes_none_for_missing_output_file(tmp_path: Path) -> None:
    # Arrange — the eval declares an output file the skill did not produce (workspace is empty)
    produced: list[ProducedFiles] = []

    # Act
    cli.run_evals(
        fixture_producing(("README.md",)), Path("."), mode="judge",
        judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("done", workspace=tmp_path),
        grade_fn=capturing_grade_fn(produced),
    )

    # Assert — the missing file is reported as None (the judge renders a placeholder downstream)
    assert produced == [(("README.md", None),)]


def test_deterministic_mode_does_not_call_judge() -> None:
    # Arrange
    grade_fn, calls = recording_grade_fn(1, 1)

    # Act
    bench = cli.run_evals(
        fixture_with_check(), Path("."), mode="deterministic", judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert calls == []
    assert bench.outcomes[0].judge_total == 0
    assert bench.outcomes[0].deterministic_passed == 1


def test_all_mode_gates_judge_on_deterministic_pass() -> None:
    # Arrange
    grade_fn, calls = recording_grade_fn(1, 1)

    # Act — output lacks "run_query", so deterministic fails and judge is skipped
    bench = cli.run_evals(
        fixture_with_check(), Path("."), mode="all", judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("nothing relevant"), grade_fn=grade_fn,
    )

    # Assert
    assert calls == []
    assert bench.outcomes[0].deterministic_passed == 0


def test_all_mode_runs_judge_when_deterministic_passes() -> None:
    # Arrange
    grade_fn, calls = recording_grade_fn(1, 1)

    # Act
    cli.run_evals(
        fixture_with_check(), Path("."), mode="all", judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert calls == [1]


def test_judge_mode_runs_judge_regardless_of_deterministic() -> None:
    # Arrange
    grade_fn, calls = recording_grade_fn(1, 1)

    # Act — deterministic fails but judge mode is ungated
    cli.run_evals(
        fixture_with_check(), Path("."), mode="judge", judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("nothing relevant"), grade_fn=grade_fn,
    )

    # Assert
    assert calls == [1]


# --- verdict display (PASS/FAIL + vote-strength color) ------------------------


def test_verdict_line_formats_pass_fail_with_color() -> None:
    # Act, Assert — PASS/FAIL is the gate decision; the icon is the orthogonal vote-strength color
    assert cli._verdict_line("alpha", passed=True, color="green") == "alpha: PASS 🟢"
    assert cli._verdict_line("beta", passed=True, color="yellow") == "beta: PASS 🟡"
    assert cli._verdict_line("gamma", passed=False, color="red") == "gamma: FAIL 🔴"


# --- exit codes ---------------------------------------------------------------


def test_verdict_pass_when_all_evals_pass() -> None:
    # Arrange
    grade_fn, _ = recording_grade_fn(1, 1)
    bench = cli.run_evals(
        fixture_with_check(), Path("."), mode="all", judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Act, Assert
    assert cli.verdict(bench) == cli.EXIT_PASS


def test_verdict_fail_when_an_eval_fails() -> None:
    # Arrange
    grade_fn, _ = recording_grade_fn(0, 1)
    bench = cli.run_evals(
        fixture_with_check(), Path("."), mode="all", judges=1, model="opus", effort="max",
        run_fn=run_fn_returning("nothing relevant"), grade_fn=grade_fn,
    )

    # Act, Assert
    assert cli.verdict(bench) == cli.EXIT_FAIL


def test_run_evals_propagates_run_error() -> None:
    # Arrange
    def failing(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        raise RunError("boom")

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act, Assert
    with pytest.raises(RunError):
        cli.run_evals(
            fixture_with_check(), Path("."), mode="deterministic",
            judges=1, model="opus", effort="max",
            run_fn=failing, grade_fn=grade_fn,
        )


# --- main() end-to-end (injected, no real claude) -----------------------------


_ONE_EVAL_JSON = (
    '{"target": "code-review", "evals": [{"id": 1, "name": "n", "prompt": "Review.",'
    ' "checks": [{"type": "response_contains", "description": "d", "value": "run_query"}],'
    ' "expectations": [{"text": "flags it", "gate": "majority"}]}]}'
)


def write_target(tmp_path: Path, name: str, evals_json: str) -> Path:
    """Create a target file + its sibling evals/evals.json; return the target file path."""
    d = tmp_path / name
    (d / "evals").mkdir(parents=True)
    (d / "evals" / "evals.json").write_text(evals_json)
    target = d / "SKILL.md"
    target.write_text(f"# {name}\nInstructions under test.")

    return target


def write_fixture(tmp_path: Path) -> Path:
    return write_target(
        tmp_path, "fx",
        '{"target": "code-review", "evals": [{"id": 1, "name": "n", "prompt": "Review.",'
        ' "checks": [{"type": "response_contains", "description": "d", "value": "run_query"}],'
        ' "expectations": [{"text": "flags it", "gate": "majority"}]}]}',
    )


def test_main_all_pass_writes_benchmark_and_returns_zero(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)
    evaluations = tmp_path / "evaluations"

    # Act
    code = cli.main(
        ["run", "all", "--target", str(fixture_dir), "--out", str(evaluations)],
        run_fn=run_fn_returning("issue in run_query"),
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_PASS
    assert list(evaluations.glob("*/results.json"))


def test_main_returns_error_on_run_failure(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_fixture(tmp_path)

    def failing(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        raise RunError("boom")

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(fixture_dir), "--out", str(tmp_path / "b")],
        run_fn=failing,
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_analyze_prints_notes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Arrange
    benchmarks = tmp_path / "benchmarks"
    (benchmarks / "20260101T000000Z").mkdir(parents=True)
    (benchmarks / "20260101T000000Z" / "results.json").write_text('{"target": "code-review"}')

    def fake_analyze(
        bench: dict[str, object], **kwargs: object
    ) -> list[analyzer.AnalysisNote]:
        return [analyzer.AnalysisNote(severity="warning", text="observation one")]

    # Act
    code = cli.main(["run", "analyze", "--out", str(benchmarks)], analyze_fn=fake_analyze)

    # Assert
    out = capsys.readouterr().out
    assert code == cli.EXIT_PASS
    assert "observation one" in out
    assert "🟡 observation one" in out


# --- calibrate mode -----------------------------------------------------------


def write_labels(tmp_path: Path) -> Path:
    path = tmp_path / "labels.jsonl"
    path.write_text(
        '{"skill":"code-review","task":"t","expectation":"e1","output":"o","human_label":"pass"}\n'
        '{"skill":"security-audit","task":"t","expectation":"e2","output":"o","human_label":"fail"}\n'
    )

    return path


def fake_calibrate(cases: object, **kwargs: object) -> calibration.CalibrationReport:
    # The judge always passes; so a human-pass case agrees and a human-fail case disagrees.
    results = tuple(
        calibration.CaseResult(case=c, judge_passed=True, agree=c.human_passed)
        for c in cases  # type: ignore[attr-defined]
    )

    return calibration.CalibrationReport.from_results(results)


def test_main_calibrate_prints_agreement_and_disagreements(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    labels = write_labels(tmp_path)

    # Act
    code = cli.main(
        ["run", "calibrate", "--ground-truth", str(labels)], calibrate_fn=fake_calibrate,
    )

    # Assert — overall tally, the per-skill breakdown, and the disagreeing case are all printed
    out = capsys.readouterr().out
    assert code == cli.EXIT_PASS
    assert "1/2" in out
    assert "security-audit: 0/1" in out
    assert "e2" in out


def test_main_calibrate_forwards_judges_flag(tmp_path: Path) -> None:
    # Arrange — capture the kwargs the CLI hands the calibrate function
    labels = write_labels(tmp_path)
    captured: dict[str, object] = {}

    def capturing_calibrate(cases: object, **kwargs: object) -> calibration.CalibrationReport:
        captured.update(kwargs)
        return fake_calibrate(cases)

    # Act
    cli.main(
        ["run", "calibrate", "--ground-truth", str(labels), "--judges", "5"],
        calibrate_fn=capturing_calibrate,
    )

    # Assert — the parsed --judges reaches the grader so calibration can majority-vote
    assert captured["judges"] == 5


def test_main_calibrate_defaults_judges_to_three(tmp_path: Path) -> None:
    # Arrange
    labels = write_labels(tmp_path)
    captured: dict[str, object] = {}

    def capturing_calibrate(cases: object, **kwargs: object) -> calibration.CalibrationReport:
        captured.update(kwargs)
        return fake_calibrate(cases)

    # Act — no --judges flag
    cli.main(["run", "calibrate", "--ground-truth", str(labels)], calibrate_fn=capturing_calibrate)

    # Assert — majority-of-3 by default, so borderline cases aren't decided by a single noisy run
    assert captured["judges"] == 3


def test_main_calibrate_errors_when_no_labels_file(tmp_path: Path) -> None:
    # Act
    code = cli.main(["run", "calibrate", "--ground-truth", str(tmp_path / "missing.jsonl")])

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_calibrate_errors_when_grader_fails(tmp_path: Path) -> None:
    # Arrange — labels exist, but the grader LLM call fails
    labels = write_labels(tmp_path)

    def failing_calibrate(cases: object, **kwargs: object) -> calibration.CalibrationReport:
        raise llm.LLMParseError("grader returned non-JSON")

    # Act
    code = cli.main(
        ["run", "calibrate", "--ground-truth", str(labels)], calibrate_fn=failing_calibrate,
    )

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_calibrate_errors_on_empty_labels(tmp_path: Path) -> None:
    # Arrange — file exists but has no cases
    empty = tmp_path / "labels.jsonl"
    empty.write_text("")

    # Act
    code = cli.main(["run", "calibrate", "--ground-truth", str(empty)], calibrate_fn=fake_calibrate)

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_calibrate_loads_cases_from_multiple_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange — the per-skill split: one ground-truth file per skill
    code_review = tmp_path / "code-review.jsonl"
    code_review.write_text(
        '{"skill":"code-review","task":"t","expectation":"e1","output":"o","human_label":"pass"}\n'
    )
    security_audit = tmp_path / "security-audit.jsonl"
    security_audit.write_text(
        '{"skill":"security-audit","task":"t","expectation":"e2","output":"o","human_label":"pass"}\n'
    )

    # Act — pass both per-skill files
    code = cli.main(
        ["run", "calibrate", "--ground-truth", str(code_review), str(security_audit)],
        calibrate_fn=fake_calibrate,
    )

    # Assert — cases from every file are graded together
    out = capsys.readouterr().out
    assert code == cli.EXIT_PASS
    assert "2/2" in out
    assert "code-review: 1/1" in out
    assert "security-audit: 1/1" in out


def test_main_calibrate_errors_when_ground_truth_omitted() -> None:
    # Act — calibrate with no --ground-truth at all
    code = cli.main(["run", "calibrate"], calibrate_fn=fake_calibrate)

    # Assert — the flag is mandatory for calibrate (no default set)
    assert code == cli.EXIT_ERROR


def test_main_calibrate_errors_when_any_file_missing(tmp_path: Path) -> None:
    # Arrange — one real file alongside one that does not exist
    present = write_labels(tmp_path)
    missing = tmp_path / "missing.jsonl"

    # Act
    code = cli.main(
        ["run", "calibrate", "--ground-truth", str(present), str(missing)],
        calibrate_fn=fake_calibrate,
    )

    # Assert — every named file must exist
    assert code == cli.EXIT_ERROR


def test_main_analyze_errors_when_no_benchmark(tmp_path: Path) -> None:
    # Act
    code = cli.main(["run", "analyze", "--out", str(tmp_path / "empty")])

    # Assert
    assert code == cli.EXIT_ERROR


# --- all-fixtures sweep (default when --skill is omitted) --------------------


def write_two_targets(tmp_path: Path) -> list[Path]:
    """Two target files (alpha, beta), each with a sibling evals/evals.json; return both paths."""
    targets = []
    for name in ("alpha", "beta"):
        content = {
            "target": name,
            "evals": [
                {"id": 1, "name": "n", "prompt": "p",
                 "checks": [{"type": "response_contains", "description": "d", "value": "ok"}]},
            ],
        }
        targets.append(write_target(tmp_path, name, json.dumps(content)))

    return targets


def test_main_single_target_still_works(tmp_path: Path) -> None:
    # Arrange
    target = write_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)
    benchmarks = tmp_path / "b"

    # Act — one explicit --target
    code = cli.main(
        ["run", "all", "--target", str(target), "--out", str(benchmarks)],
        run_fn=run_fn_returning("issue in run_query"),
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_PASS
    bench = json.loads(next(benchmarks.glob("*/results.json")).read_text())
    assert bench["summary"]["num_fixtures"] == 1


def test_suite_verdict_fails_when_no_benchmarks() -> None:
    # Act, Assert
    assert cli._suite_verdict([]) == cli.EXIT_FAIL


def test_main_analyze_errors_when_analysis_raises(tmp_path: Path) -> None:
    # Arrange — a benchmark exists, but the analyzer LLM call fails
    benchmarks = tmp_path / "benchmarks"
    (benchmarks / "20260101T000000Z").mkdir(parents=True)
    (benchmarks / "20260101T000000Z" / "results.json").write_text('{"target": "x"}')

    def failing_analyze(bench: dict[str, object], **kwargs: object) -> list[analyzer.AnalysisNote]:
        raise llm.LLMParseError("bad json")

    # Act
    code = cli.main(["run", "analyze", "--out", str(benchmarks)], analyze_fn=failing_analyze)

    # Assert
    assert code == cli.EXIT_ERROR


# --- --eval filter + regrade mode -------------------------------------------


def recording_run_fn() -> tuple[cli.RunFn, list[int]]:
    """A run_fn that records the eval ids it was asked to run (to assert it isn't called)."""
    ran: list[int] = []

    def _run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        ran.append(ev.id)

        return run_fn_returning("run_query ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    return _run, ran


def recording_output_grade_fn() -> tuple[cli.GradeFn, list[str]]:
    """A passing grade_fn that records the output_text it graded (for regrade assertions)."""
    seen: list[str] = []

    def _grade(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        seen.append(output_text)

        return GradingResult.from_payload(
            {"expectations": [{"text": "flags it", "passed": True, "evidence": ""}]}
        )

    return _grade, seen


def write_two_eval_fixture(tmp_path: Path) -> Path:
    return write_target(tmp_path, "fx2", json.dumps({
        "target": "code-review",
        "evals": [
            {"id": 1, "name": "alpha", "prompt": "Review.",
             "checks": [{"type": "response_contains", "description": "d", "value": "run_query"}],
             "expectations": [{"text": "flags it", "gate": "majority"}]},
            {"id": 2, "name": "beta", "prompt": "Review.",
             "checks": [{"type": "response_contains", "description": "d", "value": "run_query"}],
             "expectations": [{"text": "flags it", "gate": "majority"}]},
        ],
    }))


def write_results(evaluations_root: Path, evals: list[dict[str, object]]) -> None:
    """Persist a minimal results.json (one fixture) so regrade has saved outputs to grade."""
    out = evaluations_root / "20260101T000000Z"
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps({
        "summary": {}, "fixtures": [{"target": "code-review", "evals": evals}],
    }))


def test_main_eval_filter_by_id_runs_only_selected(tmp_path: Path) -> None:
    # Arrange — a 2-eval fixture; --eval 2 must run only eval 2
    fixture_dir = write_two_eval_fixture(tmp_path)
    run_fn, ran = recording_run_fn()
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(fixture_dir), "--eval", "2",
         "--out", str(tmp_path / "ev")],
        run_fn=run_fn, grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_PASS
    assert ran == [2]


def test_main_eval_filter_by_name_runs_only_selected(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_two_eval_fixture(tmp_path)
    run_fn, ran = recording_run_fn()
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    cli.main(
        ["run", "deterministic", "--target", str(fixture_dir), "--eval", "alpha",
         "--out", str(tmp_path / "ev")],
        run_fn=run_fn, grade_fn=grade_fn,
    )

    # Assert
    assert ran == [1]


def test_main_eval_filter_no_match_errors(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_two_eval_fixture(tmp_path)
    run_fn, ran = recording_run_fn()
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act — a selector matching no eval is an error, and nothing runs
    code = cli.main(
        ["run", "deterministic", "--target", str(fixture_dir), "--eval", "99",
         "--out", str(tmp_path / "ev")],
        run_fn=run_fn, grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR
    assert ran == []


def test_main_regrade_grades_saved_output_without_running(tmp_path: Path) -> None:
    # Arrange — saved run holds eval 1's output; current fixture has evals 1 and 2
    evaluations = tmp_path / "ev"
    write_results(evaluations, [{"eval_id": 1, "eval_name": "a", "output_text": "SAVED REVIEW"}])
    fixture_dir = write_two_eval_fixture(tmp_path)
    run_fn, ran = recording_run_fn()
    grade_fn, seen = recording_output_grade_fn()

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(evaluations)],
        run_fn=run_fn, grade_fn=grade_fn,
    )

    # Assert — the agent never ran; eval 1 was graded against its saved text; eval 2 (no saved
    # output) was skipped
    assert code == cli.EXIT_PASS
    assert ran == []
    assert seen == ["SAVED REVIEW"]


def test_main_regrade_no_saved_results_errors(tmp_path: Path) -> None:
    # Arrange — no results.json anywhere under the evaluations root
    fixture_dir = write_two_eval_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(tmp_path / "empty")],
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_regrade_respects_eval_filter(tmp_path: Path) -> None:
    # Arrange — saved outputs for evals 1 and 2; --eval 2 regrades only eval 2
    evaluations = tmp_path / "ev"
    write_results(evaluations, [
        {"eval_id": 1, "eval_name": "alpha", "output_text": "A"},
        {"eval_id": 2, "eval_name": "beta", "output_text": "B"},
    ])
    fixture_dir = write_two_eval_fixture(tmp_path)
    grade_fn, graded = recording_grade_fn(1, 1)

    # Act
    cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--eval", "2",
         "--out", str(evaluations)],
        grade_fn=grade_fn,
    )

    # Assert
    assert graded == [2]


def test_main_regrade_fails_when_expectation_fails(tmp_path: Path) -> None:
    # Arrange — the judge fails the expectation against the saved output
    evaluations = tmp_path / "ev"
    write_results(evaluations, [{"eval_id": 1, "eval_name": "alpha", "output_text": "X"}])
    fixture_dir = write_two_eval_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(0, 1)

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(evaluations)],
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_FAIL


def test_main_regrade_no_matching_eval_errors(tmp_path: Path) -> None:
    # Arrange — saved results exist but the --eval selector matches nothing to grade
    evaluations = tmp_path / "ev"
    write_results(evaluations, [{"eval_id": 1, "eval_name": "alpha", "output_text": "X"}])
    fixture_dir = write_two_eval_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--eval", "99",
         "--out", str(evaluations)],
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR


# --- run_suite: bounded-concurrency sweep (PERF1) ---------------------------------


def suite_fixture(target: str, eval_id: int) -> tuple[Fixture, Path, Path]:
    fixture = Fixture(
        target=target,
        evals=(
            Eval(
                id=eval_id, name=f"{target}-eval", prompt="p",
                checks=(ResponseContains(description="d", value="ok"),),
            ),
        ),
    )

    return fixture, Path("."), Path("SKILL.md")


def two_fixture_suite() -> list[tuple[Fixture, Path, Path]]:
    return [suite_fixture("alpha", 1), suite_fixture("beta", 2)]


def test_run_suite_returns_benchmarks_in_fixture_declaration_order() -> None:
    # Arrange
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    benchmarks = cli.run_suite(
        two_fixture_suite(), mode="deterministic", judges=1, model="m", effort="e",
        concurrency=1, run_fn=run_fn_returning("ok"), grade_fn=grade_fn,
    )

    # Assert
    assert [b.target for b in benchmarks] == ["alpha", "beta"]
    assert all(b.all_passed for b in benchmarks)


def test_run_suite_serial_runs_evals_in_declaration_order() -> None:
    # Arrange
    started: list[str] = []

    def recording_run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        started.append(target)

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    cli.run_suite(
        two_fixture_suite(), mode="deterministic", judges=1, model="m", effort="e",
        concurrency=1, run_fn=recording_run, grade_fn=grade_fn,
    )

    # Assert — a 1-worker pool executes in submission order, preserving today's serial behavior
    assert started == ["alpha", "beta"]


def test_run_suite_keeps_fixture_order_when_completion_is_out_of_order() -> None:
    # Arrange — alpha's run blocks until beta's run finishes, so beta completes first
    beta_done = threading.Event()

    def staggered_run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        if target == "alpha":
            assert beta_done.wait(timeout=5)

        result = run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

        if target == "beta":
            beta_done.set()

        return result

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    benchmarks = cli.run_suite(
        two_fixture_suite(), mode="deterministic", judges=1, model="m", effort="e",
        concurrency=2, run_fn=staggered_run, grade_fn=grade_fn,
    )

    # Assert — results land in declaration order regardless of completion order
    assert [b.target for b in benchmarks] == ["alpha", "beta"]


def test_run_suite_overlaps_evals_up_to_concurrency() -> None:
    # Arrange — the barrier only releases when both runs are in flight at once
    rendezvous = threading.Barrier(2)

    def barrier_run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        rendezvous.wait(timeout=5)  # BrokenBarrierError if the runs are serialized

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    benchmarks = cli.run_suite(
        two_fixture_suite(), mode="deterministic", judges=1, model="m", effort="e",
        concurrency=2, run_fn=barrier_run, grade_fn=grade_fn,
    )

    # Assert
    assert len(benchmarks) == 2


def test_run_suite_caps_in_flight_evals_at_concurrency() -> None:
    # Arrange — 4 evals on 2 workers; track the high-water mark of simultaneous runs
    lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0

    def counting_run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        nonlocal in_flight, max_in_flight

        with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)

        time.sleep(0.05)

        with lock:
            in_flight -= 1

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    fixtures = [suite_fixture(name, i) for i, name in enumerate(("a", "b", "c", "d"), start=1)]
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    cli.run_suite(
        fixtures, mode="deterministic", judges=1, model="m", effort="e",
        concurrency=2, run_fn=counting_run, grade_fn=grade_fn,
    )

    # Assert
    assert max_in_flight <= 2


def test_run_suite_skips_pending_evals_after_any_eval_chain_error() -> None:
    # Arrange — serial pool; a non-RunError crash in eval 1's chain must still skip eval 2,
    # so a sweep doesn't keep burning paid agent runs after it is already doomed
    ran: list[str] = []

    def crashing_first(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        if target == "alpha":
            raise ValueError("chain crashed outside the runner")

        ran.append(target)

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act, Assert
    with pytest.raises(ValueError):
        cli.run_suite(
            two_fixture_suite(), mode="deterministic", judges=1, model="m", effort="e",
            concurrency=1, run_fn=crashing_first, grade_fn=grade_fn,
        )

    assert ran == []


def test_run_suite_skips_pending_evals_after_a_run_error() -> None:
    # Arrange — serial pool; the first eval's run fails, so the second must never start
    ran: list[str] = []

    def failing_first(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        if target == "alpha":
            raise RunError("boom")

        ran.append(target)

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act, Assert
    with pytest.raises(RunError):
        cli.run_suite(
            two_fixture_suite(), mode="deterministic", judges=1, model="m", effort="e",
            concurrency=1, run_fn=failing_first, grade_fn=grade_fn,
        )

    assert ran == []


def test_run_suite_rejects_concurrency_below_one() -> None:
    # Act, Assert
    with pytest.raises(ValueError):
        cli.run_suite([], mode="deterministic", judges=1, model="m", effort="e", concurrency=0)


# --- --concurrency flag (PERF1) ----------------------------------------------------


def test_concurrency_below_one_is_rejected_at_parse_time() -> None:
    # Act, Assert
    with pytest.raises(argparse.ArgumentTypeError):
        cli._positive_int("0")


def test_concurrency_parses_positive_values() -> None:
    # Act, Assert
    assert cli._positive_int("8") == 8


def test_default_concurrency_is_cpu_bounded_at_eight() -> None:
    # Act, Assert — workers are subprocess-I/O-bound; the cap guards the subscription rate limit
    assert min(8, os.cpu_count() or 1) == cli.DEFAULT_CONCURRENCY


def test_main_threads_concurrency_to_the_suite(tmp_path: Path) -> None:
    # Arrange — two targets; the barrier only releases when both runs are in flight at once
    alpha, beta = write_two_targets(tmp_path)
    rendezvous = threading.Barrier(2)

    def barrier_run(
        ev: Eval, fixture_dir: Path, target: str, target_path: Path,
        system_prompt: str | None, model: str, effort: str,
    ) -> RunResult:
        rendezvous.wait(timeout=5)

        return run_fn_returning("ok")(
            ev, fixture_dir, target, target_path, system_prompt, model, effort
        )

    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(alpha), "--target", str(beta),
         "--concurrency", "2", "--out", str(tmp_path / "b")],
        run_fn=barrier_run, grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_PASS


# --- per-eval detail dir (OBS1) --------------------------------------------------


def detail_ballot(judge: int, session_id: str | None) -> JudgeBallot:
    return JudgeBallot(
        judge=judge,
        expectations=(ExpectationResult(text="e", passed=True, evidence="ev"),),
        session_id=session_id, cost_usd=0.05, input_tokens=1, output_tokens=2,
        cache_read_tokens=3, cache_creation_tokens=4, duration_ms=50,
    )


def ballot_grade_fn(*ballots: JudgeBallot) -> cli.GradeFn:
    """A passing single-expectation grade_fn whose result carries the given ballots."""

    def _grade(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        return GradingResult(
            expectations=(ExpectationResult(text="e", passed=True, evidence="ev"),),
            passed=1, failed=0, total=1, pass_rate=1.0, ballots=ballots,
        )

    return _grade


def detail_fixture() -> Fixture:
    return Fixture(
        target="t",
        evals=(Eval(id=1, name="n", prompt="p", output_files=("readme.md", "missing.md")),),
    )


def test_run_evals_writes_eval_detail_dir(tmp_path: Path) -> None:
    # Arrange — a workspace with one produced file; the other declared file is never produced
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "readme.md").write_text("# produced")
    detail_root = tmp_path / "run"

    # Act
    cli.run_evals(
        detail_fixture(), tmp_path, mode="all", judges=1, model="m", effort="e",
        run_fn=run_fn_returning("final message", workspace=workspace),
        grade_fn=ballot_grade_fn(detail_ballot(1, "sid-unlocatable")),
        detail_root=detail_root, projects_dir=tmp_path / "no-projects",
    )

    # Assert — output + produced file content + ballots are all on disk, audit-ready
    out = detail_root / "evals" / "t" / "n"
    assert (out / "output.md").read_text() == "final message"
    assert (out / "files" / "readme.md").read_text() == "# produced"
    assert not (out / "files" / "missing.md").exists()
    ballots = json.loads((out / "ballots.json").read_text())["ballots"]
    assert ballots[0]["judge"] == 1
    assert ballots[0]["session_id"] == "sid-unlocatable"
    assert ballots[0]["transcript"] is None
    assert ballots[0]["expectations"] == [{"text": "e", "passed": True, "evidence": "ev"}]
    assert ballots[0]["tokens"] == {"input": 1, "output": 2, "cache_read": 3, "cache_creation": 4}


def test_run_evals_copies_judge_transcripts_when_locatable(tmp_path: Path) -> None:
    # Arrange — the grader session's transcript exists under a project slug dir
    projects = tmp_path / "projects" / "slug"
    projects.mkdir(parents=True)
    (projects / "sid-1.jsonl").write_text('{"role": "judge"}')
    detail_root = tmp_path / "run"

    # Act
    cli.run_evals(
        detail_fixture(), tmp_path, mode="all", judges=1, model="m", effort="e",
        run_fn=run_fn_returning("final message", workspace=tmp_path),
        grade_fn=ballot_grade_fn(detail_ballot(1, "sid-1"), detail_ballot(2, None)),
        detail_root=detail_root, projects_dir=tmp_path / "projects",
    )

    # Assert — the locatable transcript is copied in; the session-less ballot records none
    out = detail_root / "evals" / "t" / "n"
    assert (out / "judge-1.jsonl").read_text() == '{"role": "judge"}'
    ballots = json.loads((out / "ballots.json").read_text())["ballots"]
    assert ballots[0]["transcript"] == "judge-1.jsonl"
    assert ballots[1]["transcript"] is None


def test_run_evals_persists_files_even_without_judging(tmp_path: Path) -> None:
    # Arrange — deterministic mode never grades, but the audit record must still be complete
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "readme.md").write_text("# produced")
    detail_root = tmp_path / "run"

    # Act
    cli.run_evals(
        detail_fixture(), tmp_path, mode="deterministic", judges=1, model="m", effort="e",
        run_fn=run_fn_returning("final message", workspace=workspace),
        grade_fn=ballot_grade_fn(),
        detail_root=detail_root, projects_dir=tmp_path / "no-projects",
    )

    # Assert
    out = detail_root / "evals" / "t" / "n"
    assert (out / "files" / "readme.md").read_text() == "# produced"
    assert json.loads((out / "ballots.json").read_text()) == {"ballots": []}


def test_main_writes_detail_dir_alongside_results(tmp_path: Path) -> None:
    # Arrange
    fixture_dir = write_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)
    evaluations = tmp_path / "evaluations"

    # Act
    cli.main(
        ["run", "all", "--target", str(fixture_dir), "--out", str(evaluations)],
        run_fn=run_fn_returning("issue in run_query"),
        grade_fn=grade_fn,
    )

    # Assert — the detail dir lands in the same stamped run dir as results.json
    results = next(evaluations.glob("*/results.json"))
    assert (results.parent / "evals" / "code-review" / "n" / "output.md").read_text() == (
        "issue in run_query"
    )


# --- regrade from the detail record (OBS1) ---------------------------------------


def recording_full_grade_fn() -> tuple[cli.GradeFn, list[tuple[str, ProducedFiles]]]:
    """A passing grade_fn recording (output_text, produced_files) — for regrade fidelity checks."""
    seen: list[tuple[str, ProducedFiles]] = []

    def _grade(
        ev: Eval, output_text: str, produced_files: ProducedFiles,
        judges: int, model: str, effort: str,
    ) -> GradingResult:
        seen.append((output_text, produced_files))

        return GradingResult.from_payload(
            {"expectations": [{"text": "e", "passed": True, "evidence": ""}]}
        )

    return _grade, seen


def write_output_files_fixture(tmp_path: Path) -> Path:
    return write_target(tmp_path, "code-review", json.dumps({
        "target": "code-review",
        "evals": [{"id": 1, "name": "alpha", "prompt": "Review.",
                   "output_files": ["readme.md", "missing.md"],
                   "expectations": [{"text": "flags it", "gate": "majority"}]}],
    }))


def write_detail_results(evaluations_root: Path) -> None:
    """Persist a new-format saved run: results.json + the eval's detail record."""
    out = evaluations_root / "20260101T000000Z"
    detail = out / "evals" / "code-review" / "alpha"
    (detail / "files").mkdir(parents=True)
    (detail / "output.md").write_text("DETAIL REVIEW")
    (detail / "files" / "readme.md").write_text("# judged content")
    (out / "results.json").write_text(json.dumps({
        "summary": {},
        "fixtures": [{"target": "code-review", "evals": [
            {"eval_id": 1, "eval_name": "alpha", "detail": "evals/code-review/alpha/"},
        ]}],
    }))


def test_main_regrade_reads_detail_record_and_produced_files(tmp_path: Path) -> None:
    # Arrange — a new-format saved run; the current eval declares two output files
    evaluations = tmp_path / "ev"
    write_detail_results(evaluations)
    fixture_dir = write_output_files_fixture(tmp_path)
    grade_fn, seen = recording_full_grade_fn()

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(evaluations)],
        grade_fn=grade_fn,
    )

    # Assert — the judge re-sees exactly the persisted record: final message + file content,
    # with the never-produced file as None (placeholder), not silently dropped
    assert code == cli.EXIT_PASS
    assert seen == [
        ("DETAIL REVIEW", (("readme.md", "# judged content"), ("missing.md", None))),
    ]


def test_main_regrade_falls_back_to_legacy_text_when_detail_record_is_gone(tmp_path: Path) -> None:
    # Arrange — the saved eval names a detail dir that no longer exists, but has inline text
    evaluations = tmp_path / "ev"
    out = evaluations / "20260101T000000Z"
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps({
        "summary": {},
        "fixtures": [{"target": "code-review", "evals": [
            {"eval_id": 1, "eval_name": "alpha", "detail": "evals/code-review/alpha/",
             "output_text": "LEGACY TEXT"},
        ]}],
    }))
    fixture_dir = write_output_files_fixture(tmp_path)
    grade_fn, seen = recording_full_grade_fn()

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(evaluations)],
        grade_fn=grade_fn,
    )

    # Assert — graded on the inline final message, with no reconstructed files
    assert code == cli.EXIT_PASS
    assert seen == [("LEGACY TEXT", ())]


def test_main_regrade_skips_eval_whose_record_is_unusable(tmp_path: Path) -> None:
    # Arrange — a saved eval with a dangling detail pointer and no inline text
    evaluations = tmp_path / "ev"
    out = evaluations / "20260101T000000Z"
    out.mkdir(parents=True)
    (out / "results.json").write_text(json.dumps({
        "summary": {},
        "fixtures": [{"target": "code-review", "evals": [
            {"eval_id": 1, "eval_name": "alpha", "detail": "evals/code-review/alpha/"},
        ]}],
    }))
    fixture_dir = write_output_files_fixture(tmp_path)
    grade_fn, seen = recording_full_grade_fn()

    # Act — nothing gradable → the regrade reports an error rather than a hollow pass
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(evaluations)],
        grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR
    assert seen == []


def test_main_all_then_regrade_roundtrips_the_detail_record(tmp_path: Path) -> None:
    # Arrange — a real `all` run persists the detail record the regrade must then re-read,
    # guarding the writer and the pointer against silently drifting apart
    fixture_dir = write_fixture(tmp_path)
    evaluations = tmp_path / "evaluations"
    grade_fn, _ = recording_grade_fn(1, 1)
    cli.main(
        ["run", "all", "--target", str(fixture_dir), "--out", str(evaluations)],
        run_fn=run_fn_returning("issue in run_query"),
        grade_fn=grade_fn,
    )
    regrade_fn, seen = recording_full_grade_fn()

    # Act
    code = cli.main(
        ["run", "regrade", "--target", str(fixture_dir), "--out", str(evaluations)],
        grade_fn=regrade_fn,
    )

    # Assert — regrade graded the persisted final message, not a legacy inline field
    assert code == cli.EXIT_PASS
    assert seen == [("issue in run_query", ())]
    saved = json.loads(next(evaluations.glob("*/results.json")).read_text())
    assert "output_text" not in saved["fixtures"][0]["evals"][0]


# --- --target file interface (Phase GR) --------------------------------------------


def test_evals_path_resolves_sibling_evals_json(tmp_path: Path) -> None:
    # Arrange — a target file with evals/evals.json beside it
    target = write_target(tmp_path, "s", _ONE_EVAL_JSON)

    # Act, Assert
    assert cli._evals_path(target) == target.parent / "evals" / "evals.json"


def test_evals_path_none_when_no_sibling_evals(tmp_path: Path) -> None:
    # Arrange — a bare file with no evals/ beside it
    target = tmp_path / "SKILL.md"
    target.write_text("# bare")

    # Act, Assert
    assert cli._evals_path(target) is None


def test_load_target_returns_none_when_no_evals(tmp_path: Path) -> None:
    # Arrange — a bare target with no evals beside it → None (callers emit one clean error)
    target = tmp_path / "SKILL.md"
    target.write_text("# bare")

    # Act, Assert
    assert cli._load_target(target, None) is None


def test_load_target_returns_fixture_and_seed_dir(tmp_path: Path) -> None:
    # Arrange
    target = write_target(tmp_path, "code-review", _ONE_EVAL_JSON)

    # Act
    loaded = cli._load_target(target, None)

    # Assert — the seed dir is the evals/ dir beside the target (where the runner copies seeds from)
    assert loaded is not None
    fixture, seed_dir = loaded
    assert fixture.target == "code-review"
    assert seed_dir == target.parent / "evals"


def test_main_target_resolves_sibling_evals(tmp_path: Path) -> None:
    # Arrange — the target file's content is fed in; its sibling evals/evals.json drives the run
    target = write_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(target), "--out", str(tmp_path / "out")],
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_PASS
    assert list((tmp_path / "out").glob("*/results.json"))


def test_main_results_colocate_beside_target(tmp_path: Path) -> None:
    # Arrange — single --target, no --out → results land in <dir>/eval-runs/ beside the target
    target = write_fixture(tmp_path)
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    cli.main(
        ["run", "deterministic", "--target", str(target)],
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert list((target.parent / "eval-runs").glob("*/results.json"))


def test_main_out_overrides_colocation(tmp_path: Path) -> None:
    # Arrange — --out wins; nothing is written beside the target
    target = write_fixture(tmp_path)
    out = tmp_path / "elsewhere"
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    cli.main(
        ["run", "deterministic", "--target", str(target), "--out", str(out)],
        run_fn=run_fn_returning("issue in run_query"), grade_fn=grade_fn,
    )

    # Assert
    assert list(out.glob("*/results.json"))
    assert not (target.parent / "eval-runs").exists()


def test_main_multi_target_runs_both_in_declaration_order(tmp_path: Path) -> None:
    # Arrange — two distinct targets named on one command via repeated --target
    def write_named(name: str) -> Path:
        check = {"type": "response_contains", "description": "d", "value": "ok"}
        return write_target(tmp_path, name, json.dumps({
            "target": name,
            "evals": [{"id": 1, "name": "n", "prompt": "p", "checks": [check]}],
        }))

    alpha, beta = write_named("alpha"), write_named("beta")
    out = tmp_path / "out"
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(alpha), "--target", str(beta), "--out", str(out)],
        run_fn=run_fn_returning("ok"), grade_fn=grade_fn,
    )

    # Assert — both ran; results regroup in the order the targets were named
    assert code == cli.EXIT_PASS
    bench = json.loads(next(out.glob("*/results.json")).read_text())
    assert [f["target"] for f in bench["fixtures"]] == ["alpha", "beta"]


def test_main_errors_when_target_has_no_evals(tmp_path: Path) -> None:
    # Arrange — a target file with no evals/ beside it
    target = tmp_path / "SKILL.md"
    target.write_text("# bare")
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(target), "--out", str(tmp_path / "out")],
        run_fn=run_fn_returning("x"), grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_errors_when_no_target_given(tmp_path: Path) -> None:
    # Act — run with no --target at all
    code = cli.main(
        ["run", "deterministic", "--out", str(tmp_path / "out")],
        run_fn=run_fn_returning("x"), grade_fn=recording_grade_fn(1, 1)[0],
    )

    # Assert
    assert code == cli.EXIT_ERROR


def test_main_errors_when_target_file_missing(tmp_path: Path) -> None:
    # Arrange — evals/ exists, but the named --target file itself does not (e.g. a typo)
    d = tmp_path / "s"
    (d / "evals").mkdir(parents=True)
    (d / "evals" / "evals.json").write_text(_ONE_EVAL_JSON)
    missing = d / "SKILL.md"  # never created
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act
    code = cli.main(
        ["run", "deterministic", "--target", str(missing), "--out", str(tmp_path / "out")],
        run_fn=run_fn_returning("x"), grade_fn=grade_fn,
    )

    # Assert — a clean config error, not a traceback from reading a missing file
    assert code == cli.EXIT_ERROR


def test_results_root_single_target_colocates() -> None:
    # Act, Assert — <dir>/eval-runs beside the target file
    assert cli._results_root(None, [Path("skills/code-review/SKILL.md")]) == (
        Path("skills/code-review") / "eval-runs"
    )


def test_results_root_multi_target_uses_cwd_eval_runs() -> None:
    # Act, Assert
    assert cli._results_root(None, [Path("a/SKILL.md"), Path("b/SKILL.md")]) == Path("eval-runs")


def test_results_root_out_flag_wins() -> None:
    # Act, Assert
    assert cli._results_root("custom", [Path("a/SKILL.md")]) == Path("custom")


def test_default_ground_truth_uses_convention_path() -> None:
    # Act
    paths = cli._default_ground_truth([Path("skills/code-review/SKILL.md")])

    # Assert — <dir>/ground_truth/<dir-name>.jsonl, beside the target file
    assert paths == [Path("skills/code-review") / "ground_truth" / "code-review.jsonl"]


def test_main_calibrate_defaults_ground_truth_from_target(tmp_path: Path) -> None:
    # Arrange — no --ground-truth; the convention file beside the target is used
    target = write_target(tmp_path, "code-review", _ONE_EVAL_JSON)
    gt_dir = target.parent / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "code-review.jsonl").write_text(
        '{"skill":"code-review","task":"t","expectation":"e1","output":"o","human_label":"pass"}\n'
    )
    captured: list[object] = []

    def capturing_calibrate(cases: object, **kwargs: object) -> calibration.CalibrationReport:
        captured.extend(cases)  # type: ignore[arg-type]

        return fake_calibrate(cases)

    # Act
    code = cli.main(
        ["run", "calibrate", "--target", str(target)], calibrate_fn=capturing_calibrate,
    )

    # Assert — the one convention-located case was loaded and graded
    assert code == cli.EXIT_PASS
    assert len(captured) == 1


def test_main_calibrate_errors_when_no_ground_truth_resolves(tmp_path: Path) -> None:
    # Arrange — no --ground-truth and no --target to default from

    # Act
    code = cli.main(["run", "calibrate"], calibrate_fn=fake_calibrate)

    # Assert — a clean error, not an empty-message slip-through
    assert code == cli.EXIT_ERROR


def test_main_regrade_errors_cleanly_when_target_has_no_evals(tmp_path: Path) -> None:
    # Arrange — a saved run exists, but the regrade target has no evals/ beside it
    evaluations = tmp_path / "ev"
    write_results(evaluations, [{"eval_id": 1, "eval_name": "a", "output_text": "SAVED"}])
    target = tmp_path / "SKILL.md"
    target.write_text("# bare")
    grade_fn, _ = recording_grade_fn(1, 1)

    # Act — run-mode and regrade share the same clean "no evals" error, not a traceback
    code = cli.main(
        ["run", "regrade", "--target", str(target), "--out", str(evaluations)], grade_fn=grade_fn,
    )

    # Assert
    assert code == cli.EXIT_ERROR


# --- init scaffold (Phase I) -------------------------------------------------------


def test_write_if_absent_writes_when_missing(tmp_path: Path) -> None:
    # Act, Assert
    path = tmp_path / "f.txt"
    assert cli._write_if_absent(path, "content") is True
    assert path.read_text() == "content"


def test_write_if_absent_skips_when_present(tmp_path: Path) -> None:
    # Arrange
    path = tmp_path / "f.txt"
    path.write_text("original")

    # Act, Assert — scaffolding never clobbers an authored file
    assert cli._write_if_absent(path, "new") is False
    assert path.read_text() == "original"


def test_init_scaffolds_target_assets(tmp_path: Path) -> None:
    # Arrange, Act — E7: deterministic scaffold beside the target file, no LLM/network
    target = tmp_path / "my-skill" / "SKILL.md"
    code = cli.main(["init", "--target", str(target)])

    # Assert — the four artifacts land in the target's directory, named by convention
    d = target.parent
    assert code == cli.EXIT_PASS
    assert (d / "evals" / "evals.json").is_file()
    assert (d / "evals" / "README.md").is_file()
    assert (d / "ground_truth" / "my-skill.jsonl").read_text() == ""
    assert (d / ".gitignore").read_text() == "eval-runs/\n"


def test_init_scaffold_evals_json_is_schema_valid(tmp_path: Path) -> None:
    # Arrange, Act — E8: the starter eval must load through the real schema
    target = tmp_path / "my-skill" / "SKILL.md"
    cli.main(["init", "--target", str(target)])

    # Assert
    fixture = Fixture.from_json((target.parent / "evals" / "evals.json").read_text())
    assert fixture.target == "my-skill"
    assert len(fixture.evals) == 1


def test_init_output_is_resolvable_by_run(tmp_path: Path) -> None:
    # Arrange, Act — the scaffold lands where the run resolver looks (sibling evals/)
    target = tmp_path / "my-skill" / "SKILL.md"
    cli.main(["init", "--target", str(target)])

    # Assert
    assert cli._evals_path(target) == target.parent / "evals" / "evals.json"


def test_init_is_idempotent_and_never_clobbers(tmp_path: Path) -> None:
    # Arrange — scaffold, then author the evals.json
    target = tmp_path / "my-skill" / "SKILL.md"
    cli.main(["init", "--target", str(target)])
    authored = target.parent / "evals" / "evals.json"
    authored.write_text('{"target": "my-skill", "evals": []}')

    # Act — re-init
    code = cli.main(["init", "--target", str(target)])

    # Assert — the authored content survives
    assert code == cli.EXIT_PASS
    assert authored.read_text() == '{"target": "my-skill", "evals": []}'


def test_init_requires_target() -> None:
    # Act, Assert — --target is mandatory for init (no cwd default)
    with pytest.raises(SystemExit):
        cli.main(["init"])
