---
name: test-prompt
description: Autonomous orchestrator that re-tests a target after it changes and keeps its eval suite and judge calibration in sync. A target is any prompt/instruction file the eval-harness runs in place — a skill's SKILL.md, a workflow, an agent definition, any prompt file. Targets one explicitly, or — if omitted — diffs the current repo to find changed targets. Runs the eval-harness baseline, maps the diff to coverage gaps, delegates authoring to generate-evals and calibration to generate-ground-truth, iterates fixtures to green, then surfaces ONE consolidated review (full diff + a decision log of every judgment call made) at the end — no mid-run stops. Uses regrade for wording and run for behavior. Never commits.
---

# Test Prompt — re-test a target after it changes, keeping its evals in sync

You edited a **target** — any prompt/instruction file the eval-harness runs in place (a skill's
SKILL.md, a workflow, an agent definition, a prompt file). This orchestrates the *eval-maintenance*
loop around that edit: baseline the existing evals against the new target, fill coverage gaps,
recalibrate only if needed, and iterate to green. It runs the loop **end-to-end autonomously** —
making each judgment call (regression-vs-stale, contract derivation, fixture design, GT labels) with
best judgment as it goes — then surfaces **one consolidated review at the end**: the full diff plus a
decision log, for you to accept or correct. No mid-run stops; you review the finished result, not each
step. It never commits — the diff is yours to land.

## Inputs

Invoked as `/test-prompt [target]`.

- **`target` given** → operate on that target (a name or a path to the prompt file under test).
- **`target` omitted** → `git diff` the current repo (working tree + last commit) and resolve the
  changed target(s) from the touched prompt files. If several changed, list them and ask which to run
  (or confirm "all, in sequence").

The target's `evals/` sits beside it (harness convention). The harness `--target` is that file's path.

## The harness you drive

`eval-harness run <mode> --target <FILE> [--eval ID…] [--concurrency N] [--out DIR]`:

| mode | what it does | when |
|---|---|---|
| `run all` | runs the target on each eval, deterministic checks, then judge (gated on deterministic pass) | the baseline + final validation |
| `run deterministic` | run + objective checks only (no judge) | cheap re-check while iterating |
| `run regrade` | re-judges the **latest run's saved outputs** against current expectations — **no agent runs** | tuning **expectation wording** only |
| `run calibrate` | re-grades frozen ground-truth cases → judge-vs-human agreement | **only when ground truth changed** |

### Locating & invoking it

The harness is the **`eval-harness` project** — this repo (this skill ships inside it), and equally a
standalone tool you can point at targets living in *other* repos. It has **zero runtime deps**, so its
existing virtualenv runs without any install (never `pip install` it). Invoke in this order of
preference:

1. **`uv run eval-harness`** from the harness repo root — the canonical in-repo form (this is where the
   skill lives);
2. `eval-harness` if it's on `PATH`;
3. the project's venv binary — `<eval-harness>/.venv/bin/eval-harness`;
4. `<eval-harness>/.venv/bin/python -m eval_harness.cli`.

When driving a target in **another** repo and the harness path isn't known, locate it:
`find ~ -maxdepth 2 -name pyproject.toml -path '*eval-harness*'`. Run from **any** cwd — pass an
**absolute** `--target`; the harness reads the sibling `evals/evals.json` and writes results to
`eval-runs/` beside the target. A `run` spawns real `claude -p` subprocesses (minutes), so launch it in
the **background** and poll the log file.

## Flow

Run these in order, **end-to-end without stopping** — make each call with best judgment and record it
for the final review (see Output). The only human checkpoint is **after** the run, over the diff.

1. **Resolve target(s)** — from the arg, or the repo diff (working tree + last commit). If several
   targets changed, do **all, in sequence** — don't ask; note the set in the final review.
