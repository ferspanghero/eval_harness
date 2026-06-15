# v2 Tasks — TOOL1: Productize into `eval-harness`

Mirrors `v2/plan.md`. Prefixes: **GR** generic `--target` runner · **P** path interface (superseded) ·
**V** verify baseline · **RN** rename · **I** init/CLI · **MV** migrate + samples · **PUB** public
release · **E** test scenarios.

Each code phase (GR, P, RN, I) runs `/dev-pipeline` phases 3→4→5 (TDD → review → verify) + the
Phase-6 doc-sync; V, MV, and PUB are verification + file moves + the publish gate.

## Phase GR — Generic `--target` runner (headline; supersedes P)

- [x] **GR1**: CLI `--skill <dir>` → `--target <file>` (append, full path); drop `--skill`/`--skills-root`/discovery; `<dir>`=`file.parent` drives co-located paths; `init --target <file>`
- [x] **GR2**: drop legacy flat-`evals.json` fallback (canonical `<dir>/evals/evals.json` only)
- [x] **GR3**: runner model B — read `--target` content → system prompt (+ eval directive); empty `.claude/` marker; no copy; remove `_build_invocation`/`_find_claude_dir`/`claude_dir`/`RunResult.transcript_path`
- [x] **GR4**: remove `SkillInvoked` + `transcript.py` + REGISTRY entry + `RunArtifacts.transcript` + `cli._read_transcript`
- [x] **GR5**: strip `skill_invoked` checks from the 6 `~/.claude/skills/*` evals + `samples/sample-skill`; drop the `test_fixtures` skill-invocation guard
- [x] **GR6**: live smoke — target a moved `SKILL.md`, confirm B runs in place + file-producing target writes into the isolated workspace
- [x] **GR7**: doc-sync (README + CLAUDE.md) to the `--target` model

## Phase P — `--skill` path interface

- [x] **P1**: replace `--fixture` with `--skill` (`append`, default cwd `.`), repeatable; path relative-or-absolute
- [x] **P2**: `_load_fixture` resolves `<dir>/evals/evals.json` with legacy fallback `<dir>/evals.json`
- [x] **P3**: rename `--fixtures-root` → `--skills-root`; discovery accepts either `evals/evals.json` or `evals.json`
- [x] **P4**: ground-truth default `<skill>/ground_truth/<name>.jsonl` for `calibrate`; `--ground-truth` still overrides
- [x] **P5**: `--out DIR`; default `<skill>/eval-runs` (single skill) else `./eval-runs`; feeds run/regrade/analyze
- [x] **P6**: keep one combined `results.json` for multi-skill sweeps (preserve `_combine`); record the per-skill-split deferral in a code comment

## Phase V0 — Verify baseline (cheapest config, nothing moved)

- [x] **V1**: `run deterministic --skill fixtures/<t>` (+ `--skills-root fixtures`) green, matching the old `--fixture` run → **E12**
- [x] **V2**: judged path — *folded* into GR6's live smoke + the judge unit suite (GR superseded `--skill`)
- [x] **V3**: calibrate path — *folded* into the calibrate unit suite + the convention-default test (GR superseded `--skill`)
- [x] **V4**: GATE — confirm parity before any rename/move

## Phase RN — Rename to `eval_harness`

- [x] **RN1**: `git mv src/pipeline_eval → src/eval_harness`; rewrite all `from pipeline_eval…` imports
- [x] **RN2**: `pyproject.toml` — `name` → `eval-harness`; console script `eval-harness = "eval_harness.cli:main"`
- [x] **RN3**: `cli.py` — `prog`, logger name `eval_harness`, `DEFAULT_RUNS_ROOT` temp name `eval-harness-runs`
- [x] **RN4**: tests — imports + any `prog`/logger-name assertions; full suite green → **E11**
- [x] **RN5**: repo dir `dev-pipeline-v2` → `eval-harness` (filesystem `mv`, last); update project path in `CLAUDE.md` + memory

## Phase I — `init` + `run` CLI restructure

- [x] **I1**: argparse subparsers `init` | `run <mode>`; today's `main` body becomes the `run` handler
- [x] **I2**: deterministic `init` in `cli.py` (no LLM/network), idempotent (never clobber)
- [x] **I2a**: scaffold `evals/evals.json` — one schema-valid example eval (`Fixture.from_json`-loadable) → **E8**
- [x] **I2b**: scaffold `evals/README.md` (field reference), `ground_truth/<name>.jsonl` (empty), `.gitignore` (`eval-runs/`) → **E7**
- [x] **I3**: `run` guard — no `evals/` (nor legacy `evals.json`) → "run init first", exit 2 → **E9**

## Phase MV — Migrate assets out + keep sample self-test

- [x] **MV1**: move `fixtures/<t>/*` → `<skill>/evals/`, `calibration/<skill>.jsonl` → `<skill>/ground_truth/` in `~/.claude/skills` (cross-repo)
- [x] **MV2**: keep one self-contained `samples/<skill>/` (sample `SKILL.md` + evals + ground_truth) as the self-test **and** public worked example (→ PUB3)
- [x] **MV3**: re-test cheapest config — moved locations (**E13**) + retained sample (**E14**)

## Phase PUB — Public release prep (last, one-way; tag a backup first)

- [x] **PUB1**: `rm -rf .git` → `git init` (main) → `.gitignore` in place → `git add -A` → one clean commit recommitting the whole project (working tree unchanged); verify single root commit, no old objects, no tags; publish to a **fresh** GitHub repo
- [x] **PUB2**: run `prepare-public-repo` (security scan + README + LICENSE + examples + `.gitignore` audit); address findings
- [x] **PUB3**: confirm `samples/<skill>/` worked example is complete + runs green on a fresh clone (deterministic tier)
- [x] **PUB4**: secrets sweep — `git grep` key/token patterns, no `.env`/credentials/home-paths/transcripts tracked, `.gitignore` covers `evaluations/`/`eval-runs/`/workspaces/`.venv` → **E15**

## Test Scenarios (from plan)

- [x] **E1**: `--target` resolves the sibling `<dir>/evals/evals.json`  *(GR: superseded the P-era dir form)*
- [x] **E2**: no sibling `evals/` for a target → clean "run init" error  *(GR: legacy fallback dropped)*
- [x] **E3**: missing `--target`, or named target file not found → clean error  *(GR: cwd default dropped)*
- [x] **E4**: multi-`--target` run, declaration-order regroup
- [x] **E5**: single-target results → `<dir>/eval-runs/<stamp>/` beside the target
- [x] **E6**: `--out` override (run + regrade + analyze)
- [x] **E7**: `init` scaffold contents, no LLM/network, idempotent
- [x] **E8**: scaffolded `evals.json` is schema-valid
- [x] **E9**: missing-evals guard → "run init first", exit 2
- [x] **E10**: ground-truth convention default + explicit override
- [x] **E11**: rename smoke (import, `--help`, green suite, no `pipeline_eval` left)
- [x] **E12**: V0 baseline parity (old `--fixture` vs new `--skill`)
- [x] **E13**: post-move parity
- [x] **E14**: sample self-test green after move
- [x] **E15**: secrets/sensitive-data sweep clean (keys, `.env`, home paths, transcripts; `.gitignore` audit)
- [x] **E16**: fresh-clone self-test green on the squashed repo (no old skill content in history)
