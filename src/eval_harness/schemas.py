"""Fixture and result schemas for the eval harness.

Adapted from skill-creator's ``evals.json`` / ``grading.json`` shapes, specialised for this
harness's two-tier model: each eval carries **checks** (objective
:class:`~eval_harness.deterministic.base.Check` objects, run by ``deterministic``) and
**expectations** (rubric :class:`Expectation` objects graded by the LLM ``judge``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from eval_harness.deterministic import Check, parse_check

# Produced files fed to the judge as (workspace-relative path, content) pairs — mirrors
# ``LLMRequest.extra_args``' pair-sequence shape. ``content`` is ``None`` when the skill never
# produced the declared file (the judge renders a placeholder so content expectations fail).
ProducedFiles = tuple[tuple[str, str | None], ...]

GATES = ("majority", "unanimous")


@dataclass(frozen=True)
class Expectation:
    """One rubric expectation the judge grades, plus the vote *gate* that decides its pass/fail.

    ``gate`` is how the per-expectation majority vote is read: ``"majority"`` passes on a majority
    of the judges; ``"unanimous"`` requires every judge — for high-stakes expectations where a
    flaky 2/3 should fail rather than squeak by. The gate is **explicit, never defaulted**: a
    fixture writes ``{"text": ..., "gate": ...}`` for every expectation; a bare string or a missing
    gate is malformed config that fails to load (see :meth:`from_dict`).
    """

    text: str
    gate: str

    def __post_init__(self) -> None:
        if self.gate not in GATES:
            raise ValueError(f"gate must be one of {GATES}, got {self.gate!r}")

    @classmethod
    def from_dict(cls, data: Any) -> Expectation:
        if not (isinstance(data, dict) and "text" in data and "gate" in data):
            raise ValueError(
                f"expectation must be an object with explicit 'text' and 'gate', got {data!r}"
            )

        return cls(text=data["text"], gate=data["gate"])

    def passes(self, pass_votes: int, total_votes: int) -> bool:
        """Whether ``pass_votes`` of ``total_votes`` judges clears this expectation's gate."""
        if self.gate == "unanimous":
            return pass_votes == total_votes

        return pass_votes * 2 > total_votes


@dataclass(frozen=True)
class Eval:
    """A single fixture case: a task to run plus how to grade what it produces."""

    id: int
    name: str
    prompt: str
    files: tuple[str, ...] = ()
    checks: tuple[Check, ...] = ()
    expectations: tuple[Expectation, ...] = ()
    # Produced files (workspace-relative paths) whose *content* the judge should grade — for
    # file-producing skills, where the agent's final message is only a summary. Empty for text
    # skills, whose output is the final message itself.
    output_files: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Eval:
        return cls(
            id=data["id"],
            name=data["name"],
            prompt=data["prompt"],
            files=tuple(data.get("files", ())),
            checks=tuple(parse_check(c) for c in data.get("checks", ())),
            expectations=tuple(Expectation.from_dict(e) for e in data.get("expectations", ())),
            output_files=tuple(data.get("output_files", ())),
        )


@dataclass(frozen=True)
class Fixture:
    """A fixture file (``evals.json``): the target under test and its evals.

    ``target`` is the result label (shown in results; conventionally the target's directory name).
    ``system_prompt`` is an optional directive **appended after the target file's content** in the
    run's system prompt — e.g. the autonomous instruction a full-pipeline target needs to run
    end-to-end without stopping at approval gates; most fixtures omit it.
    """

    target: str
    evals: tuple[Eval, ...]
    system_prompt: str | None = None

    @classmethod
    def from_json(cls, text: str) -> Fixture:
        data = json.loads(text)

        return cls(
            target=data["target"],
            evals=tuple(Eval.from_dict(e) for e in data["evals"]),
            system_prompt=data.get("system_prompt"),
        )


@dataclass(frozen=True)
class CalibrationCase:
    """A frozen judge test case: an artifact + question + the human's correct verdict.

    Calibration re-runs the *real* grader on ``output`` for ``expectation`` and compares its
    pass/fail to ``human_label`` — so the artifact is baked into the case (not a run pointer),
    and the only variable across calibration runs is the grader prompt under test.
    """

    skill: str
    task: str
    expectation: str
    output: str
    human_label: str
    notes: str = ""

    def __post_init__(self) -> None:
        if self.human_label not in ("pass", "fail"):
            raise ValueError(f"human_label must be 'pass' or 'fail', got {self.human_label!r}")

    @property
    def human_passed(self) -> bool:
        return self.human_label == "pass"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationCase:
        return cls(
            skill=data["skill"],
            task=data["task"],
            expectation=data["expectation"],
            output=data["output"],
            human_label=data["human_label"],
            notes=data.get("notes", ""),
        )


@dataclass(frozen=True)
class ExpectationResult:
    """The judge's verdict on one rubric expectation.

    ``passed`` is the gate-applied verdict. ``pass_votes`` / ``total_votes`` carry the raw vote
    tally once aggregated across judges (both ``None`` for a single unaggregated ballot), and
    ``gate`` records which gate decided it. ``color`` reads the *vote strength* — green (unanimous)
    / yellow (a majority, not all) / red (below a majority) — independent of pass/fail, so a
    ``unanimous``-gated 2/3 is a yellow *fail*.
    """

    text: str
    passed: bool
    evidence: str
    pass_votes: int | None = None
    total_votes: int | None = None
    gate: str = "majority"

    @property
    def color(self) -> str:
        votes, total = self.pass_votes, self.total_votes
        if votes is None or total is None or total == 0:
            return "green" if self.passed else "red"
        if votes == total:
            return "green"
        if votes * 2 > total:
            return "yellow"

        return "red"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExpectationResult:
        return cls(
            text=data["text"],
            passed=bool(data["passed"]),
            evidence=data.get("evidence", ""),
        )


@dataclass(frozen=True)
class JudgeBallot:
    """One judge's complete ballot: its own votes plus the grader call's audit metadata.

    ``expectations`` are this judge's raw verdicts (pre-aggregation, dissent included).
    ``session_id`` locates the call's transcript; the spend fields carry what the call cost.
    ``error`` is set when the grader call itself failed — its votes are then the synthesized
    all-fail ballot and the metadata fields are ``None`` (nothing real to record).
    """

    judge: int
    expectations: tuple[ExpectationResult, ...]
    session_id: str | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    duration_ms: int | None
    error: str | None = None


@dataclass(frozen=True)
class GradingResult:
    """The judge's grading of an eval's expectations (skill-creator ``grading.json`` shape).

    ``expectations`` are the aggregated, gate-applied verdicts; ``ballots`` retain every judge's
    raw ballot (votes + call audit metadata) so a verdict can be audited after the fact.
    """

    expectations: tuple[ExpectationResult, ...]
    passed: int
    failed: int
    total: int
    pass_rate: float
    ballots: tuple[JudgeBallot, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> GradingResult:
        # Counts are always recomputed from the expectations — the model's own ``summary`` (if any)
        # is redundant and untrusted; deriving from the verdicts keeps the result self-consistent.
        expectations = tuple(
            ExpectationResult.from_dict(e) for e in payload.get("expectations", ())
        )
        total = len(expectations)
        passed = sum(1 for e in expectations if e.passed)

        return cls(
            expectations=expectations,
            passed=passed,
            failed=total - passed,
            total=total,
            pass_rate=(passed / total) if total else 0.0,
        )