2. **Diff → changed behaviors** — read the target's diff hunks; summarize which contract items were
   added / changed / removed. **Targeting signal only** — it tells you *where* coverage is at risk; it
   is **never** the source of an assertion (assertions derive from the target's stated contract, via
   generate-evals — the anti-circularity invariant holds under autonomy too).
3. **Baseline** — `run all` on the existing evals against the edited target (narrow with `--eval` if
   the change is local). For each red, **decide regression-vs-stale yourself and act**: target
   regressed → the fix belongs in the target (flag it — that's a real catch); a deliberate behavior
   change made a once-correct eval *stale* → update the eval. **Record every classification + action
   for the final review.**
4. **Map coverage gaps** — `generate-evals analyze <target>` → gaps; intersect with the step-2 changed
   behaviors. Fill via `generate-evals create <target>`, driven **autonomously**: derive and
   **pre-confirm the contract from the target's stated rules**, author the cases, and **fold its held
   uncertain/bait cases into your final review** rather than stopping mid-run.
5. **Ground truth** — only if a **new judge-expectation *type*** was introduced (a new kind of
   subjective claim the judge hasn't been calibrated on). Then `generate-ground-truth create <target>` —
   **draft** the labels with best judgment, `run calibrate`, and **surface the drafted labels in the
   final review** for confirmation. **No new ground-truth type → skip calibrate.**
6. **Iterate to green:**
   - Changed only **expectation wording** → `run regrade` (re-judges saved outputs, no agent runs — near free).
   - Changed the **target** (a fix) or added/edited a **fixture** → `run` (full agent re-exec; narrow with `--eval`).
   - **Never substitute regrade for run** — regrade re-scores old outputs and cannot validate a fix.
   - Loop autonomously until green, diagnosing the **root cause** of each red: fixture flaw → fix the
     fixture; assertion off-contract → fix the eval; genuine target regression → flag it (don't tune
     the target to the eval).
7. **Final review** — compile the Output report: the **full diff** of everything changed + a
   **decision log** of every call made. **Do not commit** — this is the hand-off for my review.

## Riskiest call — existing-case edits

Adding a new eval or GT case is routine. **Modifying or deleting an existing** eval or GT case is the
riskiest move — it asserts "a once-correct case is now wrong." Make the call when the evidence is clear
(the diff shows a deliberate, intended behavior change), but **flag every such edit prominently in the
final review's decision log** — and since it lands in the diff, I see it before anything is committed.
When the evidence is genuinely ambiguous, **prefer leaving the existing case untouched** and noting the
tension in the review over guessing.

## Cost discipline

A single eval is a full agentic `claude -p` run (minutes) + serial judges; the full suite is ~$ and
~10+ min. Run autonomously, but spend efficiently — and report **total** spend in the final review:

- **Narrow with `--eval`** to the behaviors the diff actually touched; don't re-run the whole suite to
  check one local change.
- **`regrade` before `run`** wherever the change is expectation-wording only.
- Concurrency is already maxed for small suites (default `min(8, cpu)`), so it is **not** the lever.

## Output

Compiled **once, at the end** — this is the review I read:

```
Test-Prompt Results — <target>:
- Target: [arg | resolved from repo diff; all targets if several]
- Changed behaviors: [contract items added/changed/removed]
- Baseline: [run all: X/Y passed; each red + how you classified and acted on it — fix-target vs eval-update]
- Coverage: [gaps via analyze; new evals/fixtures added; any held uncertain/bait cases needing my call]
- Ground truth: [new expectation-type? GT cases drafted + calibrated, labels for my confirmation | skipped — no new type]
- Iteration: [regrade vs run used where; root cause of each red fixed; final result green/red]
- Decision log: [every call made autonomously — regression-vs-stale, contract assumptions, fixture designs, existing-case edits — one line each]
- Diff: [files added/modified, for my review]
- Spend: [total approx $ / wall]
- Commit: not performed — diff left for your review
```

## No commit

Never `git add`/`commit`/`push`. This tool changes evals and targets; you review and commit yourself.
