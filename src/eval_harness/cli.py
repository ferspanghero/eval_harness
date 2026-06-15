"""CLI + orchestration for the ``eval-harness`` entrypoint.

Two verbs: ``init`` deterministically scaffolds a target's ``evals/`` + ``ground_truth/`` (no LLM);
``run <mode>`` evaluates one or more targets (``--target FILE``; the file's content becomes the
run's system prompt — execution model B; reads the sibling ``<dir>/evals/evals.json``, writes
results to ``<dir>/eval-runs/``).

Flow per eval: run the target once (``runner``) → deterministic checks on its artifacts →
optionally the LLM judge → aggregate into a :class:`~eval_harness.benchmark.Benchmark`.

Run modes:
- ``deterministic`` — run + objective checks, no judge.
- ``judge`` — run + checks + judge (ungated).
- ``all`` — run + checks; judge only when the deterministic tier passes (fail-fast gate).
- ``analyze`` — standalone LLM pass over the latest written benchmark.
- ``calibrate`` — re-grade frozen calibration cases and report judge-vs-human agreement.
- ``regrade`` — re-judge the latest run's saved outputs against the *current* expectations, no
  agent runs (cheap, fast iteration on expectation wording).

``--eval ID|NAME`` narrows a run or regrade to specific eval(s) within the target.

Exit codes: 0 PASS / 1 evals failed / 2 hard failure.
The agent run + deterministic checks happen once; only the judge repeats (``--judges``).
A sweep runs evals concurrently on a bounded worker pool (``--concurrency``); judge calls stay
serial within an eval, so the bound is also the max number of concurrent ``claude`` processes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from eval_harness import analyzer, calibration, deterministic, judge, llm, runner
from eval_harness.benchmark import Benchmark, EvalOutcome, detail_rel_path
from eval_harness.calibration import CalibrationReport
from eval_harness.runner import RunError, RunResult
from eval_harness.schemas import (
    CalibrationCase,
    Eval,
    Fixture,
    GradingResult,
    ProducedFiles,
)

logger = logging.getLogger("eval_harness")

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

# Icons for analyzer note severities (see analyzer.SEVERITIES). Uniform-width emoji (no variation
# selectors) so the single space after the icon renders consistently across terminals.
_NOTE_ICONS = {"ok": "🟢", "warning": "🟡", "issue": "🔴"}

# PASS/FAIL line icons — the vote-strength color (green unanimous / yellow majority / red below),
# shown alongside the gate's PASS/FAIL so a borderline-but-passing eval is visible at a glance.
_COLOR_ICONS = {"green": "🟢", "yellow": "🟡", "red": "🔴"}


def _verdict_line(label: str, *, passed: bool, color: str) -> str:
    """One-line verdict: ``PASS``/``FAIL`` (the gate decision) + the vote-strength icon."""
    return f"{label}: {'PASS' if passed else 'FAIL'} {_COLOR_ICONS[color]}"

# Workspaces live OUTSIDE the repo: claude -p resolves a run's project root from the nearest
# ancestor .git, so a workspace inside this repo would make the target write into the repo itself.
# Out-of-repo + an empty .claude marker makes each workspace its own isolated project root.
DEFAULT_RUNS_ROOT = Path(tempfile.gettempdir()) / "eval-harness-runs"
# Results co-locate beside the target: `<dir>/eval-runs/<stamp>/` by default (gitignored),
# overridable via --out. A multi-target run writes one combined run under `./eval-runs/`.
DEFAULT_RESULTS_DIRNAME = "eval-runs"
# The judge calibration ground-truth set — frozen (output, expectation, human_label) cases,
# committed (the durable judge-regression asset), unlike the gitignored evaluations/run workspaces.
# Split one file per skill under calibration/; the caller names which file(s) to
# calibrate — no default.

# CLI defaults live here, applied only as argparse `default=`; the concrete value flows downstream
# (into logs/benchmarks too) and is always passed to `claude -p` — model/effort are never None below
# the CLI. One model + one effort serve every LLM call (the run, the judge, the analyzer); split per
# consumer later if needed — the calls still go through the one `llm` seam.
DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_EFFORT = "xhigh"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")

# Worker-pool bound for the sweep. Workers are subprocess-I/O-bound (each blocks on one `claude -p`
# at a time — judge calls stay serial within an eval), so CPU count is only a loose ceiling; the 8
# cap is what keeps a default sweep inside the subscription's burst tolerance.
DEFAULT_CONCURRENCY = min(8, os.cpu_count() or 1)

RunFn = Callable[[Eval, Path, str, Path, "str | None", str, str], RunResult]
GradeFn = Callable[[Eval, str, ProducedFiles, int, str, str], GradingResult]
CalibrateFn = Callable[..., CalibrationReport]

_EMPTY_GRADING = GradingResult(expectations=(), passed=0, failed=0, total=0, pass_rate=0.0)


def _default_run_fn(  # pragma: no cover
    ev: Eval,
    fixture_dir: Path,
    target: str,
    target_path: Path,
    system_prompt: str | None,
    model: str,
    effort: str,
) -> RunResult:
    """Runs one eval headless via the real runner and returns the captured result."""
    return runner.run(
        ev,
        fixture_dir,
        target=target,
        target_path=target_path,
        model=model,
        effort=effort,
        system_prompt=system_prompt,
        runs_root=DEFAULT_RUNS_ROOT,
    )


def _default_grade_fn(  # pragma: no cover
    ev: Eval, output_text: str, produced_files: ProducedFiles, judges: int, model: str, effort: str
) -> GradingResult:
    """Grades the eval's expectations against the produced output with the real judge."""
    return judge.grade(
        ev, output_text, produced_files=produced_files, model=model, effort=effort, judges=judges
    )


