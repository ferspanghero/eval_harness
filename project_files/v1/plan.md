# v1 Implementation Plan — dev-pipeline-v2 Eval Harness

> A behavioral eval harness for the `/dev-pipeline` workflow, modeled on Anthropic's skill-creator.
> Rationale + prior art: `../research.md` (read §1 + §4 for *why* before building). Deferred ideas:
> `../v2/ideas.md`.
>
> **Project root:** `~/dev-pipeline-v2/` (Python, `src/` + `tests/` layout). The `claude` CLI (v2.1.x)
> is installed and headless `claude -p` works here.
>
> Build **cheap-first**: validate the eval machinery on skills (M1) before the expensive full-pipeline
> run (M2).

## Goal

Regression-test the dev-pipeline as it's edited, by **running** its skills (and the command) on coding
fixtures and **grading the produced artifacts** — assert on *output*, never on the prompt source —
against a baseline version. No static analysis of the prompt files.

## Approach (from skill-creator)

| skill-creator | our use |
|---|---|
| `evals.json` (input + assertions) | fixture format |
| `agents/grader.md` + `grading.json` (`passed`, `evidence`) | the LLM grader |
| `agents/comparator.md` (blind A/B) | regression as "is new worse than baseline" |
| `agents/analyzer.md` | optional cross-case diagnosis |
| `benchmark.json` (pass-rate, tokens, duration) | metrics + baseline comparison |

A fixture's "gold standard" is its **assertions** + the **baseline version's score** — no stored
expected output.

## User Experience (CLI)

```
evaluate deterministic         # all fixtures: objective checks on produced artifacts (no LLM, fail-fast)
evaluate judge   [--judges N]   # all fixtures: LLM grader + comparator (judge repeated N×, majority; default 1)
evaluate analyze               # standalone: one LLM pass over the latest benchmark → observations
evaluate all     [--judges N]   # deterministic → (if it passes) judge.   analyze NOT included
```
LLM calls go through the `llm` seam → headless `claude -p` (subscription, no SDK). Exit codes:
0 PASS / 1 worse-than-baseline / 2 hard failure.

**Run + capture:** the runner executes each fixture in its own temp working directory —
`claude -p "<task>" --session-id <uuid> --output-format json --permission-mode bypassPermissions
[--append-system-prompt "<autonomous directive>"] [--model <id>]`. Produced files land in the working
directory; the transcript is at `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` (the session id is set, so
the path is known); cost, tokens, and duration come from the JSON stdout.

**Judge:** Opus at max effort (pin a current Opus id, e.g. `claude-opus-4-8`, via `--model`; set max
reasoning effort via the thinking-budget setting — confirm the exact headless mechanism at build time),
with a versioned grader prompt and structured `passed`+`evidence` output. `--judges N` (default 1)
repeats only the judge for a majority vote — the agent run and the deterministic checks each run once.

## Stages / modules (each independently runnable; deterministic gates judge)

```
runner        → headless run (a skill in M1; the command in M2) → store artifacts + transcript
deterministic → objective assertions on the artifacts (pytest, coverage, files, acceptance test)
judge         → grader + comparator (LLM) on artifact content
analyzer      → one-pass diagnosis over the benchmark (optional)
benchmark     → aggregate pass-rate / tokens / duration; compare vs baseline version
```
`all` runs deterministic first and **stops on failure** — never spends judge tokens on output that's
already objectively broken.

## Assertion tiers (assert output, like unit tests)

- **Deterministic** (objective, cheap): execute against the produced artifacts.
- **Judge** (subjective residue): rubric, via LLM.
- **Conventions ride along** as assertion sets attached to any code-producing eval: deterministic
  canaries (type hints, AAA markers, `--cov-branch`, ≥ coverage threshold, no bare `except`) + judge
  for the fuzzy rules. No standalone convention runs.

---

## M0 — Project bootstrap

- **S1** Scaffold the project. `src/pipeline_eval/` holds the modules — `cli.py` (the `evaluate`
  entrypoint), `runner.py`, `deterministic.py`, `judge.py`, `analyzer.py`, `benchmark.py`, `llm.py`
  (the `llm` seam) — `tests/` mirrors them, plus `pyproject.toml` (Python, `pytest`; no
  `markdown-it`/`anthropic` — the harness runs code and shells out to `claude -p`).
