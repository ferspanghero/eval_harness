"""Calibration tier: measure judge-vs-human agreement on frozen test cases.

A :class:`~eval_harness.schemas.CalibrationCase` freezes ``(task, expectation, output)`` plus the
human's correct verdict. :func:`calibrate` re-runs the **real** grader on each frozen output and
compares its pass/fail to the human label — a regression test for the grader prompt under test. No
agent runs; only the grader, on saved text. A failing agreement means the judge can't be trusted
(and prior green boards are suspect) until the grader prompt / expectation is fixed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from eval_harness import judge, llm
from eval_harness.schemas import CalibrationCase, Eval, Expectation

# Injected with a fake in tests; the default (:func:`judge_case`) calls the real grader.
GradeCaseFn = Callable[[CalibrationCase], bool]


@dataclass(frozen=True)
class CaseResult:
    """One calibration case: the judge's verdict and whether it matched the human's."""

    case: CalibrationCase
    judge_passed: bool
    agree: bool


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate judge-vs-human agreement over a calibration set."""

    results: tuple[CaseResult, ...]
    agreed: int
    total: int
    agreement_rate: float

    @classmethod
    def from_results(cls, results: tuple[CaseResult, ...]) -> CalibrationReport:
        agreed = sum(1 for r in results if r.agree)
        total = len(results)

        return cls(
            results=results,
            agreed=agreed,
            total=total,
            agreement_rate=(agreed / total) if total else 0.0,
        )

    @property
    def disagreements(self) -> tuple[CaseResult, ...]:
        return tuple(r for r in self.results if not r.agree)

    def by_skill(self) -> dict[str, tuple[int, int]]:
        """Agreement broken down per skill: ``skill -> (agreed, total)``.

        Surfaces *per-cell* bias (e.g. lenient on every security-audit case) that an aggregate
        rate hides, and shows which skills are under-covered.
        """
        breakdown: dict[str, tuple[int, int]] = {}
        for r in self.results:
            agreed, total = breakdown.get(r.case.skill, (0, 0))
            breakdown[r.case.skill] = (agreed + int(r.agree), total + 1)

        return breakdown


def judge_case(
    case: CalibrationCase,
    *,
    model: str,
    effort: str,
    judges: int = 1,
    call_json: judge.CallJson = llm.call_json,
) -> bool:
    """Run the **real** grader on one frozen case → its pass/fail for that single expectation.

    ``judges > 1`` repeats the grading and takes a majority vote, matching the eval path — so a
    borderline case the grader flips on run-to-run is measured against its majority verdict.
    """
    ev = Eval(
        id=0, name="calibration", prompt=case.task,
        expectations=(Expectation(text=case.expectation, gate="majority"),),
    )
    result = judge.grade(
        ev, case.output, model=model, effort=effort, judges=judges, call_json=call_json
    )

    return result.expectations[0].passed


def calibrate(
    cases: Sequence[CalibrationCase], *, grade_case: GradeCaseFn
) -> CalibrationReport:
    """Grade each frozen case with ``grade_case`` and tally agreement with the human labels."""
    results = []
    for case in cases:
        judge_passed = grade_case(case)
        results.append(
            CaseResult(
                case=case, judge_passed=judge_passed, agree=judge_passed == case.human_passed
            )
        )

    return CalibrationReport.from_results(tuple(results))
