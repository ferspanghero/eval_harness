"""Tests for benchmark aggregation and baseline (working-vs-HEAD) comparison."""

from __future__ import annotations

from dataclasses import replace

import pytest

from eval_harness import benchmark
from eval_harness.deterministic import CheckResult, DeterministicResult
from eval_harness.runner import RunResult
from eval_harness.schemas import ExpectationResult, GradingResult, JudgeBallot


def run_result(eval_id: int, cost: float, tokens: int, output_text: str = "") -> RunResult:
    return RunResult(
        eval_id=eval_id, target="code-review", workspace=__import__("pathlib").Path("."),
        output_text=output_text, session_id="s",
        cost_usd=cost, input_tokens=tokens, output_tokens=tokens,
        cache_read_tokens=tokens, cache_creation_tokens=tokens, duration_ms=1000,
    )


def det(passed: int, total: int) -> DeterministicResult:
    checks = tuple(
        CheckResult(description=f"c{i}", passed=i < passed, evidence="") for i in range(total)
    )

    return DeterministicResult.from_checks(checks)


def grading(passed: int, total: int) -> GradingResult:
    exps = tuple(
        ExpectationResult(text=f"e{i}", passed=i < passed, evidence="") for i in range(total)
    )

    return GradingResult.from_payload({"expectations": [
        {"text": e.text, "passed": e.passed, "evidence": ""} for e in exps
    ]})


def grading_with(expectations: tuple[ExpectationResult, ...]) -> GradingResult:
    """A GradingResult built directly from aggregated expectations (carrying vote tallies)."""
    passed = sum(1 for e in expectations if e.passed)
    total = len(expectations)

    return GradingResult(
        expectations=expectations, passed=passed, failed=total - passed,
        total=total, pass_rate=(passed / total) if total else 0.0,
    )


# --- EvalOutcome --------------------------------------------------------------


def test_outcome_combines_deterministic_and_judge() -> None:
    # Act
    outcome = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.2, 100), "planted", det(2, 2), grading(1, 2)
    )

    # Assert — 2/2 deterministic + 1/2 judge = 3/4
    assert outcome.pass_rate == pytest.approx(0.75)
    assert outcome.all_passed is False


def test_outcome_all_passed_when_every_check_and_expectation_passes() -> None:
    # Act
    outcome = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.2, 100), "planted", det(2, 2), grading(2, 2)
    )

    # Assert
    assert outcome.all_passed is True
    assert outcome.pass_rate == pytest.approx(1.0)


# --- color rollup (vote strength) ---------------------------------------------


def test_outcome_color_green_when_all_pass_unanimously() -> None:
    # Arrange — every deterministic check passes and the one expectation is a 3/3
    grading = grading_with((ExpectationResult("e", True, "", pass_votes=3, total_votes=3),))

    # Act
    outcome = benchmark.EvalOutcome.from_parts(run_result(1, 0.1, 10), "n", det(1, 1), grading)

    # Assert
    assert outcome.color == "green"


def test_outcome_color_yellow_when_an_expectation_is_only_a_majority() -> None:
    # Arrange — a 2/3 expectation drags the whole eval's color to yellow even though it passed
    grading = grading_with(
        (
            ExpectationResult("a", True, "", pass_votes=3, total_votes=3),
            ExpectationResult("b", True, "", pass_votes=2, total_votes=3),
        )
    )

    # Act
    outcome = benchmark.EvalOutcome.from_parts(run_result(1, 0.1, 10), "n", det(1, 1), grading)

    # Assert
    assert outcome.color == "yellow"


def test_outcome_color_red_when_a_deterministic_check_fails() -> None:
    # Arrange — a failed objective check is red regardless of the judge's colors
    grading = grading_with((ExpectationResult("a", True, "", pass_votes=3, total_votes=3),))

    # Act
    outcome = benchmark.EvalOutcome.from_parts(run_result(1, 0.1, 10), "n", det(0, 1), grading)

    # Assert
    assert outcome.color == "red"


def test_benchmark_all_passed_true_when_every_eval_passes() -> None:
    # Arrange
    outcomes = (
        benchmark.EvalOutcome.from_parts(run_result(1, 0.1, 10), "a", det(1, 1), grading(1, 1)),
    )
    bench = benchmark.Benchmark.from_outcomes("t", outcomes)

    # Act, Assert
    assert bench.all_passed is True


def test_benchmark_all_passed_false_when_an_eval_fails() -> None:
    # Arrange — one passing eval, one with a failed deterministic check
    outcomes = (
        benchmark.EvalOutcome.from_parts(run_result(1, 0.1, 10), "a", det(1, 1), grading(1, 1)),
        benchmark.EvalOutcome.from_parts(run_result(2, 0.1, 10), "b", det(0, 1), grading(1, 1)),
    )
    bench = benchmark.Benchmark.from_outcomes("t", outcomes)

    # Act, Assert
    assert bench.all_passed is False


