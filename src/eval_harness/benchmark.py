"""Benchmark: aggregate per-eval outcomes and compare a working tree against a baseline.

An :class:`EvalOutcome` folds one eval's run metrics, deterministic checks, and judge grading into
a single pass-rate. A :class:`Benchmark` aggregates outcomes (pass-rate / cost / tokens / duration).
:func:`compare` flags a regression when the working tree scores worse than the baseline (the HEAD
worktree) on any eval — the harness's core "is the edited pipeline worse than before?" check.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from eval_harness.deterministic import DeterministicResult
from eval_harness.runner import RunResult
from eval_harness.schemas import GradingResult

# Vote-strength colors from worst to best. ``_worst_color`` picks the most severe across an eval's
# checks/expectations (and across a benchmark's evals) — one yellow drags the rollup to yellow.
_COLOR_RANK = {"red": 0, "yellow": 1, "green": 2}


def _worst_color(colors: Iterable[str]) -> str:
    return min(colors, key=lambda c: _COLOR_RANK[c], default="green")


def detail_rel_path(target: str, eval_name: str) -> str:
    """The run-dir-relative location of one eval's audit record — the ``detail`` pointer.

    The single source of truth for the layout: the detail writer joins it under the run dir and
    ``regrade`` resolves the persisted pointer against it, so the two can't silently drift.
    """
    return f"evals/{target}/{eval_name}/"


@dataclass(frozen=True)
class EvalOutcome:
    """One eval's combined result: deterministic checks + judge grading + run metrics + output.

    Retains the full :class:`DeterministicResult` and :class:`GradingResult` (not just counts) so
    ``to_dict`` can persist each check/expectation's verdict *and evidence* — what's needed to
    compare two versions by hand or, later, assertion-by-assertion.
    """

    eval_id: int
    eval_name: str
    output_text: str
    deterministic: DeterministicResult
    grading: GradingResult
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    duration_ms: int | None

    @classmethod
    def from_parts(
        cls,
        run: RunResult,
        eval_name: str,
        deterministic: DeterministicResult,
        grading: GradingResult,
    ) -> EvalOutcome:
        return cls(
            eval_id=run.eval_id,
            eval_name=eval_name,
            output_text=run.output_text,
            deterministic=deterministic,
            grading=grading,
            cost_usd=run.cost_usd,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            cache_read_tokens=run.cache_read_tokens,
            cache_creation_tokens=run.cache_creation_tokens,
            duration_ms=run.duration_ms,
        )

    @property
    def deterministic_passed(self) -> int:
        return self.deterministic.passed

    @property
    def deterministic_total(self) -> int:
        return self.deterministic.total

    @property
    def judge_passed(self) -> int:
        return self.grading.passed

    @property
    def judge_total(self) -> int:
        return self.grading.total

    @property
    def total_checks(self) -> int:
        return self.deterministic_total + self.judge_total

    @property
    def total_passed(self) -> int:
        return self.deterministic_passed + self.judge_passed

    @property
    def pass_rate(self) -> float:
        return (self.total_passed / self.total_checks) if self.total_checks else 0.0

    @property
    def all_passed(self) -> bool:
        return self.total_checks > 0 and self.total_passed == self.total_checks

    @property
    def color(self) -> str:
        """Worst vote-strength color across this eval — a failed objective check counts as red."""
        det = ["green" if c.passed else "red" for c in self.deterministic.checks]
        judge = [e.color for e in self.grading.expectations]

        return _worst_color(det + judge)

    @property
    def judge_cost_usd(self) -> float:
        """Total judge spend across this eval's ballots (an errored ballot contributes 0)."""
        return sum(b.cost_usd or 0.0 for b in self.grading.ballots)

    @property
    def total_cost_usd(self) -> float:
        """Run + judge spend — the eval's true cost."""
        return (self.cost_usd or 0.0) + self.judge_cost_usd

    @property
    def judge_total_tokens(self) -> int:
        """All token components across this eval's ballots (None treated as 0)."""
        return sum(
            (b.input_tokens or 0)
            + (b.output_tokens or 0)
            + (b.cache_read_tokens or 0)
            + (b.cache_creation_tokens or 0)
            for b in self.grading.ballots
        )

    @property
    def total_tokens(self) -> int:
        """Sum of all run token components plus judge tokens (None treated as 0)."""
        parts = (
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_creation_tokens,
        )

        return sum(t for t in parts if t is not None) + self.judge_total_tokens

    def to_dict(self) -> dict[str, Any]:
        """Serialise the outcome — counts, metrics, and per-check/expectation evidence + output."""
        return {
            "eval_id": self.eval_id,
            "eval_name": self.eval_name,
            "pass_rate": round(self.pass_rate, 4),
            "all_passed": self.all_passed,
            "deterministic": {
                "passed": self.deterministic_passed,
                "total": self.deterministic_total,
                "checks": [
                    {"description": c.description, "passed": c.passed, "evidence": c.evidence}
                    for c in self.deterministic.checks
                ],
            },
            "judge": {
                "passed": self.judge_passed,
                "total": self.judge_total,
                "expectations": [
                    {
                        "text": e.text, "passed": e.passed, "evidence": e.evidence,
                        "pass_votes": e.pass_votes, "total_votes": e.total_votes,
                        "gate": e.gate, "color": e.color,
                    }
                    for e in self.grading.expectations
                ],
            },
            "cost_usd": {
                "run": round(self.cost_usd, 6) if self.cost_usd is not None else None,
                "judges": round(self.judge_cost_usd, 6),
                "total": round(self.total_cost_usd, 6),
            },
            "duration_ms": self.duration_ms,
            "tokens": {
                "total": self.total_tokens,
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cache_read": self.cache_read_tokens,
                "cache_creation": self.cache_creation_tokens,
                "judges": self.judge_total_tokens,
            },
        }


