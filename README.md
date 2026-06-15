# eval-harness

A behavioral regression harness for prompts and instruction files — a Claude Code skill, a workflow,
or any prompt file. You point it at the file under
test by full path (`--target FILE`, e.g. a `SKILL.md`); it runs that file **in place** — the file's
content becomes the run's system prompt — on co-located coding fixtures and grades the **produced
artifacts**, so you catch when an edit makes the skill *worse*, not merely different. It asserts on
output (like unit tests), never on the prompt source, and is decoupled from any baked-in skills. The
bundled sample fixtures exercise the customized **`/dev-pipeline`** workflow skills.

## How It Works

```
runner (claude -p, isolated workspace) → deterministic checks → judge (gated) → benchmark
```

1. **Runner** — runs the **target file in place** headless via `claude -p` in an isolated, out-of-repo
   workspace: the file's content becomes the run's system prompt (execution model B — nothing is
   copied into the harness), seed files are placed alongside, and an empty `.claude` marker isolates
   file writes. Captures the output. An optional per-eval directive (e.g. for a full-pipeline run) is
   appended to the system prompt.
2. **Deterministic tier** — objective, no-LLM checks on the produced artifacts — its response text
   and produced files — plus **executing the produced code** (e.g. its tests, coverage, typecheck,
   lint, and a held-out acceptance test). No LLM; runs first.
3. **Judge tier** — an LLM grades fuzzy rubric expectations. In `all` mode it runs **only when the
   deterministic tier passes** (fail-fast gate). `--judges N` repeats the judge and takes a
   per-expectation majority.
4. **Benchmark** — aggregates pass-rate, cost (run + judge spend, split out), tokens, and duration
   into a summary `results.json`, alongside a per-eval **audit record** (`evals/<target>/<eval>/`):
   the final message, the judged output-file content, every judge's raw ballot (votes, evidence,
   session id, spend), and a copy of each judge's transcript.
5. **Analyzer** *(optional)* — one read-only LLM pass over the latest benchmark, emitting
   severity-tagged observations.

LLM calls go through a thin `llm` seam over headless `claude -p` (reusing a Claude subscription, no
separate API billing). See `project_files/research.md` for *why* this design, and
`project_files/v1/plan.md` for the build.

Separately, **`eval-harness run calibrate`** validates the *judge itself*: it re-grades a set of
frozen, human-labeled cases (per-target `.jsonl`, defaulting to `<dir>/ground_truth/<dir-name>.jsonl`
beside the target, overridable with `--ground-truth`) and reports judge-vs-human agreement — so the
judge's verdicts can be trusted, and a grader-prompt change can be regression-tested.

## Assertion tiers

| Tier | What it checks | Cost |
|---|---|---|
| Deterministic | objective facts about artifacts (substring/file checks) and tool verdicts on the produced code (tests, coverage, typecheck, lint, a held-out acceptance test) | no LLM |
| Judge | subjective residue (severity, correctness, no hallucinated findings) | LLM — 3-judge majority by default (Opus + `xhigh`; all configurable) |

The deterministic tier **gates** the judge in `all` mode — no judge tokens are spent on output that's
already objectively broken.

## CLI

Two verbs — `init` (scaffold) and `run <mode>` (evaluate):

```
eval-harness init --target FILE              # scaffold evals/ + ground_truth/ + .gitignore beside FILE (no LLM)
eval-harness run deterministic --target FILE # run the target + objective checks (no judge)
eval-harness run judge --target FILE         # run + checks + judge (ungated)
eval-harness run all --target FILE           # run + checks; judge only if deterministic passes
eval-harness run analyze --target FILE       # LLM pass over the latest benchmark → observations
eval-harness run regrade --target FILE       # re-judge the latest run's saved outputs (no agent runs)
eval-harness run calibrate --target FILE     # re-grade frozen cases → judge-vs-human agreement
```

**Targeting & modes:**