def test_benchmark_color_is_the_worst_eval_color() -> None:
    # Arrange — one green eval and one yellow eval
    green = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.1, 10), "a", det(1, 1),
        grading_with((ExpectationResult("x", True, "", pass_votes=3, total_votes=3),)),
    )
    yellow = benchmark.EvalOutcome.from_parts(
        run_result(2, 0.1, 10), "b", det(1, 1),
        grading_with((ExpectationResult("y", True, "", pass_votes=2, total_votes=3),)),
    )

    # Act
    bench = benchmark.Benchmark.from_outcomes("code-review", (green, yellow))

    # Assert
    assert bench.color == "yellow"


# --- Benchmark aggregate ------------------------------------------------------


def test_benchmark_aggregates_cost_and_pass_rate() -> None:
    # Arrange
    outcomes = (
        benchmark.EvalOutcome.from_parts(run_result(1, 0.2, 100), "a", det(2, 2), grading(2, 2)),
        benchmark.EvalOutcome.from_parts(run_result(2, 0.3, 200), "b", det(1, 2), grading(0, 2)),
    )

    # Act
    bench = benchmark.Benchmark.from_outcomes("code-review", outcomes)

    # Assert
    assert bench.num_evals == 2
    assert bench.evals_passed == 1
    assert bench.total_cost_usd == pytest.approx(0.5)
    assert bench.mean_pass_rate == pytest.approx((1.0 + 0.25) / 2)


def test_outcome_to_dict_includes_evidence_without_output_text() -> None:
    # Arrange
    run = run_result(1, 0.2, 100, output_text="Critical: SQL injection in run_query")
    deterministic = DeterministicResult.from_checks(
        (CheckResult(description="names fn", passed=True, evidence="found 'run_query'"),)
    )
    grading = GradingResult.from_payload(
        {"expectations": [{"text": "flags it", "passed": True, "evidence": "line 4"}]}
    )

    # Act
    data = benchmark.EvalOutcome.from_parts(run, "planted", deterministic, grading).to_dict()

    # Assert — the final message lives in the run's detail dir, not the summary document
    assert "output_text" not in data
    assert data["deterministic"]["checks"][0] == {
        "description": "names fn", "passed": True, "evidence": "found 'run_query'",
    }
    assert data["judge"]["expectations"][0] == {
        "text": "flags it", "passed": True, "evidence": "line 4",
        "pass_votes": None, "total_votes": None, "gate": "majority", "color": "green",
    }


def test_outcome_to_dict_breaks_down_tokens_with_total() -> None:
    # Arrange — run_result sets input=output=cache_read=cache_creation = 100
    outcome = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.2, 100), "a", det(1, 1), grading(1, 1)
    )

    # Act
    tokens = outcome.to_dict()["tokens"]

    # Assert — total is the sum of every component (no ballots → no judge tokens)
    assert tokens == {
        "total": 400, "input": 100, "output": 100, "cache_read": 100, "cache_creation": 100,
        "judges": 0,
    }


def test_benchmark_summary_token_total_sums_components() -> None:
    # Arrange
    outcomes = (
        benchmark.EvalOutcome.from_parts(run_result(1, 0.2, 100), "a", det(1, 1), grading(1, 1)),
        benchmark.EvalOutcome.from_parts(run_result(2, 0.3, 50), "b", det(1, 1), grading(1, 1)),
    )

    # Act
    summary = benchmark.Benchmark.from_outcomes("code-review", outcomes).to_dict()["summary"]
    tokens = summary["tokens"]

    # Assert — (100*4) + (50*4) = 600
    assert tokens["total"] == 600
    assert tokens["input"] == 150


def test_benchmark_to_dict_has_expected_keys() -> None:
    # Arrange
    outcomes = (
        benchmark.EvalOutcome.from_parts(run_result(1, 0.2, 100), "a", det(2, 2), grading(2, 2)),
    )
    bench = benchmark.Benchmark.from_outcomes("code-review", outcomes)

    # Act
    data = bench.to_dict()

    # Assert
    assert data["target"] == "code-review"
    assert data["summary"]["num_evals"] == 1
    assert data["evals"][0]["eval_id"] == 1


# --- baseline comparison ------------------------------------------------------


def bench_with(pass_rates: dict[int, tuple[int, int]]) -> benchmark.Benchmark:
    """Build a benchmark where eval_id -> (judge_passed, judge_total), deterministic all-pass."""
    outcomes = tuple(
        benchmark.EvalOutcome.from_parts(
            run_result(eid, 0.1, 10), f"e{eid}", det(1, 1), grading(p, t)
        )
        for eid, (p, t) in pass_rates.items()
    )

    return benchmark.Benchmark.from_outcomes("code-review", outcomes)


