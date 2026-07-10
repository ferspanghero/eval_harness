# eval-harness

Behavioral eval harness for prompts/instruction files (a Claude Code skill, a workflow, or any
prompt file) — point it at the file under test by path
(`--target FILE`, e.g. a `SKILL.md`), it runs that file **in place** (the file's content becomes the
run's system prompt — execution model B, nothing copied in) on co-located fixtures and grades the
produced artifacts (deterministic checks + LLM judge) to catch regressions. The bundled `samples/`
worked example is also the self-test.

Read `README.md` for setup/usage and `project_files/v1/plan.md` (rationale in `project_files/research.md`)
for the full architecture and milestone plan — don't duplicate those here.

## Commands

| Command | Purpose |
|---|---|
| `uv sync` | set up / refresh the environment |
| `uv run pytest` | tests + branch coverage |
| `uv run mypy` | strict typecheck |
| `uv run ruff check` | lint |
| `uv run eval-harness init --target FILE` | scaffold a target's sibling `evals/` + `ground_truth/` (deterministic, no LLM) |
| `uv run eval-harness run <mode> --target FILE` | run the harness — `deterministic` / `judge` / `analyze` / `all` / `calibrate` / `regrade` |

## Structure

- `src/eval_harness/` — the harness (cli, runner, deterministic, judge, benchmark, analyzer, the `llm` seam, schemas; `prompts/`).
- `samples/<skill>/` — a self-contained worked example (`SKILL.md` + `evals/` + `ground_truth/`); the in-repo self-test. The harness is **decoupled** — point `--target` at any file's path.
- `.claude/skills/` — the **vendored eval-authoring toolkit** (`generate-evals`, `generate-ground-truth`, `test-prompt`), each a self-contained skill carrying its own `evals/` + `ground_truth/`. Ships in-repo so the harness depends on **no external/global skills**; each skill also doubles as an additional harness target alongside `samples/`.
- `project_files/` — research, and versioned `vN/plan.md` + `tasks.md`.
- A target's `<dir>/eval-runs/` (beside the target file) and run workspaces are gitignored artifacts.

## Conventions & gotchas

- **Run everything via `uv run`** (uv + hatchling + ruff + mypy + pytest). Don't `pip install`.
- **Never install tooling without explicit approval** — surface the choice and ask.
- **Eval run workspaces must live OUTSIDE the repo.** `claude -p` resolves a run's project root from the nearest ancestor `.git`/`.claude`, so an in-repo workspace makes file-producing targets write into *this* repo. The runner gives each workspace an **empty `.claude` marker** (model B copies nothing in); don't move runs back in-repo.
- The harness shells out to **headless `claude -p`** via the `llm` seam (Claude subscription) — `claude` must be logged in.
- TDD; ruff + mypy + the branch-coverage gate must pass before claiming done.
- Mock only the external boundary — the `claude -p` subprocess, centralized in the **`llm` seam**. Every LLM call routes through `llm.call` (judge/analyzer via `call_json`; the runner adds agentic flags via `LLMRequest.extra_args` + `cwd`/`env`). `llm`'s public surface is `call`/`call_json` + the data types; the subprocess (`_default_runner`) is private. Inject at the right layer: runner/judge/analyzer take `call`/`call_json`; the cli takes `run_fn`/`grade_fn`; `test_llm` fakes the subprocess via `llm.call`'s `runner` param.
- **Deterministic tier is an open/closed check library.** Add a check type by dropping a `Check` subclass under `deterministic/checks/` and adding one line to the `REGISTRY` table in `checks/__init__.py` — both edits in plain sight (no import-time self-registration); the `parse_check` dispatch and the iterator (`checks_runner.py`) need no edits. Every check reads one `RunArtifacts` bundle (response / workspace / fixture_dir) and returns a `CheckResult`. **Execution checks** (running the produced code — tests/coverage/typecheck/lint/held-out acceptance) run *fixture-declared* commands as their own subprocesses (`shlex.split`, no shell, with a timeout) in the produced workspace — **not** via the `llm` seam, and tested with real trivial commands, not mocked. Executing produced code shares the agent-run trust boundary (out-of-repo workspace; OS sandbox deferred — SEC1).

## Domain operating rules

- **Flow:** runner → deterministic → (gated) judge → benchmark. `all` runs the judge only when the deterministic tier passes. The agent run + deterministic checks happen once; only the judge repeats (`--judges N`, per-expectation majority; default 3).
- **Target selection (execution model B):** `--target FILE` (repeatable; the file under test, named by full path — no default, no discovery) reads the sibling `<dir>/evals/evals.json` (`<dir>` = the file's parent). The target file's **content becomes the run's system prompt** — the runner copies nothing in; the workspace gets an empty `.claude` marker for project-root isolation. `--eval ID|NAME …` narrows to specific eval(s). One target = one `evals.json` with an `evals[]` array. (A multi-skill *workflow* like dev-pipeline degrades under B — only its own text is in context, not leaf skills.)
- **Concurrent sweep:** evals run on a bounded worker pool (`--concurrency N`, default `min(8, cpu count)`); judge calls stay serial within an eval, so N is also the max concurrent `claude` processes. Results regroup in declaration order — identical output regardless of completion order; log lines are prefixed `target/eval` since they interleave. The first failed eval chain trips an abort: pending evals are skipped, in-flight ones drain, exit 2.
- **Cheap iteration:** `regrade` re-judges the latest run's saved record against the *current* expectations — judge calls only, **no agent runs** — so tuning expectation wording is near-free. The per-eval audit record (final message + persisted output-file content) reconstructs exactly what the original judge saw, so regrading is faithful for file-producing skills too; runs saved before the audit record fall back to the inline final message. Pair with `--eval` to target one case.
- **Unified model/effort:** one `--model` (default `claude-opus-4-8`) and one `--effort` (default `xhigh`; `low`/`medium`/`high`/`xhigh`/`max`) drive **every** LLM call — the run, the judge, and the analyzer. Defaults live in `cli.py`, applied only as argparse `default=`; both are required `str` below the CLI and always passed to `claude -p` (never `None`). All three route through the one `llm.call` (the runner included — it adds its agentic flags via `LLMRequest.extra_args`). Split model/effort per consumer later if needed.
- **Judge isolation:** `--setting-sources project,local`. Do **not** use `--bare` — it strips the login credential ("Not logged in").
- **A failed judge call** counts as a single **logged fail vote** in the per-expectation majority — `judge.grade` is the one place this is handled (callers don't catch it): one bad judge can't sink a vote the others carry, and a total outage just fails the item rather than aborting a sweep or calibration. The `judge error` evidence + the WARNING log distinguish an internal/grader failure from a real judge-assessed fail.
- **Results** are written under `<dir>/eval-runs/<timestamp>/` (beside a single target; else `./eval-runs/`, overridable `--out`): a summary `results.json` plus a per-eval **audit record** (layout in the README) — every judge's raw ballot, dissent included, plus copied transcripts. Every `llm` call runs under a pinned `--session-id` so even a failed grader call is locatable. `analyze` and `regrade` read the latest under that results root; the analyzer stays a deny-all single pass over the summary only. Exit codes: `0` pass / `1` evals failed / `2` hard failure.

## Authoring & re-validating evals

The eval suites and ground truth this harness runs are authored by the **vendored skills** under
`.claude/skills/` — no external/global skills required:

- **`generate-evals`** (`create` / `analyze`) authors or audits a target's sibling `evals/evals.json`
  (deterministic checks + judge expectations) and its fixtures. Assertions derive from the target's
  **confirmed contract**, never its observed output.
- **`generate-ground-truth`** authors the judge-calibration ground truth (see below).
- **`test-prompt`** is the orchestrator: **after any target changes** — a prompt/instruction file,
  *including these skills themselves* — re-validate it with `/test-prompt <target>` → baseline
  `run all` → coverage gaps via `generate-evals` → recalibrate via `generate-ground-truth` only if a
  new judge-expectation *type* appeared → iterate fixtures to green. It drives the harness (`uv run
  eval-harness`) and never commits.

## Judge calibration

To author or extend a target's ground truth, use the **`generate-ground-truth`** skill. Then test judge-vs-human agreement with `uv run eval-harness run calibrate --target FILE` (defaults to the sibling `<dir>/ground_truth/<dir-name>.jsonl`) or an explicit `--ground-truth FILE …`.