- `--target FILE` — the file under test, named by full path; repeatable to sweep several. Reads the
  sibling `<dir>/evals/evals.json` (`<dir>` = the file's parent).
- `--eval ID|NAME` — narrow a `run` or `regrade` to specific eval(s).
- `init` scaffolds the sibling `evals/` + `ground_truth/` deterministically (no LLM); a `run` against
  a target with no evals points you at `init`.
- `regrade` re-judges the previous run's saved record — the exact final message and output files the
  judge saw — against the *current* expectations. Judge calls only, no agent runs, so iterating on
  expectation wording is near-free.

**Flags:**

| Flag | Effect | Default |
|---|---|---|
| `--judges N` | judge votes per expectation (majority vote) | `3` |
| `--concurrency N` | evals running at once in a sweep — one `claude` process each, results order-independent | `min(8, cpu count)` |
| `--effort <level>` | reasoning effort for every LLM call — run, judge, analyzer (`low`/`medium`/`high`/`xhigh`/`max`) | `xhigh` |
| `--model <id>` | model for every LLM call — run, judge, analyzer | `claude-opus-4-8` |
| `--out DIR` | where results are written | `<dir>/eval-runs/` beside the target (gitignored) |

**Exit codes:** `0` pass · `1` evals failed · `2` hard failure.

## Fixtures

A target's eval assets co-locate beside it: `<dir>/evals/evals.json` (task + deterministic checks +
judge expectations) plus seed files and a companion `README.md`, and `<dir>/ground_truth/<dir-name>.jsonl`
for judge calibration (`<dir>` = the target file's parent). `eval-harness init` scaffolds this
layout. Fixtures may plant a known defect (e.g. a SQL injection) so grading has objective ground
truth; a full-pipeline fixture ships a held-out acceptance test run against the produced code. The
bundled worked example lives under `samples/` — see that directory.

## Eval format (`evals.json`)

A target's evals live in `<dir>/evals/evals.json` (next to the target file). One file is a single
JSON object — a `target` label plus an `evals` array:

```json
{
  "target": "code-review",
  "system_prompt": "Optional run directive appended after the target file's content.",
  "evals": [
    {
      "id": 1,
      "name": "planted-sql-injection",
      "prompt": "Review app.py for issues.",
      "files": ["files/app.py"],
      "checks": [
        { "type": "response_contains", "description": "names the class", "value": "SQL injection" }
      ],
      "expectations": [
        { "text": "Identifies the SQL injection and names the vulnerability class.", "gate": "unanimous" }
      ],
      "output_files": []
    }
  ]
}
```

**Eval fields:** `id` + `name` (stable identifiers — `--eval` selects by either) · `prompt` (the task
handed to the target) · `files` (seed files copied into the run workspace; paths relative to
`evals/`, a `files/` prefix is stripped) · `checks` (deterministic) · `expectations` (judge) ·
`output_files` (optional — produced files whose content the judge reads, and which `regrade` replays).

**Deterministic checks** (objective, no LLM; run first and gate the judge in `all` mode):

| `type` | Asserts | Key fields |
|---|---|---|
| `response_contains` | the final message contains a substring (case-insensitive) | `value` |
| `response_contains_any` | the final message contains **any** of several substrings | `values` |
| `file_exists` | a workspace-relative file was produced | `path` |
| `file_contains` | a produced file contains a substring | `path`, `value` |
| `command_succeeds` | a declared command exits 0 in the workspace (e.g. `pytest`, `mypy`, `ruff`) | `command` |
| `coverage_at_least` | coverage.py `TOTAL` ≥ a threshold | `command`, `minimum` |
| `acceptance_test` | a held-out test (copied in post-run) passes against the produced code | `source`, `command` |

Every check also takes a `description`. Execution checks (`command_succeeds`/`coverage_at_least`/
`acceptance_test`) run fixture-declared commands as their own subprocesses (`shlex.split`, no shell,
timed) — not via the LLM.

**Expectations** are judge-graded, each `{ "text": ..., "gate": "majority" | "unanimous" }`:
`majority` passes on ≥ majority of the `--judges`; `unanimous` requires all (use it for high-stakes
rubric items). Vote-strength colour (🟢 unanimous / 🟡 majority / 🔴 below) is reported orthogonally.

`eval-harness init --target <FILE>` scaffolds a schema-valid starter `evals.json` to edit.

## Getting Started

### Prerequisites

- [`uv`](https://docs.astral.sh/uv/) for environment + dependency management.
- The `claude` CLI, logged in — the harness shells out to headless `claude -p`.

### Setup

```bash
uv sync
```

### Usage

```bash
S=samples/sample-skill/SKILL.md                              # a target file under test
uv run eval-harness init --target path/to/new/SKILL.md       # scaffold the sibling eval assets (no LLM)
uv run eval-harness run all "--target=$S"                    # run + gated judge
uv run eval-harness run deterministic "--target=$S" --model claude-haiku-4-5-20251001
uv run eval-harness run all "--target=$S" --judges 1         # single judge: skips the majority vote
uv run eval-harness run all --target a/SKILL.md --target b/SKILL.md --concurrency 2   # several at once
uv run eval-harness run all "--target=$S" --model claude-sonnet-4-6 --effort high
uv run eval-harness run analyze "--target=$S"                # observations over the latest benchmark
uv run eval-harness run regrade "--target=$S" --eval 1       # re-grade one eval's saved output (judge only)
uv run eval-harness run calibrate "--target=$S"              # judge-vs-human agreement (sibling ground_truth/)
```

Progress logs go to stdout (timestamped, levelled); the result summary prints there too. Each run
writes a self-contained record under `<dir>/eval-runs/<timestamp>/` beside the target (or wherever
`--out` points):

```
<dir>/eval-runs/<timestamp>/
├── results.json            # summary: verdicts + cited evidence, cost split {run, judges, total},
│                           # and a `detail` pointer per eval
└── evals/<target>/<eval>/  # per-eval audit record
    ├── output.md           # the agent's final message
    ├── files/              # the produced output-file content the judge graded
    ├── ballots.json        # every judge's raw votes + evidence, session id, spend, error
    └── judge-<n>.jsonl     # each judge's transcript, copied in (survives CLI transcript pruning)
```

Any verdict — including a dissenting judge's — is inspectable after the fact: its evidence sits in
`ballots.json`, its full reasoning in the copied transcript. `regrade` re-judges from this record.

## Project Structure

```
.
├── src/eval_harness/    # the harness: cli, runner, deterministic, judge, benchmark, analyzer, calibration, llm seam, schemas
├── samples/<skill>/     # a self-contained worked example: SKILL.md + evals/ + ground_truth/ (also the self-test)
├── project_files/       # research, and versioned plan/tasks
└── tests/               # test suite (mirrors src/)
```

## Tests

```bash
uv run pytest        # tests + branch coverage
uv run mypy          # strict typecheck
uv run ruff check    # lint
```

See `project_files/v1/plan.md` for the milestone plan and `project_files/v1/tasks.md` for status.

## License

[MIT](LICENSE).