def test_compare_flags_regression_when_working_worse() -> None:
    # Arrange
    working = bench_with({1: (0, 2)})
    baseline = bench_with({1: (2, 2)})

    # Act
    comparison = benchmark.compare(working, baseline)

    # Assert
    assert comparison.regressed is True
    assert 1 in comparison.regressed_eval_ids


def test_compare_no_regression_when_working_equal_or_better() -> None:
    # Arrange
    working = bench_with({1: (2, 2)})
    baseline = bench_with({1: (1, 2)})

    # Act
    comparison = benchmark.compare(working, baseline)

    # Assert
    assert comparison.regressed is False
    assert comparison.regressed_eval_ids == ()


# --- judge spend + detail pointer (OBS1) ----------------------------------------


def ballot(judge: int, *, cost: float | None = 0.05, tokens: int | None = 10) -> JudgeBallot:
    return JudgeBallot(
        judge=judge, expectations=(), session_id=f"sid-{judge}",
        cost_usd=cost, input_tokens=tokens, output_tokens=tokens,
        cache_read_tokens=tokens, cache_creation_tokens=tokens, duration_ms=50,
    )


def with_ballots(grading_result: GradingResult, *ballots: JudgeBallot) -> GradingResult:
    return replace(grading_result, ballots=ballots)


def test_outcome_judge_spend_rolls_up_from_ballots() -> None:
    # Arrange — run cost 0.2 / 400 run tokens; 3 ballots of 0.05 / 40 tokens each
    outcome = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.2, 100), "a", det(1, 1),
        with_ballots(grading(1, 1), ballot(1), ballot(2), ballot(3)),
    )

    # Act, Assert
    assert outcome.judge_cost_usd == pytest.approx(0.15)
    assert outcome.total_cost_usd == pytest.approx(0.35)
    assert outcome.judge_total_tokens == 120
    assert outcome.total_tokens == 520


def test_outcome_judge_spend_treats_errored_ballot_metrics_as_zero() -> None:
    # Arrange — an errored ballot carries no metrics
    outcome = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.2, 100), "a", det(1, 1),
        with_ballots(grading(1, 1), ballot(1), ballot(2, cost=None, tokens=None)),
    )

    # Act, Assert
    assert outcome.judge_cost_usd == pytest.approx(0.05)
    assert outcome.judge_total_tokens == 40


def test_outcome_to_dict_splits_cost_and_counts_judge_tokens() -> None:
    # Arrange
    outcome = benchmark.EvalOutcome.from_parts(
        run_result(1, 0.2, 100), "a", det(1, 1),
        with_ballots(grading(1, 1), ballot(1), ballot(2), ballot(3)),
    )

    # Act
    data = outcome.to_dict()

    # Assert — judge spend is visible, no longer silently dropped
    assert data["cost_usd"] == {"run": 0.2, "judges": 0.15, "total": 0.35}
    assert data["tokens"]["judges"] == 120
    assert data["tokens"]["total"] == 520


def test_benchmark_totals_include_judge_spend() -> None:
    # Arrange
    outcomes = (
        benchmark.EvalOutcome.from_parts(
            run_result(1, 0.2, 100), "a", det(1, 1),
            with_ballots(grading(1, 1), ballot(1), ballot(2)),
        ),
        benchmark.EvalOutcome.from_parts(run_result(2, 0.3, 50), "b", det(1, 1), grading(1, 1)),
    )

    # Act
    bench = benchmark.Benchmark.from_outcomes("code-review", outcomes)
    summary = bench.to_dict()["summary"]

    # Assert — 0.2 + 0.3 run + 2×0.05 judges; 600 run + 80 judge tokens
    assert bench.total_cost_usd == pytest.approx(0.6)
    assert summary["tokens"]["judges"] == 80
    assert summary["tokens"]["total"] == 680


def test_benchmark_to_dict_points_each_eval_at_its_detail_dir() -> None:
    # Arrange
    outcomes = (
        benchmark.EvalOutcome.from_parts(
            run_result(1, 0.2, 100), "planted", det(1, 1), grading(1, 1)
        ),
    )

    # Act
    data = benchmark.Benchmark.from_outcomes("code-review", outcomes).to_dict()

    # Assert
    assert data["evals"][0]["detail"] == "evals/code-review/planted/"


def test_outcome_to_dict_keeps_unknown_run_cost_as_none() -> None:
    # Arrange — a run whose envelope carried no cost
    run = replace(run_result(1, 0.2, 100), cost_usd=None)
    outcome = benchmark.EvalOutcome.from_parts(run, "a", det(1, 1), grading(1, 1))

    # Act
    cost = outcome.to_dict()["cost_usd"]

    # Assert — unknown stays None (not fabricated as 0); totals treat it as 0
    assert cost == {"run": None, "judges": 0.0, "total": 0.0}