def _default_calibrate_fn(  # pragma: no cover
    cases: list[CalibrationCase], *, judges: int, model: str, effort: str
) -> CalibrationReport:
    """Runs each calibration case through the real grader and returns the agreement report."""
    def grade_case(case: CalibrationCase) -> bool:
        return calibration.judge_case(case, judges=judges, model=model, effort=effort)

    return calibration.calibrate(cases, grade_case=grade_case)


def _load_calibration_cases(path: Path) -> list[CalibrationCase]:
    """Parse a calibration ground-truth ``.jsonl`` — one frozen case per non-blank line."""
    return [
        CalibrationCase.from_dict(json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _read_output_files(ev: Eval, workspace: Path) -> ProducedFiles:
    """Read each declared output file's content from the run workspace for the judge.

    A declared file the skill never produced is reported as ``None`` rather than raising, so the
    judge can render a placeholder and fail the content expectation instead of crashing the sweep.
    Reads as UTF-8 with ``errors="replace"`` — matching ``deterministic._read_file`` so both tiers
    see produced artifacts identically and a stray byte can't abort the run.
    """
    produced: list[tuple[str, str | None]] = []
    for rel in ev.output_files:
        path = workspace / rel
        content = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        produced.append((rel, content))

    return tuple(produced)


def _write_eval_detail(
    detail_root: Path,
    target: str,
    ev: Eval,
    output_text: str,
    produced_files: ProducedFiles,
    grading: GradingResult,
    projects_dir: Path,
) -> None:
    """Persist one eval's audit record: final message, judged file content, and judge ballots.

    The record makes the run self-contained: ``output.md`` + ``files/`` are exactly what the judge
    graded (and what ``regrade`` re-judges), and ``ballots.json`` keeps every judge's raw votes and
    call metadata. Each ballot's transcript is **copied** in (``judge-<n>.jsonl``) when locatable —
    the CLI prunes its own ``~/.claude/projects`` copies after a retention window, so a pointer
    alone would rot.
    """
    out = detail_root / detail_rel_path(target, ev.name)
    out.mkdir(parents=True, exist_ok=True)
    (out / "output.md").write_text(output_text, encoding="utf-8")

    for rel, content in produced_files:
        if content is None:
            continue

        dest = out / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    ballots = []
    for ballot in grading.ballots:
        transcript = None
        source = (
            runner.find_transcript(projects_dir, ballot.session_id)
            if ballot.session_id
            else None
        )

        if source is not None:
            transcript = f"judge-{ballot.judge}.jsonl"
            shutil.copyfile(source, out / transcript)

        ballots.append({
            "judge": ballot.judge,
            "session_id": ballot.session_id,
            "transcript": transcript,
            "error": ballot.error,
            "cost_usd": ballot.cost_usd,
            "duration_ms": ballot.duration_ms,
            "tokens": {
                "input": ballot.input_tokens,
                "output": ballot.output_tokens,
                "cache_read": ballot.cache_read_tokens,
                "cache_creation": ballot.cache_creation_tokens,
            },
            "expectations": [
                {"text": e.text, "passed": e.passed, "evidence": e.evidence}
                for e in ballot.expectations
            ],
        })
    (out / "ballots.json").write_text(json.dumps({"ballots": ballots}, indent=2))


def _run_one_eval(
    fixture: Fixture,
    fixture_dir: Path,
    target_path: Path,
    ev: Eval,
    *,
    mode: str,
    judges: int,
    model: str,
    effort: str,
    run_fn: RunFn,
    grade_fn: GradeFn,
    detail_root: Path | None,
    projects_dir: Path,
) -> EvalOutcome:
    """The full chain for one eval: run → deterministic checks → (gated) judge → audit record.

    Progress is logged via the module ``logger`` — INFO for normal steps, WARNING when a check
    fails (deterministic miss or judge error) — emitted *before* each slow
    step so the operator sees what a multi-second ``claude -p`` call is waiting on. Every line
    is prefixed ``target/eval`` so it stays self-identifying when concurrent evals interleave.
    """
    label = f"{fixture.target}/{ev.name}"
    started = time.perf_counter()
    logger.info("%s: running…", label)
    run = run_fn(ev, fixture_dir, fixture.target, target_path, fixture.system_prompt, model, effort)
    artifacts = deterministic.RunArtifacts(
        response=run.output_text,
        workspace=run.workspace,
        fixture_dir=fixture_dir,
    )
    det = deterministic.evaluate(ev.checks, artifacts)
    det_level = logging.INFO if det.all_passed else logging.WARNING
    logger.log(det_level, "%s: deterministic %d/%d", label, det.passed, det.total)

    produced_files = _read_output_files(ev, run.workspace)

    if mode == "judge" or (mode == "all" and det.all_passed):
        logger.info("%s: judging (%d×)…", label, judges)
        # grade owns judge-error handling: a failed grader call is a logged fail vote, so the
        # sweep is never crashed by it (see judge.grade / _errored_vote).
        grading = grade_fn(ev, run.output_text, produced_files, judges, model, effort)
        logger.info("%s: judge %d/%d", label, grading.passed, grading.total)
    else:
        grading = _EMPTY_GRADING

    if detail_root is not None:
        _write_eval_detail(
            detail_root, fixture.target, ev, run.output_text,
            produced_files, grading, projects_dir,
        )

    outcome = EvalOutcome.from_parts(run, ev.name, det, grading)
    logger.info("%s", _verdict_line(label, passed=outcome.all_passed, color=outcome.color))
    logger.info("%s: elapsed %.1fs", label, time.perf_counter() - started)

    return outcome


def run_suite(
    fixtures: list[tuple[Fixture, Path, Path]],
    *,
    mode: str,
    judges: int,
    model: str,
    effort: str,
    concurrency: int = 1,
    run_fn: RunFn = _default_run_fn,
    grade_fn: GradeFn = _default_grade_fn,
    detail_root: Path | None = None,
    projects_dir: Path = runner.DEFAULT_PROJECTS_DIR,
) -> list[Benchmark]:
    """Run every eval across the fixtures on a bounded worker pool, aggregating per fixture.

    Each eval's chain is one independent task (own workspace, session id, detail dir), so evals
    from different fixtures overlap freely up to ``concurrency`` workers — also the max number of
    concurrent ``claude`` processes, since judge calls stay serial within an eval. Outcomes
    regroup in fixture/eval declaration order, so results are identical regardless of completion
    order (a 1-worker pool reproduces the serial sweep exactly). The first ``RunError`` aborts:
    not-yet-started evals are skipped, and the error propagates once in-flight evals drain.

    With ``detail_root`` set, each eval's audit record (final message, judged file content,
    ballots, judge transcripts) is persisted under ``<detail_root>/evals/<target>/<eval>/``.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")

    aborted = threading.Event()

    def run_one(
        fixture: Fixture, fixture_dir: Path, target_path: Path, ev: Eval
    ) -> EvalOutcome | None:
        if aborted.is_set():
            return None

        try:
            return _run_one_eval(
                fixture, fixture_dir, target_path, ev, mode=mode, judges=judges, model=model,
                effort=effort, run_fn=run_fn, grade_fn=grade_fn, detail_root=detail_root,
                projects_dir=projects_dir,
            )
        except Exception:
            # Any chain failure (not just RunError) dooms the sweep — trip the abort so pending
            # evals stop launching paid agent runs; the re-raise reaches the gather below.
            aborted.set()
            raise

    jobs = [
        (index, fixture, fixture_dir, target_path, ev)
        for index, (fixture, fixture_dir, target_path) in enumerate(fixtures)
        for ev in fixture.evals
    ]

    for fixture, _, _ in fixtures:
        logger.info(
            "Evaluating %s: %d eval(s), mode=%s, model=%s, effort=%s",
            fixture.target, len(fixture.evals), mode, model, effort,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(run_one, fixture, fixture_dir, target_path, ev)
            for _, fixture, fixture_dir, target_path, ev in jobs
        ]
        # Submission-order gather: the first failed eval's RunError propagates from result().
        # A job skipped after the abort returns None, but a skip implies a raised sibling in the
        # same list, so the comprehension never completes with a None in it — the cast is safe.
        gathered = cast("list[EvalOutcome]", [future.result() for future in futures])

    grouped: list[list[EvalOutcome]] = [[] for _ in fixtures]

    for (index, *_), outcome in zip(jobs, gathered, strict=True):
        grouped[index].append(outcome)

    benchmarks = []

    for (fixture, _, _), outcomes in zip(fixtures, grouped, strict=True):
        bench = Benchmark.from_outcomes(fixture.target, tuple(outcomes))
        logger.info(
            "%s", _verdict_line(fixture.target, passed=bench.all_passed, color=bench.color)
        )
        benchmarks.append(bench)

    return benchmarks


def run_evals(
    fixture: Fixture,
    fixture_dir: Path,
    *,
    target_path: Path = Path("SKILL.md"),
    mode: str,
    judges: int,
    model: str,
    effort: str,
    run_fn: RunFn = _default_run_fn,
    grade_fn: GradeFn = _default_grade_fn,
    detail_root: Path | None = None,
    projects_dir: Path = runner.DEFAULT_PROJECTS_DIR,
) -> Benchmark:
    """Run every eval in one fixture serially — :func:`run_suite` with a single fixture/worker."""
    return run_suite(
        [(fixture, fixture_dir, target_path)], mode=mode, judges=judges, model=model, effort=effort,
        run_fn=run_fn, grade_fn=grade_fn, detail_root=detail_root, projects_dir=projects_dir,
    )[0]


def verdict(bench: Benchmark) -> int:
    """PASS only if every eval passed all of its checks; otherwise FAIL."""
    return EXIT_PASS if bench.all_passed else EXIT_FAIL


def _target_dir(target_path: Path) -> Path:
    """A target file's asset home — its parent dir (holds evals/, ground_truth/, eval-runs/)."""
    return target_path.parent


def _evals_path(target_path: Path) -> Path | None:
    """The target's ``evals/evals.json`` (sibling of the target file), or ``None`` if absent."""
    candidate = _target_dir(target_path) / "evals" / "evals.json"

    return candidate if candidate.is_file() else None


def _resolve_targets(targets: list[str] | None) -> list[Path]:
    """The target files to act on — each named explicitly by path (no discovery, no default)."""
    return [Path(t) for t in targets] if targets else []


def _results_root(out: str | None, target_paths: list[Path]) -> Path:
    """Where run results live: ``--out`` if given, else `<dir>/eval-runs` for a single target."""
    if out:
        return Path(out)

    if len(target_paths) == 1:
        return _target_dir(target_paths[0]) / DEFAULT_RESULTS_DIRNAME

    return Path(DEFAULT_RESULTS_DIRNAME)


def _default_ground_truth(target_paths: list[Path]) -> list[Path]:
    """The convention ground-truth path per target: ``<dir>/ground_truth/<dir-name>.jsonl``."""
    return [
        _target_dir(t) / "ground_truth" / f"{_target_dir(t).resolve().name}.jsonl"
        for t in target_paths
    ]


def _apply_eval_filter(fixture: Fixture, selectors: list[str] | None) -> Fixture:
    """Keep only the evals whose id (as a string) or name is in ``selectors`` (all if None)."""
    if not selectors:
        return fixture

    wanted = set(selectors)
    kept = tuple(e for e in fixture.evals if str(e.id) in wanted or e.name in wanted)

    return replace(fixture, evals=kept)


def _combine(benchmarks: list[Benchmark]) -> dict[str, Any]:
    """Fold per-fixture benchmarks into one suite document with a combined summary."""
    outcomes = [o for b in benchmarks for o in b.outcomes]
    num_evals = len(outcomes)
    mean = (sum(o.pass_rate for o in outcomes) / num_evals) if num_evals else 0.0

    return {
        "summary": {
            "num_fixtures": len(benchmarks),
            "num_evals": num_evals,
            "evals_passed": sum(b.evals_passed for b in benchmarks),
            "mean_pass_rate": round(mean, 4),
            "total_cost_usd": round(sum(b.total_cost_usd for b in benchmarks), 6),
            "total_duration_ms": sum(b.total_duration_ms for b in benchmarks),
            "tokens": {
                "total": sum(
                    b.total_input_tokens
                    + b.total_output_tokens
                    + b.total_cache_read_tokens
                    + b.total_cache_creation_tokens
                    + b.total_judge_tokens
                    for b in benchmarks
                ),
                "input": sum(b.total_input_tokens for b in benchmarks),
                "output": sum(b.total_output_tokens for b in benchmarks),
                "cache_read": sum(b.total_cache_read_tokens for b in benchmarks),
                "cache_creation": sum(b.total_cache_creation_tokens for b in benchmarks),
                "judges": sum(b.total_judge_tokens for b in benchmarks),
            },
            "grader_prompt_version": judge.GRADER_PROMPT_VERSION,
        },
        "fixtures": [b.to_dict() for b in benchmarks],
    }


def _suite_verdict(benchmarks: list[Benchmark]) -> int:
    """PASS only if every fixture's every eval passed; FAIL otherwise (or if nothing ran)."""
    if not benchmarks:
        return EXIT_FAIL

    return EXIT_PASS if all(verdict(b) == EXIT_PASS for b in benchmarks) else EXIT_FAIL


def _write_benchmark(data: dict[str, Any], evaluations_root: Path, stamp: str) -> Path:
    out_dir = evaluations_root / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "results.json"
    path.write_text(json.dumps(data, indent=2))

    return path


def _latest_results_path(evaluations_root: Path) -> Path | None:
    candidates = sorted(evaluations_root.glob("*/results.json"))

    return candidates[-1] if candidates else None


def _latest_benchmark(evaluations_root: Path) -> dict[str, Any] | None:
    path = _latest_results_path(evaluations_root)
    if path is None:
        return None

    data: dict[str, Any] = json.loads(path.read_text())

    return data


def _read_optional(path: Path) -> str | None:
    """A tolerant optional read: file content, or ``None`` when the file doesn't exist."""
    return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None


def _saved_grading_input(
    sev: dict[str, Any], run_dir: Path, ev: Eval
) -> tuple[str, ProducedFiles] | None:
    """What to re-judge for one saved eval: its detail record, or the legacy inline text.

    The detail record reconstructs exactly what the original judge saw — the final message plus
    each declared output file's persisted content (``None`` when it was never produced). Runs
    persisted before the detail record carry only an inline ``output_text``; those regrade on the
    final message alone. ``None`` when the saved run has nothing usable for this eval.
    """
    detail = sev.get("detail")

    if detail:
        output_path = run_dir / detail / "output.md"

        if output_path.is_file():
            produced = tuple(
                (rel, _read_optional(run_dir / detail / "files" / rel))
                for rel in ev.output_files
            )

            return output_path.read_text(encoding="utf-8", errors="replace"), produced

    legacy = sev.get("output_text")
    if legacy is None:
        return None

    return str(legacy), ()


def _load_target(target_path: Path, selectors: list[str] | None) -> tuple[Fixture, Path] | None:
    """A target's filtered fixture + its seed dir (the ``evals/`` dir), or ``None`` if no evals.

    The seed dir is the ``evals/`` directory beside the target file — where the runner copies seed
    files and held-out acceptance assets from.
    """
    ev_path = _evals_path(target_path)
    if ev_path is None:
        return None

    return _apply_eval_filter(Fixture.from_json(ev_path.read_text()), selectors), ev_path.parent


def _log_no_evals(target_path: Path) -> None:
    """The shared 'no evals beside this target' error — run and regrade message identically."""
    logger.error(
        "No evals found for %s (expected %s/evals/evals.json — run `eval-harness init`)",
        target_path, _target_dir(target_path),
    )


def _regrade(args: argparse.Namespace, evaluations_root: Path, grade_fn: GradeFn) -> int:
    """Re-grade the latest run's saved outputs against the *current* expectations — judge only.

    No agent runs: each eval's saved record is graded against the expectations now on disk, so
    iterating on expectation wording costs judge calls only. The detail record (final message +
    persisted output-file content) reconstructs exactly what the original judge saw, so regrading
    is faithful for file-producing skills too; legacy runs without a detail record fall back to
    the inline final message.
    """
    results_path = _latest_results_path(evaluations_root)
    if results_path is None:
        logger.error("No results found to regrade under %s/", evaluations_root)
        return EXIT_ERROR

    saved: dict[str, Any] = json.loads(results_path.read_text())
    run_dir = results_path.parent
    targets = _resolve_targets(args.target)
    current: dict[str, Fixture] = {}
    for t in targets:
        loaded = _load_target(t, args.eval)
        if loaded is None:
            _log_no_evals(t)
            return EXIT_ERROR

        current[loaded[0].target] = loaded[0]
    saved_evals = {
        (sfx["target"], sev["eval_id"]): sev
        for sfx in saved.get("fixtures", [])
        for sev in sfx.get("evals", [])
    }

    logger.info("Regrading saved outputs under %s/ against current expectations…", evaluations_root)
    graded = 0
    all_passed = True
    for fx in current.values():
        for ev in fx.evals:
            sev = saved_evals.get((fx.target, ev.id))
            grading_input = _saved_grading_input(sev, run_dir, ev) if sev else None
            if grading_input is None:
                continue

            output_text, produced_files = grading_input
            grading = grade_fn(
                ev, output_text, produced_files, args.judges, args.model, args.effort
            )
            graded += 1
            passed = grading.passed == grading.total
            all_passed = all_passed and passed
            print(_verdict_line(
                f"{fx.target}/{ev.id} {ev.name}",
                passed=passed, color="green" if passed else "red",
            ))
            for e in grading.expectations:
                print(f"  [{'PASS' if e.passed else 'FAIL'}] {e.text}")

    if graded == 0:
        logger.error("No saved outputs matched the selected eval(s) under %s/", evaluations_root)
        return EXIT_ERROR

    return EXIT_PASS if all_passed else EXIT_FAIL


def _positive_int(value: str) -> int:
    """Argparse type for flags that must be a positive integer (e.g. ``--concurrency``)."""
    parsed = int(value)

    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")

    return parsed


def _configure_logging() -> None:
    """Timestamped, levelled log lines to stdout. Re-applied per ``main`` call (``force=True``)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def _timestamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


AnalyzeFn = Callable[..., "list[analyzer.AnalysisNote]"]


# --- init: deterministic scaffold (no LLM, no network) -----------------------------

_SCAFFOLD_EVALS_README = """\
# evals/

`evals.json` declares the cases the harness runs against this skill — one file shaped
`{"target": "<skill>", "evals": [ ... ]}`. Each eval:

- `id` / `name` — stable identifiers (`--eval` selects by either).
- `prompt` — the task handed to the skill.
- `checks` — deterministic, objective assertions (no LLM): `response_contains`,
  `response_contains_any`, `file_exists`, `file_contains`, `command_succeeds`, `coverage_at_least`,
  `acceptance_test`.
- `expectations` — judge-graded, each `{ "text": ..., "gate": "majority" | "unanimous" }`.
- `output_files` (optional) — produced files whose content the judge should read.

Seeds and held-out assets live alongside this file. Results land in `../eval-runs/` (gitignored);
judge ground truth lives in `../ground_truth/<name>.jsonl`.
"""


def _scaffold_evals_json(name: str) -> str:
    """A schema-valid starter eval (placeholders to replace); loads via ``Fixture.from_json``."""
    return json.dumps(
        {
            "target": name,
            "evals": [
                {
                    "id": 1,
                    "name": "example",
                    "prompt": "Describe the task to hand the target (replace this).",
                    "checks": [
                        {
                            "type": "response_contains",
                            "description": "the response addresses the task (replace this)",
                            "value": "replace-with-an-expected-substring",
                        }
                    ],
                    "expectations": [
                        {
                            "text": "What the judge should verify about the output (replace this).",
                            "gate": "majority",
                        }
                    ],
                }
            ],
        },
        indent=2,
    ) + "\n"


def _write_if_absent(path: Path, content: str) -> bool:
    """Write ``content`` only when ``path`` is absent — scaffolding never clobbers files."""
    if path.exists():
        return False

    path.write_text(content, encoding="utf-8")

    return True


def _init(target_path: Path) -> int:
    """Scaffold a target's eval assets beside it: evals/, ground_truth/, .gitignore (no LLM).

    Deterministic — pure filesystem, no LLM and no network. Idempotent: existing files are left
    untouched, so re-running only fills in what's missing. ``<dir>`` is the target file's parent.
    """
    target_dir = _target_dir(target_path)
    name = target_dir.resolve().name
    evals_dir = target_dir / "evals"
    ground_truth_dir = target_dir / "ground_truth"
    evals_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_dir.mkdir(parents=True, exist_ok=True)

    for path, content in (
        (evals_dir / "evals.json", _scaffold_evals_json(name)),
        (evals_dir / "README.md", _SCAFFOLD_EVALS_README),
        (ground_truth_dir / f"{name}.jsonl", ""),
        (target_dir / ".gitignore", "eval-runs/\n"),
    ):
        if _write_if_absent(path, content):
            logger.info("created %s", path)

    logger.info(
        "Scaffolded eval assets beside %s — author evals/evals.json, then "
        "`eval-harness run all --target %s`", target_path, target_path,
    )

    return EXIT_PASS


def _add_run_arguments(parser: argparse.ArgumentParser) -> None:
    """The flags for `eval-harness run <mode>` — every eval/judge/calibrate/regrade/analyze knob."""
    parser.add_argument(
        "mode", choices=["deterministic", "judge", "analyze", "all", "calibrate", "regrade"]
    )
    parser.add_argument(
        "--target",
        action="append",
        metavar="FILE",
        help="path to the file under test (e.g. a SKILL.md); repeatable for several",
    )
    parser.add_argument(
        "--eval",
        nargs="+",
        metavar="ID|NAME",
        help="run/regrade only the eval(s) with these ids or names (default: all for the target)",
    )
    parser.add_argument(
        "--judges", type=int, default=3, help="judge runs per eval; majority vote (default 3)"
    )
    parser.add_argument(
        "--concurrency",
        type=_positive_int,
        default=DEFAULT_CONCURRENCY,
        help="evals run concurrently in a sweep — each is at most one `claude` process at a time "
        f"(default min(8, cpu count) = {DEFAULT_CONCURRENCY})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"model for every LLM call — run, judge, analyzer (default {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--effort",
        default=DEFAULT_EFFORT,
        choices=EFFORT_LEVELS,
        help=f"reasoning effort for every LLM call — judge, analyzer (default {DEFAULT_EFFORT})",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="results root (default: <dir>/eval-runs for a single target, else ./eval-runs)",
    )
    parser.add_argument(
        "--ground-truth",
        nargs="+",
        metavar="FILE",
        help="calibration ground-truth file(s) for `calibrate` — one or more per-target .jsonl "
        "(default: <dir>/ground_truth/<dir-name>.jsonl)",
    )


def _build_parser() -> argparse.ArgumentParser:
    """The `eval-harness` CLI: two verbs — `init` (scaffold) and `run <mode>` (evaluate)."""
    parser = argparse.ArgumentParser(prog="eval-harness", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser(
        "init", help="scaffold evals/ + ground_truth/ beside a target file (no LLM)"
    )
    init_p.add_argument(
        "--target", required=True, metavar="FILE",
        help="the file under test; eval assets are scaffolded in its directory",
    )

    _add_run_arguments(sub.add_parser("run", help="run an eval mode against one or more targets"))

    return parser


def main(
    argv: list[str] | None = None,
    *,
    run_fn: RunFn = _default_run_fn,
    grade_fn: GradeFn = _default_grade_fn,
    analyze_fn: AnalyzeFn = analyzer.analyze,
    calibrate_fn: CalibrateFn = _default_calibrate_fn,
) -> int:
    """Parse arguments and dispatch to `init` (scaffold) or `run` (evaluate)."""
    _configure_logging()
    args = _build_parser().parse_args(argv)

    if args.command == "init":
        return _init(Path(args.target))

    return _run(
        args, run_fn=run_fn, grade_fn=grade_fn, analyze_fn=analyze_fn, calibrate_fn=calibrate_fn,
    )


def _run(
    args: argparse.Namespace,
    *,
    run_fn: RunFn,
    grade_fn: GradeFn,
    analyze_fn: AnalyzeFn,
    calibrate_fn: CalibrateFn,
) -> int:
    """Run the requested mode, write a benchmark, and return an exit code."""
    targets = _resolve_targets(args.target)
    evaluations_root = _results_root(args.out, targets)

    if args.mode == "analyze":
        bench = _latest_benchmark(evaluations_root)
        if bench is None:
            logger.error("No results found to analyze under %s/", evaluations_root)
            return EXIT_ERROR

        logger.info("Analyzing latest results under %s/ …", evaluations_root)
        try:
            notes = analyze_fn(bench, model=args.model, effort=args.effort)
        except llm.LLMError as exc:
            logger.error("Analysis failed: %s", exc)
            return EXIT_ERROR

        for note in notes:
            print(f"{_NOTE_ICONS.get(note.severity, '•')} {note.text}")

        return EXIT_PASS

    if args.mode == "calibrate":
        gt_paths = (
            [Path(p) for p in args.ground_truth]
            if args.ground_truth
            else _default_ground_truth(targets)
        )
        if not gt_paths:
            logger.error(
                "calibrate needs --ground-truth FILE … or a --target with a "
                "ground_truth/<dir-name>.jsonl",
            )
            return EXIT_ERROR

        missing = [p for p in gt_paths if not p.is_file()]
        if missing:
            logger.error(
                "No calibration ground-truth found at %s",
                ", ".join(str(p) for p in missing),
            )
            return EXIT_ERROR

        cases = [case for path in gt_paths for case in _load_calibration_cases(path)]
        if not cases:
            logger.error("No calibration cases in %s", ", ".join(str(p) for p in gt_paths))
            return EXIT_ERROR

        logger.info("Calibrating the judge over %d case(s)…", len(cases))
        try:
            report = calibrate_fn(
                cases, judges=args.judges, model=args.model, effort=args.effort
            )
        except llm.LLMError as exc:
            logger.error("Calibration failed: %s", exc)
            return EXIT_ERROR

        print(
            f"agreement: {report.agreed}/{report.total} "
            f"({report.agreement_rate:.0%}) judge-vs-human"
        )
        for skill, (agreed, total) in sorted(report.by_skill().items()):
            print(f"  {skill}: {agreed}/{total} ({agreed / total:.0%})")
        for r in report.disagreements:
            verdict = "pass" if r.judge_passed else "fail"
            print(f"  DISAGREE judge={verdict} human={r.case.human_label} | {r.case.expectation}")

        return EXIT_PASS

    if args.mode == "regrade":
        return _regrade(args, evaluations_root, grade_fn)

    if not targets:
        logger.error("No --target given; name the file(s) under test by path")
        return EXIT_ERROR

    fixtures: list[tuple[Fixture, Path, Path]] = []
    for t in targets:
        if not t.is_file():
            logger.error("Target file not found: %s", t)
            return EXIT_ERROR

        loaded = _load_target(t, args.eval)
        if loaded is None:
            _log_no_evals(t)
            return EXIT_ERROR

        fixtures.append((loaded[0], loaded[1], t))

    if args.eval and not any(fx.evals for fx, _, _ in fixtures):
        logger.error("No evals matched --eval %s", " ".join(args.eval))
        return EXIT_ERROR

    stamp = _timestamp()
    try:
        benchmarks = run_suite(
            fixtures,
            mode=args.mode,
            judges=args.judges,
            model=args.model,
            effort=args.effort,
            concurrency=args.concurrency,
            run_fn=run_fn,
            grade_fn=grade_fn,
            detail_root=evaluations_root / stamp,
        )
    except RunError as exc:
        logger.error("Run failed: %s", exc)
        return EXIT_ERROR

    suite = _combine(benchmarks)
    path = _write_benchmark(suite, evaluations_root, stamp)
    print(json.dumps(suite["summary"], indent=2))
    print(f"results: {path}")

    return _suite_verdict(benchmarks)
