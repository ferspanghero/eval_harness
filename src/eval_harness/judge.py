"""Judge tier: an LLM grader over artifact content, via the ``llm`` seam.

The grader receives the eval's task, its expectations, and the produced output text inline, and
returns per-expectation pass/fail with evidence (skill-creator's grader, adapted to a single-shot
``call_json``). Model and effort are passed in by the caller (the CLI supplies the defaults); the
prompt is versioned. ``judges > 1`` repeats only the grading and takes a per-expectation majority
vote — the agent run happens once upstream.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from importlib.resources import files
from typing import Any

from eval_harness import llm
from eval_harness.schemas import (
    Eval,
    Expectation,
    ExpectationResult,
    GradingResult,
    JudgeBallot,
    ProducedFiles,
)

logger = logging.getLogger(__name__)

GRADER_PROMPT_VERSION = "grader_v1"

# Stand-in shown to the grader for a declared output file the skill never produced, so a content
# expectation fails on real absence instead of crashing the grader on a missing read.
FILE_NOT_PRODUCED = "<file not produced>"

CallJson = Callable[..., "tuple[Any, llm.LLMResponse]"]


def _grader_instructions() -> str:
    return files("eval_harness.prompts").joinpath(f"{GRADER_PROMPT_VERSION}.md").read_text()


def _format_output(output_text: str, produced_files: ProducedFiles) -> str:
    """The body of the OUTPUT section: the final message, then each produced file's content.

    With no produced files this is just the final message — byte-identical to grading a text skill,
    so existing text fixtures don't shift. With files, each is appended under a ``### File: <path>``
    header so the grader verifies file *content*, not merely the agent's summary; a file the skill
    never produced (``content is None``) shows a placeholder.
    """
    if not produced_files:
        return output_text

    sections = [f"### Final message\n{output_text}"]
    for path, content in produced_files:
        body = content if content is not None else FILE_NOT_PRODUCED
        sections.append(f"### File: {path}\n{body}")

    return "\n\n".join(sections)


def _build_grader_prompt(
    ev: Eval, output_text: str, produced_files: ProducedFiles = ()
) -> str:
    """Assemble the single-shot grader prompt: instructions + task + expectations + output.

    Only each expectation's *text* reaches the grader — the gate is a harness-side decision applied
    when the votes are aggregated, not something the judge needs to (or should) see.
    """
    numbered = "\n".join(f"{i}. {e.text}" for i, e in enumerate(ev.expectations, start=1))

    return (
        f"{_grader_instructions()}\n\n"
        f"## TASK\n{ev.prompt}\n\n"
        f"## EXPECTATIONS\n{numbered}\n\n"
        f"## OUTPUT\n{_format_output(output_text, produced_files)}\n"
    )


def _grading_validator(expected_count: int) -> Callable[[Any], bool]:
    """A grading payload must carry an ``expectations`` list of exactly ``expected_count`` items.

    Enforcing the count makes every accepted run align by position, so the majority vote can't
    crash on a short/long run — a mismatched run is rejected and retried instead.
    """

    def _valid(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and isinstance(value.get("expectations"), list)
            and len(value["expectations"]) == expected_count
        )

    return _valid


def _errored_vote(expectations: tuple[Expectation, ...], exc: llm.LLMError) -> GradingResult:
    """One judge's ballot when its grader call failed: every expectation voted fail.

    An internal grader error counts as a single fail vote (not a raised exception), so one
    transient judge error can't sink a vote the other judges would carry, and a total outage
    simply fails the item rather than aborting the sweep/calibration. The ``judge error`` evidence
    here plus the logged warning in :func:`_grade_once` distinguish an internal failure from a
    legitimate judge-assessed fail.
    """
    results = tuple(
        ExpectationResult(text=e.text, passed=False, evidence=f"judge error: {exc}")
        for e in expectations
    )
    total = len(results)

    return GradingResult(
        expectations=results, passed=0, failed=total, total=total, pass_rate=0.0
    )


def _grade_once(
    request: llm.LLMRequest,
    validate: Callable[[Any], bool],
    expectations: tuple[Expectation, ...],
    call_json: CallJson,
) -> tuple[GradingResult, llm.LLMResponse | None, str | None]:
    """One judge's grading plus the call's response (``None`` with the error when it failed).

    An LLM error becomes a logged fail vote, not a raised exception.
    """
    try:
        payload, response = call_json(request, validate=validate)

        return GradingResult.from_payload(payload), response, None
    except llm.LLMError as exc:
        logger.warning("judge call errored (counted as a fail vote): %s", exc)

        return _errored_vote(expectations, exc), None, str(exc)


def _ballot(judge_index: int, result: GradingResult, response: llm.LLMResponse | None,
            error: str | None) -> JudgeBallot:
    """Fold one judge's votes and its call's audit metadata into a ballot."""
    return JudgeBallot(
        judge=judge_index,
        expectations=result.expectations,
        session_id=response.session_id if response else None,
        cost_usd=response.cost_usd if response else None,
        input_tokens=response.input_tokens if response else None,
        output_tokens=response.output_tokens if response else None,
        cache_read_tokens=response.cache_read_tokens if response else None,
        cache_creation_tokens=response.cache_creation_tokens if response else None,
        duration_ms=response.duration_ms if response else None,
        error=error,
    )


def _aggregate(
    results: list[GradingResult], expectations: tuple[Expectation, ...]
) -> GradingResult:
    """Fold N per-judge ballots into one verdict per expectation, applying each gate.

    Aligned by position: for each expectation, count the pass votes, apply its gate (``majority``
    or ``unanimous``) to decide pass/fail, and keep the raw tally so ``ExpectationResult.color`` can
    read the vote strength independently of the gate verdict. Runs even for a single judge so the
    tally + gate are always populated.
    """
    merged: list[ExpectationResult] = []

    for index, expectation in enumerate(expectations):
        ballots = [r.expectations[index] for r in results]
        pass_votes = sum(1 for b in ballots if b.passed)
        total_votes = len(ballots)
        verdict = expectation.passes(pass_votes, total_votes)
        # Cite a judge whose ballot matches the gate verdict, so a pass never carries an error-vote
        # placeholder and a failed unanimous gate cites a dissenting judge's reasoning.
        cited = next((b for b in ballots if b.passed == verdict), ballots[0])
        merged.append(
            ExpectationResult(
                text=cited.text,
                passed=verdict,
                evidence=cited.evidence,
                pass_votes=pass_votes,
                total_votes=total_votes,
                gate=expectation.gate,
            )
        )

    passed = sum(1 for e in merged if e.passed)
    total = len(merged)

    return GradingResult(
        expectations=tuple(merged),
        passed=passed,
        failed=total - passed,
        total=total,
        pass_rate=(passed / total) if total else 0.0,
    )


def grade(
    ev: Eval,
    output_text: str,
    *,
    produced_files: ProducedFiles = (),
    model: str,
    effort: str,
    judges: int = 1,
    call_json: CallJson = llm.call_json,
) -> GradingResult:
    """Grade an eval's expectations against the produced output, optionally voting over N runs.

    ``produced_files`` carries the content of the eval's declared output files (path, content);
    for file-producing skills the judge grades that content, not just the agent's final message.
    """
    if judges < 1:
        raise ValueError("judges must be >= 1")

    if not ev.expectations:
        return GradingResult(expectations=(), passed=0, failed=0, total=0, pass_rate=0.0)

    request = llm.LLMRequest(
        prompt=_build_grader_prompt(ev, output_text, produced_files), model=model, effort=effort
    )
    validate = _grading_validator(len(ev.expectations))
    rounds = [
        _grade_once(request, validate, ev.expectations, call_json) for _ in range(judges)
    ]
    ballots = tuple(
        _ballot(index, result, response, error)
        for index, (result, response, error) in enumerate(rounds, start=1)
    )

    return replace(_aggregate([r for r, _, _ in rounds], ev.expectations), ballots=ballots)