def _sum_optional(values: list[int | None]) -> int:
    return sum(v for v in values if v is not None)


@dataclass(frozen=True)
class Benchmark:
    """Aggregate of all eval outcomes for one target under test."""

    target: str
    outcomes: tuple[EvalOutcome, ...]
    num_evals: int
    evals_passed: int
    mean_pass_rate: float
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_read_tokens: int
    total_cache_creation_tokens: int
    total_judge_tokens: int
    total_duration_ms: int

    @property
    def all_passed(self) -> bool:
        """Whether every eval in the fixture passed all its checks."""
        return self.evals_passed == self.num_evals

    @property
    def color(self) -> str:
        """The worst vote-strength color across this fixture's evals."""
        return _worst_color(o.color for o in self.outcomes)

    @classmethod
    def from_outcomes(cls, target: str, outcomes: tuple[EvalOutcome, ...]) -> Benchmark:
        num = len(outcomes)
        mean_pass_rate = (sum(o.pass_rate for o in outcomes) / num) if num else 0.0

        return cls(
            target=target,
            outcomes=outcomes,
            num_evals=num,
            evals_passed=sum(1 for o in outcomes if o.all_passed),
            mean_pass_rate=mean_pass_rate,
            total_cost_usd=sum(o.total_cost_usd for o in outcomes),
            total_input_tokens=_sum_optional([o.input_tokens for o in outcomes]),
            total_output_tokens=_sum_optional([o.output_tokens for o in outcomes]),
            total_cache_read_tokens=_sum_optional([o.cache_read_tokens for o in outcomes]),
            total_cache_creation_tokens=_sum_optional([o.cache_creation_tokens for o in outcomes]),
            total_judge_tokens=sum(o.judge_total_tokens for o in outcomes),
            total_duration_ms=_sum_optional([o.duration_ms for o in outcomes]),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the benchmark to the persisted JSON shape (combined summary + per-eval)."""
        return {
            "target": self.target,
            "summary": {
                "num_evals": self.num_evals,
                "evals_passed": self.evals_passed,
                "mean_pass_rate": round(self.mean_pass_rate, 4),
                "total_cost_usd": round(self.total_cost_usd, 6),
                "total_duration_ms": self.total_duration_ms,
                "tokens": {
                    "total": (
                        self.total_input_tokens
                        + self.total_output_tokens
                        + self.total_cache_read_tokens
                        + self.total_cache_creation_tokens
                        + self.total_judge_tokens
                    ),
                    "input": self.total_input_tokens,
                    "output": self.total_output_tokens,
                    "cache_read": self.total_cache_read_tokens,
                    "cache_creation": self.total_cache_creation_tokens,
                    "judges": self.total_judge_tokens,
                },
            },
            "evals": [
                o.to_dict() | {"detail": detail_rel_path(self.target, o.eval_name)}
                for o in self.outcomes
            ],
        }


@dataclass(frozen=True)
class Comparison:
    """The verdict of comparing a working-tree benchmark against a baseline."""

    regressed: bool
    regressed_eval_ids: tuple[int, ...]
    mean_pass_rate_delta: float


def compare(working: Benchmark, baseline: Benchmark, *, epsilon: float = 1e-9) -> Comparison:
    """Flag a regression where the working tree scores worse than the baseline on any eval."""
    baseline_by_id = {o.eval_id: o for o in baseline.outcomes}
    regressed_ids = tuple(
        o.eval_id
        for o in working.outcomes
        if o.eval_id in baseline_by_id
        and o.pass_rate < baseline_by_id[o.eval_id].pass_rate - epsilon
    )

    return Comparison(
        regressed=bool(regressed_ids),
        regressed_eval_ids=regressed_ids,
        mean_pass_rate_delta=working.mean_pass_rate - baseline.mean_pass_rate,
    )
