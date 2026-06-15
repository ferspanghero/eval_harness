# v2 Implementation Plan — TOOL1: Productize into `eval-harness`

> Companion to `ideas.md` (the post-v1 parking lot). This plan commits **only** to TOOL1 —
> turning the dev-pipeline-specific harness into a reusable, installable `eval-harness` CLI
> pointed at any skill's co-located eval assets by path. The orchestrator (`ideas.md §1`) and the
> measurement upgrades (`§5`) stay parked.

## Goal

Make the harness a **standalone, decoupled tool** that can evaluate **any prompt/instruction file in
any location**. You point it at the file under test by full path (`--target <file>`, e.g. a
`SKILL.md`); the harness runs it **in place** — the file's content becomes the run's **system
prompt** (execution model **B**), nothing is copied into the harness — and reads/writes the file's
**co-located** assets: `<dir>/evals/evals.json`, `<dir>/ground_truth/`, results to `<dir>/eval-runs/`
(`<dir>` = the target file's parent). Repeat `--target` for several. Rename the project
`pipeline-eval` → `eval-harness`; move the dev-pipeline assets out to their skills and keep a curated
**sample** in-repo as the tool's own self-test.

**The generic runner is the headline.** The earlier `--skill <dir>` path interface (Phase P) was a
half-measure — it located *evals* but the runner still injected the skill *code* from a hardcoded
`.claude/`. Phase **GR** replaces it: the runner sources the instruction-under-test from `--target`
and feeds it as the system prompt, so the harness is genuinely decoupled from any baked-in skills.

End the initiative by **publishing as a public repo** (Phase PUB) — squash history first so nothing
from the verified dev-pipeline skills (now moved to `~/.claude`) leaks via old commits, run
`prepare-public-repo`, ship a worked sample, and sweep for secrets.

**Explicitly out of scope (parked):** CI/CD gate, git-baseline / HEAD-compare, drift/trending,
N-run sampling, and a central eval store. The skill-sourcing version-pinning is git itself (skills
are version-controlled, single author) — no vendored copies, no compare machinery in this initiative.

## User Experience

A single installed binary with **two verbs** — `init` (scaffold) and `run <mode>` (evaluate):

```bash
# install once (later; during dev: `uv run eval-harness …`)
uv tool install eval-harness            # → `eval-harness` on PATH

# --- evaluate a file under test by full path ---
eval-harness run deterministic --target ~/.claude/skills/code-review/SKILL.md
eval-harness run all          --target ~/.claude/skills/code-review/SKILL.md
eval-harness run all          --target ~/.claude/skills/code-review/SKILL.md --eval planted-sql-injection
eval-harness run regrade      --target ~/.claude/skills/code-review/SKILL.md   # re-judge last run, no agent run
eval-harness run calibrate    --target ~/.claude/skills/code-review/SKILL.md   # uses sibling ground_truth/
eval-harness run analyze      --target ~/.claude/skills/code-review/SKILL.md   # diagnosis over latest run

# --- several at once ---
eval-harness run all --target a/SKILL.md --target b/SKILL.md --concurrency 4

# --- new target: scaffold the sibling eval assets → author → run ---
eval-harness init --target path/to/new/SKILL.md   # deterministic; no LLM, no network
#   creates <dir>/evals/{evals.json,README.md}, <dir>/ground_truth/<dir-name>.jsonl, <dir>/.gitignore
eval-harness run all --target path/to/new/SKILL.md
```

**Conventions derived from `--target <file>` (`<dir>` = the file's parent):**

| Asset | Path (convention) | Override |
|---|---|---|
| instructions under test | the `--target` file's content → the run's **system prompt** | — |
| evals | `<dir>/evals/evals.json` | — |
| seeds | `<dir>/evals/` (alongside evals.json) | — |
| ground truth | `<dir>/ground_truth/<dir-name>.jsonl` | `--ground-truth FILE …` |
| results | `<dir>/eval-runs/<stamp>/` (single target) · else `./eval-runs/<stamp>/` | `--out DIR` |

**Modes** (under `run`): `deterministic` · `judge` · `all` · `calibrate` · `regrade` · `analyze`.
**Exit codes:** `0` pass / `1` evals failed / `2` hard failure. A target whose sibling `evals/` is
missing errors **"no evals found — run `eval-harness init` first"** (exit 2). No `--skill`,
`--skills-root`, or skill auto-discovery — targets are named explicitly by file path.

## Architecture

```
eval-harness run all --target ~/.claude/skills/code-review/SKILL.md
        │
        ▼
  cli (argparse: init | run <mode>)
        │  _resolve_targets([--target files]) → [Path];  <dir> = file.parent
        │  evals = <dir>/evals/evals.json
        ▼
  runner: isolated workspace (+ empty .claude marker) + seeds
        │  system_prompt = <target file content> (+ eval directive); user prompt = eval task
        │  claude -p, --setting-sources project,local, RUNNER_TOOLS   ← NOTHING copied in
        ▼
  deterministic → (gated) judge → benchmark
        ▼
  results → <dir>/eval-runs/<stamp>/{results.json, evals/<target>/<eval>/…}

eval-harness init --target <file>
        │  deterministic scaffold beside the file (mkdir + templates); no LLM, no network
        ▼
  <dir>/{evals/{evals.json,README.md}, ground_truth/<dir-name>.jsonl, .gitignore}
```

The decoupling: the runner no longer copies a baked `.claude/` — it feeds the `--target` file's text
as the system prompt and runs in an isolated workspace whose only `.claude` is an **empty marker**
(so file-producing targets write into the workspace, not the real tree). Package rename
`pipeline_eval` → `eval_harness` is mechanical.

## Phase GR — Generic `--target` runner (the headline; supersedes P)

The decoupling Phase P only half-delivered. Done **after** MV, via TDD (phases 3→6).

- **CLI:** replace `--skill <dir>` with **`--target <file>`** (`action="append"`, full path to the
  file under test); drop `--skill`, `--skills-root`, and all skill auto-discovery. `<dir>` =
  `file.parent` drives every co-located path (`evals/`, `ground_truth/`, `eval-runs/`). `init` takes
  `--target <file>` too (scaffolds beside it). Drop the legacy flat-`evals.json` fallback (no users
  left post-MV).
- **Runner (execution model B):** stop copying `claude_dir`/`.claude`; read the `--target` file's
  content and pass it as the **system prompt** (joined with any eval directive); user prompt = the
  eval's task. Workspace gets an **empty `.claude/` marker** only (project-root isolation). Remove
  `_build_invocation`, `_find_claude_dir`, the `claude_dir` param, and `RunResult.transcript_path`.
- **Drop `skill_invoked`:** remove the `SkillInvoked` check + `transcript.py`, its `REGISTRY` entry,
  `RunArtifacts.transcript`, and `cli._read_transcript`. Under B the target's text is always in
  context, so "did the skill fire" is moot — output/file/judge checks carry the real signal.
- **Update moved fixtures + sample:** strip `skill_invoked` checks from the six `~/.claude/skills/*`
  evals and `samples/sample-skill`; drop the `test_fixtures` skill-invocation guard.
- **Known limitation:** a multi-skill *workflow* like `dev-pipeline` (its command invokes leaf
  skills) degrades under B — only its own text is in context, not the leaf skills. Acceptable: the
  generic tool tests one instruction file; full-workflow provisioning is out of scope (and explicitly
  not `--claude-dir`).

## Phase P — `--skill` path interface *(superseded by GR — kept for history)*

Make targets path-addressable; **keep current `fixtures/` + `calibration/` in place** (verify before
move). All edits are in `cli.py` (resolution + flags) — `runner`/`deterministic`/`judge` untouched.

- Replace `--fixture` with **`--skill`** (`action="append"`, default `None` → treated as `["."]`):
  repeatable; each value a dir path (relative to cwd or absolute).
- `_load_fixture(dir)` resolves evals as **`<dir>/evals/evals.json`**, falling back to
  **`<dir>/evals.json`** (the current fixture layout — this is what lets Phase V0 verify in place).
- Rename `--fixtures-root` → **`--skills-root`**; discovery accepts a subdir with **either**
  `evals/evals.json` or `evals.json`. Discovery is used only when sweeping a root (e.g. the in-repo
  sample set); the common path is one or more `--skill`.
- **Ground-truth default:** when `run calibrate` gets no `--ground-truth`, default to
  `<skill>/ground_truth/<name>.jsonl` for each `--skill`; an explicit `--ground-truth FILE …` still
  overrides (this is how V0 calibrates against the current `calibration/<skill>.jsonl`).
- **Results location:** add `--out DIR`. Default: exactly one `--skill` → `<skill>/eval-runs`; more
  than one (or a root sweep) → `./eval-runs`. Feeds `run`, `regrade`, and `analyze`
  (`_latest_results_path`) identically.
- **Design call (record in code comment):** multi-skill sweeps still write **one combined**
  `results.json` under the resolved `--out` (today's `_combine` contract preserved); per-skill split
  output is deferred — not needed for the MVP and would change the results schema.

## Phase V0 — Verify baseline (no code; verification only)

Prove the path model runs the **existing** evals, nothing moved. Cheapest config to keep it free/fast.

- `eval-harness run deterministic --skill fixtures/code-review` (and the other in-repo fixtures via
  `--skills-root fixtures`) → deterministic tier green, matching the pre-change `--fixture` run.
- One cheap judged smoke (`run all --skill fixtures/code-review --model claude-haiku-4-5-20251001
  --effort low --judges 1 --eval <one>`) to exercise the judged path end-to-end through `--skill`.
- `run calibrate --skill fixtures/code-review --ground-truth calibration/code-review.jsonl` resolves
  and grades (cheapest grader) — proves the calibrate path under the new flag.
- **Gate:** parity with the current interface (E12). Only after green do we rename/move.

## Phase RN — Rename to `eval_harness`

Behavior-preserving refactor; verify green at each step (no functional change).

- `src/pipeline_eval/` → `src/eval_harness/` (`git mv`); all `from pipeline_eval…` imports → `eval_harness`.
- `pyproject.toml`: `name = "pipeline-eval"` → `"eval-harness"`; `[project.scripts]`
  `evaluate = "pipeline_eval.cli:main"` → `eval-harness = "eval_harness.cli:main"`.
- `cli.py`: `prog="evaluate"` → `"eval-harness"`; logger `getLogger("pipeline_eval")` →
  `"eval_harness"`; `DEFAULT_RUNS_ROOT` temp name `pipeline-eval-runs` → `eval-harness-runs`.
- Tests: imports + any `prog`/logger-name assertions.
- Docs: `CLAUDE.md` + `README.md` package/command references (Phase 6 doc-sync handles drift).
- Repo dir `dev-pipeline-v2` → `eval-harness` is a filesystem `mv` of the project root (done last,
  outside the code edits; update the project path in `CLAUDE.md` + memory accordingly).

## Phase I — `init` + `run` CLI restructure

Add the scaffold verb and split the surface into `init` | `run <mode>` (built under the new name).

- argparse subparsers: **`init`** (`--skill <dir>` default `.`) and **`run`** (`mode` positional +
  all current run/judge/calibrate/regrade/analyze flags). Today's `main` body becomes the `run` handler.
- **`init` (deterministic, no LLM, no network):** create, idempotently (never clobber an existing file):
  - `<dir>/evals/evals.json` — **one schema-valid example eval** (placeholder `target`, one `checks`
    entry, one `{text, gate}` expectation) that loads via `Fixture.from_json`.
  - `<dir>/evals/README.md` — field reference (mirrors today's fixture companion READMEs).
  - `<dir>/ground_truth/<name>.jsonl` — empty file.
  - `<dir>/.gitignore` — contains `eval-runs/`.
  - `<name>` derives from the dir's basename.
- **`run` guard:** a dir with neither `evals/evals.json` nor `evals.json` → error "no evals found — run
  `eval-harness init` first" (exit 2), not a stack trace.

## Phase MV — Migrate assets out + keep sample self-test

Relocate the dev-pipeline-specific assets to their skills (new convention), keep a curated in-repo
sample set so the harness's own test suite still runs end-to-end.

- Move `fixtures/<t>/*` → `<skill>/evals/` and `calibration/<skill>.jsonl` →
  `<skill>/ground_truth/<skill>.jsonl` in `~/.claude/skills/` (a **separate repo** — cross-repo move).
- **Keep one minimal, self-contained sample** under `samples/<skill>/` — a tiny sample `SKILL.md`
  (the thing under test) + `evals/evals.json` + `ground_truth/<skill>.jsonl` — as both the tool's
  e2e self-test (the harness's tests point at it) and the public worked example (PUB3). The
  deterministic tier runs it without credentials; the judged path is documented, not run in CI.
- Re-test at cheapest config against the moved locations (E13) and the retained sample (E14).
- Note: this phase crosses into the global skills repo; sequence it **last**, after RN + I are green.

## Phase PUB — Public release prep (the publish gate)

The initiative ends by making the repo public. Sequenced **last**, after MV is green. One-way steps
(history rewrite) — tag a backup first.

- **PUB1 — Re-initialize history:** the dev-pipeline skills/seeds/transcripts were exercised in-repo
  (V0) and now live in `~/.claude` (MV); drop **all** prior history so none of that private content is
  recoverable. Cleaner than squashing (no leftover reflogs / unreachable blobs / stale tags). **No
  backup, no tags** — the working tree is unchanged, only `.git` is replaced: `rm -rf .git` →
  `git init` (branch `main`) → confirm `.gitignore` is in place (so `evaluations/`, `eval-runs/`,
  workspaces, `.venv`, caches aren't staged) → `git add -A` → one clean commit recommitting the whole
  project. Verify `git log` is a single root commit and `git rev-list --all --objects` carries nothing
  from the old history. When publishing, push to a **fresh** GitHub repo — never force-push over an
  existing public one (remotes/forks keep old commits).
- **PUB2 — `prepare-public-repo`:** run the skill — security scan, README review, LICENSE check
  (add one if absent), examples, and `.gitignore` audit. Address its findings before going public.
- **PUB3 — Sample reference:** the `samples/<skill>/` worked example from MV2 (sample SKILL + evals +
  ground_truth) is the public reference and the deterministic self-test; confirm it's complete and
  runs green on a fresh clone.
- **PUB4 — Secrets sweep (belt-and-suspenders over PUB2):** triple-check nothing sensitive is tracked
  — `git grep -nIE` for key/token patterns (`sk-`, `ANTHROPIC`, `api[_-]?key`, `Bearer`, `BEGIN .*
  PRIVATE KEY`), no `.env`/credentials committed, no absolute home paths or `~/.claude` transcript
  dumps, and `.gitignore` covers `evaluations/`, `eval-runs/`, run workspaces, `.venv`, caches.

## File Manifest

```
project_files/v2/
  plan.md                    ← this file (new)
  tasks.md                   ← new
  ideas.md                   ← unchanged (parking lot)

src/eval_harness/            ← renamed from src/pipeline_eval/ (Phase RN)
  cli.py                     ← --skill/--skills-root/--out, ground-truth default, init|run subparsers
  scaffold.py                ← NEW: deterministic `init` templates (Phase I)
  (runner.py, deterministic/, judge.py, benchmark.py, analyzer.py, llm.py, schemas.py, prompts/: unchanged)

pyproject.toml               ← name + console-script rename (Phase RN)
samples/<skill>/             ← self-contained worked example (Phase MV) — self-test + public ref (PUB3)
  SKILL.md
  evals/evals.json
  ground_truth/<skill>.jsonl
fixtures/, calibration/      ← emptied/removed after Phase MV (moved to ~/.claude/skills)
LICENSE                      ← added/confirmed by prepare-public-repo (Phase PUB)
CLAUDE.md, README.md         ← doc-sync (Phase 6)

(filesystem) dev-pipeline-v2/ → eval-harness/   ← repo dir mv (Phase RN, last)
(git)        .git re-initialized — single fresh commit, no prior history ← Phase PUB
```

## Verification

Run after **every** code phase (P, RN, I) and at the end of MV:

1. `uv run pytest` — tests + branch-coverage gate.
2. `uv run mypy` — strict typecheck.
3. `uv run ruff check` — lint.
4. `security-audit` in diff mode over the change.
5. Phase-specific live check (cheapest config): V0 baseline parity; post-RN `eval-harness --help`
   + a smoke `run deterministic`; post-I an `init` into a temp dir + `run` on it; post-MV the
   retained sample runs green.

Publish gate (Phase PUB):

6. `.git` re-initialized (working tree unchanged) → `git log` is a single root commit, no old objects
   reachable.
7. `prepare-public-repo` run with findings addressed; `LICENSE` present.
8. Secrets sweep clean (E15); a **fresh clone** builds and the sample's deterministic tier runs
   green (E16).

## Test Scenarios

> Phase **GR** moved E1–E10 to the `--target <file>` model (legacy fallback + cwd-default dropped);
> E12–E14 below are V0/MV history. E1–E5 are restated for the current model.

- **E1** — sibling resolution: `--target <file>` with `<dir>/evals/evals.json` beside it → loads and runs it.
- **E2** — no sibling evals: `--target <file>` with no `<dir>/evals/` → clean "run init" error (exit 2).
- **E3** — missing `--target` (none given, or the named file absent) → clean error.
- **E4** — multi-target: two `--target` files → both run; outcomes regroup in declaration order.
- **E5** — output co-location: a single-target run writes `<dir>/eval-runs/<stamp>/results.json` beside the target.
- **E6** — `--out` override: results land in the named dir; `regrade`/`analyze` read latest there.
- **E7** — `init` scaffold: empty dir → `evals/{evals.json,README.md}`, `ground_truth/<name>.jsonl`,
  `.gitignore` (contains `eval-runs/`); no LLM, no network; idempotent (won't clobber).
- **E8** — `init` output is schema-valid: the scaffolded `evals.json` loads via `Fixture.from_json`.
- **E9** — missing evals guard: `run` on a dir with no evals → "run init first", exit 2.
- **E10** — ground-truth default: `run calibrate` with no `--ground-truth`, skill has
  `ground_truth/<name>.jsonl` → uses it; explicit `--ground-truth` still overrides.
- **E11** — rename smoke: `from eval_harness.cli import main` imports; `eval-harness --help` works;
  pytest/mypy/ruff green; no `pipeline_eval` references remain.
- **E12** — baseline parity (the V0 gate): existing in-repo fixtures via `--skill` at cheapest config
  produce the same pass/fail as the pre-change `--fixture` interface.
- **E13** — post-move parity: after MV, `run` against `<skill>/evals` + `<skill>/ground_truth`
  reproduces the V0 verdict.
- **E14** — sample self-test: after real assets move out, the retained in-repo sample runs green.
- **E15** — secrets clean: `git grep` for key/token patterns + a `.gitignore` audit find nothing
  sensitive tracked (no keys, `.env`, credentials, home paths, transcript dumps).
- **E16** — fresh-clone self-test: cloning the re-initialized repo, `uv sync`, and the sample's
  deterministic tier run green — with no old skill content reachable in history.

## Implementation Order

```
P (--skill path interface)            ← code, TDD
   → V0 (verify baseline, E12)        ← verification only, cheapest config — GATE
      → RN (rename → eval_harness)    ← mechanical, behavior-preserving, verify green
         → I (init + run subcommands) ← code, TDD (built under the new name)
            → MV (move assets + sample self-test, E13/E14)   ← cross-repo, last
               → PUB (re-init git → prepare-public-repo → sample → secrets sweep, E15/E16) ← publish, one-way
```
(P and the V0 gate are the critical path; nothing is parallelizable — each phase gates the next.)