- **S2** Copy the workflow under test from the global install into `.claude/`. Source → destination:
  - `~/.claude/skills/dev-pipeline/SKILL.md` → `.claude/commands/dev-pipeline-v2.md` (renamed)
  - each leaf skill the command references (its bold-`code` names — currently `project-brainstorm`,
    `write-project-docs`, `test-driven-development`, `karpathy-guidelines`, `code-review`,
    `verification-before-completion`, `security-audit`, `create-readme`, `create-claude-md`):
    `~/.claude/skills/<name>/` → `.claude/skills/<name>/`
  - `~/.claude/skills/.conventions/*.md` (`testing`, `python`, `java`, `javascript`, `documentation`) →
    `.claude/conventions/`
  - in the copied command, rewrite `~/.claude/skills/.conventions/` → `conventions/` (the only edit to
    the copy). Verify every bold-`code` skill ref resolves to a copied `skills/<name>/SKILL.md`.
- **S3** `llm` seam: a thin wrapper over headless `claude -p` (prompt → text/JSON, with
  validate + retry). Used by `judge` and `analyzer`.

**Gate:** project imports cleanly; `llm` returns a parsed response from a trivial prompt.

## M1 — Eval machinery + SKILLS evals (no full-pipeline run)

Build the harness and prove it on **cheap single-skill runs** (one skill on one task ≈ an order of
magnitude cheaper than the full 6-phase pipeline). Per-skill fixtures are **standalone** — own input +
assertions, independent of any pipeline run.

- Adopt skill-creator schemas (`evals.json`, `grading.json`, `benchmark.json`) + grader/comparator/
  analyzer prompts — fetch the exact field shapes and prompt text from
  `github.com/anthropics/skills` → `skills/skill-creator/` (`references/schemas.md`,
  `agents/{grader,comparator,analyzer}.md`).
- Implement `runner` (single-skill headless + capture), `deterministic`, `judge`, `benchmark`,
  `analyzer`; wire the CLI (`deterministic`/`judge`/`analyze`/`all`). The runner triggers a skill with a
  task prompt and confirms it ran from the `Skill` tool-use in the transcript.
- First fixture: **`code-review` planted-bug** — a small module with a known injected defect (e.g. an
  f-string SQL injection); deterministic assertion = the review names the vulnerable symbol + a relevant
  signal (objective ground truth, because we planted it); judge = quality + no hallucinated criticals.
  Then add skills with a clean input→output contract: `create-readme`, `write-project-docs`,
  `create-claude-md`, `security-audit`. `project-brainstorm`/`karpathy`/`verification` are conversational
  and are covered by the M2 e2e run instead.
- Build the per-convention assertion checklist (ride-along).

Grading runs against **absolute assertions** (acceptance test, coverage) — pass/fail without a baseline.
Committed-vs-working comparison materializes HEAD in a **git worktree**, runs both, and flags a
regression when an assertion passes on HEAD but fails on the working tree across the judge-run band. The
judge **calibration set** (~5–10 hand-labeled runs) is built from the first real runs.

**Gate:** `evaluate all` runs the skill fixtures end-to-end; deterministic + judge produce a benchmark.

## M2 — Full pipeline run + deterministic artifact checks

Run the whole `dev-pipeline-v2` command headless on coding fixtures and evaluate the produced repo. The
runner invokes the command with an injected autonomous directive (`--append-system-prompt`, e.g. *"Run
all six phases end-to-end autonomously; never stop to ask for approval; collect any issues and surface
them only in a final summary."*) so the run doesn't stall on the pipeline's approval gates — no edits to
the copied command. (If the directive proves insufficient, fall back to a test-mode flag on the copy.)

- **Deterministic on artifacts** (most of the Definition of Done): `pytest` passes, coverage ≥ threshold,
  files exist (`plan.md`/`tasks.md`/`README`), an **independent acceptance test** we hold.
- **Judge on artifact content:** plan coherent, README accurate, code/tests quality.

We evaluate the output, not the process — TDD ordering and which phase fired aren't checked; a correct,
well-tested, convention-following result is the bar (a process slip that matters shows up in the output
anyway). The pipeline run is single per fixture (only the judge iterates); cost and tokens are tracked
per run from the JSON.

## Fixtures

Start with **one minimal skill fixture**; add iteratively. Each fixture = input task (+ seed repo if
needed) + assertions (deterministic + rubric) + which skill/phase it exercises — documented in
`evals.json` + a human-readable companion. Pipeline fixtures (M2) span types: happy-path feature
(e.g. `slugify`), bug-fix, internal-refactor, non-TDD-able, edge-heavy.

## Implementation order

```
M0 (bootstrap) → M1 (skills evals — prove the machinery cheaply) → M2 (pipeline run + artifact checks)
```
