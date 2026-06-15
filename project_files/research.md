# dev-pipeline-v2 — Research & Design

> Design notes for a regression/drift eval harness for the customized `/dev-pipeline`. Captures the
> problem, the landscape we surveyed, the approaches we weighed, and the reasoned choice. The
> implementation plan lives in `v1/plan.md`; deferred ideas in `v2/ideas.md`.

---

## 1. The problem

`/dev-pipeline` is a prompt-orchestrated, multi-skill development workflow: a command that drives a
6-phase process (plan → write docs → TDD implement → code review → verify → publish), delegating each
phase to leaf skills (`project-brainstorm`, `test-driven-development`, `code-review`,
`verification-before-completion`, `create-readme`, …) and shared convention files (`testing.md`,
`python.md`, …).

As this workflow is edited — the command, any leaf skill, or a convention — there is no way to tell
whether a change improved it or quietly **regressed** it. We want a harness that catches regressions
when any part of the pipeline is edited.

The thing under test is **prose interpreted by an LLM**, so it is nondeterministic: the same input
produces different runs. Correctness can't be asserted with `assertEquals` on a transcript — it needs
an evaluation approach built for nondeterministic agent behavior.

---

## 2. The landscape we surveyed

A deep-research pass (23 sources, claims adversarially verified) plus a review of Anthropic's own
tooling surfaced the available techniques:

- **Behavioral evals (Anthropic `skill-creator`).** Anthropic's own answer to "did my skill edit
  regress" is entirely behavioral: draft → run test cases (with-skill **vs baseline / previous
  version**) → **LLM grader** (`passed` + `evidence`) → **benchmark** (pass-rate, tokens, timing) →
  analyze → improve → iterate. No static analysis of the prompt. Stores **no golden outputs** —
  correctness is defined by **assertions** + the **baseline comparison**. [`github.com/anthropics/skills`]
- **Assertion harnesses + CI (`promptfoo`).** The canonical OSS pattern: deterministic assertions +
  model-graded (`llm-rubric`) grading, wired into CI with before/after diffs. [`github.com/promptfoo/promptfoo`]
- **LLM-as-judge.** A viable proxy — strong judges reach ~80–85% human agreement — but biased (~12
  quantifiable bias types: position, verbosity, self-enhancement), with **no judge uniformly reliable
  across tasks** and brittleness to formatting perturbations. [MT-Bench `arxiv.org/abs/2306.05685`;
  CALM `arxiv.org/abs/2410.02736`; position-bias `arxiv.org/html/2406.07791v5`]
- **Structural prompt diffing (`Arbiter`).** A prompt-AST differ with content-independent hashing that
  classifies nodes as added/removed/modified/moved, reformatting-robust — for detecting drift between
  prompt **versions**, plus interference detection across prompt blocks. [`arxiv.org/pdf/2603.08993`]
- **Trajectory / process evaluation.** Metrics like Step Success Rate measure *how* an agent reached a
  result (which steps ran), distinct from final-output checks. [`arxiv.org/html/2507.21504v1`]
- **Multi-agent failure modes (`MAST`).** ~79% of multi-agent failures are coordination / specification
  / verification gaps, not base-model capability — so a multi-skill workflow's risk is in handoffs and
  unenforced gates. [`arxiv.org/pdf/2503.13657`]
- **The gap: golden snapshots for nondeterministic agents.** No source provided a validated
  golden-transcript/snapshot method with stable flakiness thresholds — the weakest-covered area.

---

## 3. Approaches we weighed for *this* problem

The landscape collapses to three candidate strategies for regression-testing the pipeline:

### A. Static structural checking (Arbiter-style AST diff + invariants)
Parse each prompt file to a structural outline, diff it against a blessed baseline, and assert
invariants (refs resolve, every phase has a gate, DoD non-empty).
- **For:** cheap, deterministic, no run; catches mechanical breakage (broken refs, dropped sections) instantly.
- **Against:** it is **snapshot testing on the source** — *every legitimate edit trips the diff*, so you
  re-bless constantly and bless mistakes along with intent; it asserts on the **prompt**, not on what
  the workflow **produces** (the wrong target); and the failures it catches (mechanical breakage) are
  the *least valuable* — a weakened-but-present gate or a subtly wrong instruction sails through.

### B. Semantic drift detection (LLM compares prompt versions)
An LLM diffs old-vs-new prompt text and classifies meaning changes (e.g. a gate weakened from "must"
to "should").
- **For:** meaning-aware; catches subtle weakening a structural diff misses.
- **Against:** still operates on the **prompt text**, not behavior; fires on intended changes; and
  inherits all the LLM-judge reliability caveats.

### C. Behavioral evals (run the workflow, grade the outputs) — `skill-creator`'s approach
Run the skills/command on coding fixtures and grade the **produced artifacts** against a baseline
version.
- **For:** tests what actually matters — does the edited workflow still **produce** good outcomes;
  asserts on **output, like unit tests**; regression = behavior worse than baseline; it is Anthropic's
  own method for exactly this question.
- **Against:** expensive (agent runs), nondeterministic (needs N-run bands + an LLM judge), and harder
  to build than a static checker.

---

## 4. Decision — behavioral evals (C), and why

We chose **C**. The reasons:

1. **We care about behavior, not prompt structure.** The pipeline's value is what it *produces*; a test
   suite asserts on output, not on how the source is written. A and B both inspect the prompt — the
   wrong target.
2. **Static structural checking is self-defeating during editing.** It's snapshot testing: every
   intended edit trips the diff, so you bless through it — which means it blesses the very loss it was
   meant to catch. Low signal for the active-editing workflow that motivates this project.
3. **It catches the least-valuable failures.** Mechanical breakage (a broken ref, a dropped section) is
   real but rare and shallow; a *behaviorally* worse pipeline that still parses cleanly is the failure
   that hurts — and only behavioral evals see it.
4. **Anthropic's own answer is behavioral.** `skill-creator` ensures skill quality through evals, not
   static analysis — strong external validation for C over A/B.

The exploration of A/B was not wasted — several of their ideas survive as **details of the behavioral
design**:
- **deterministic assertions** (A's strength) become the cheap objective tier — but applied to
  *produced artifacts* (run `pytest`, check coverage), not to the prompt source;
- **N-run pass-rate bands** answer the golden-snapshot **gap** for nondeterministic runs;
- **judge pinning + calibration + structured outputs** answer the **LLM-judge reliability** findings.

---

## 5. The chosen design — a behavioral eval harness

**Run the skills (and the command) on coding fixtures, and grade the produced artifacts — assert on
*output*, never on the prompt source — against a baseline version.**

### Reuse from skill-creator
| skill-creator | our use |
|---|---|
| `evals.json` (input + assertions) | fixture format |
| `agents/grader.md` + `grading.json` (`passed`, `evidence`) | the LLM grader |
| `agents/comparator.md` (blind A/B) | regression as "is new worse than baseline" |
| `agents/analyzer.md` | optional cross-case diagnosis |
| `benchmark.json` (pass-rate, tokens, duration) | metrics + baseline comparison |

Not adopted: the `.skill` packaging and `scripts/run_loop` (the latter tunes a skill's *trigger
description* — irrelevant for a command, invoked by name). A fixture's "gold standard" is its
**assertions** + the **baseline version's score** — no stored expected output.

We evaluate the **output**, not the process: a correct, well-tested, convention-following result is the
bar. TDD ordering (test-first vs test-after) and which phase fired aren't checked — process is a means
to correctness, so if the result is good the means don't need policing, and any process slip that
matters surfaces in the output anyway (no tests → coverage fails; README not updated → file check fails).

### Assertion tiers (modeled separately; deterministic gates judge)
- **Deterministic** (objective, cheap): execute against produced artifacts — `pytest`, coverage,
  files-exist, an independent acceptance test. Covers most of the Definition of Done. Runs first and
  **fails fast** — no judge tokens on already-broken output.
- **Judge** (subjective residue): an LLM rubric over artifact content — plan coherent, review-worthy
  issues absent, tests meaningful.

### Conventions
Asserted by their **effect on output**, as ride-along assertion sets on any code-producing eval (no
standalone runs): deterministic canaries (type hints, AAA markers, `--cov-branch`, coverage ≥
threshold, no bare `except`) + judge for the fuzzy rules.

### Stages / CLI
```
runner        → headless run (a skill; the command in M2) → store artifacts + transcript
deterministic → objective assertions on artifacts (no LLM, fail-fast)
judge         → grader + comparator (LLM)
analyzer      → one-pass diagnosis over the benchmark (optional)
benchmark     → aggregate pass-rate / tokens / duration; compare vs baseline version

evaluate deterministic | judge | analyze | all      (all = deterministic → if pass → judge)
```

### Execution
- **LLM transport:** the `llm` seam shelling out to **headless `claude -p`** — reuses the Claude Max
  subscription, no separate API billing, one mechanic for running and grading.
- **Cheap-first:** validate the machinery on cheap single-skill runs before the expensive full-pipeline
  run.
- **Flakiness:** N-run pass-rate bands — a regression must reproduce, not flip once.

---

## 6. Key decisions

| Topic | Decision |
|---|---|
| Approach | behavioral evals (run + grade artifacts), modeled on skill-creator — over static structural checking and semantic drift |
| Baseline | assertions + previous version's score; **no golden outputs** |
| Assertion tiers | deterministic (execute) + judge (rubric); separate; deterministic gates judge |
| Conventions | ride-along assertions on code-producing evals; deterministic canaries + judge |
| LLM transport | headless `claude -p` via the `llm` seam (subscription, no SDK) |
| Build order | skills evals first (cheap), then the full-pipeline run |
| Judge reliability | judge = **Opus, max effort**, pinned + versioned prompt; structured outputs; calibration set after first runs |
| Flakiness | `--judges N` (default 1) iterates **only the judge** (majority); the agent run + deterministic checks run once |
| Run + capture | headless `claude -p` with **`--session-id`** (→ known transcript path) + `--output-format json` (→ cost/tokens); `--permission-mode bypassPermissions` for autonomous runs |
| Project home | `~/dev-pipeline-v2/`, standalone git repo; Python, `src/` + `tests/` layout |

---

## 7. Second research pass — baseline strategy & judge gating (golden vs live re-run)

> A focused deep-research pass (23 sources; 25 claims adversarially verified — 24 confirmed, 1
> refuted) on a question raised during M2: for regression-testing, should we **store a "golden"
> blessed baseline of results** and compare against it, or **re-run the baseline version live** each
> time? And how should the noisy LLM-judge tier be gated?

**Conclusion — the answer splits by tier, and it rejects *both* a naive golden and a default live
re-run:**

| Tier | Verdict | Why |
|---|---|---|
| **Deterministic** (pytest/coverage/mypy/ruff/files) | **No golden needed** — keep explicit absolute assertions; re-run and check they pass | Our checks are explicit assertions, not captured-output snapshots; the snapshot/golden pattern only applies when you *can't* assert correctness directly. A verbatim golden of *agent* output is non-deterministic → would fail every run (the snapshot anti-pattern). |
| **Judge** (subjective expectations) | **Don't freeze a verdict as golden; don't default to a live re-run either.** Keep the absolute majority-vote gate, made noise-aware | A stored judge verdict is a biased, low-reliability single sample (intra-rater Krippendorff α 0.265–0.563). Repeated-sampling/majority + pointwise-absolute scoring + calibration is the recommended treatment. Live paired re-run discriminates subtle quality diffs but is less stable — a niche opt-in. |

Real tools (promptfoo, LangSmith, DeepEval, Braintrust) gate primarily on **absolute per-run
thresholds**; baseline/pairwise comparison is a *separate* feature, and **none stores a raw judge
verdict as a frozen golden**. Anthropic's agent-eval guidance independently argues for grading
produced **output** (not execution paths) and treating per-task success as a **rate** over repeated
trials (pass@k / pass^k).

**Key findings (each adversarially verified 3-0 unless noted):**
- Judge **intra-rater reliability is low** — same judge, same input, different runs → near-arbitrary
  ratings (α 0.265–0.563, all below the 0.8 "good" bar; no improvement up to 10 runs). Majority
  aggregation helps but doesn't eliminate it. [Rating Roulette 2510.27106; McDonald 2412.12509]
- Judge scores are **systematically biased** (imperfect sensitivity/specificity); statistically valid
  reporting needs bias-correction + confidence intervals against a human calibration set.
  [Lee 2511.21140; Chen 2601.05420]
- **Pointwise/absolute** scoring is more stable than **pairwise** (flips 9% vs 35% under distractors);
  pairwise discriminates better but amplifies position/verbosity bias. [Tripathi 2504.14716;
  Comparative Trap 2406.12319]
- **Snapshots require deterministic values**; non-deterministic data must become tolerant invariants,
  not frozen samples. [Jest]
- **Characterization/golden tests assert *observed*, not *correct*, behavior** → a frozen baseline can
  ossify a wrong-but-blessed output. [Characterization test / Feathers]
- Real tools gate on **absolute thresholds**; baseline-compare is a separate opt-in feature.
  [promptfoo, LangSmith]
- **Refuted (0-3):** the "optimally combine a small human set + a large judge-only set" bias-variance
  argument — *not* relied upon.

**Honest limits:** no source directly A/B-tests "stored-pointwise-golden vs live-paired-rerun" on
regression-detection power — the core tradeoff is *reasoned, not measured*; an in-harness experiment
(re-judge N fixtures both ways, measure the false-flip rate) would settle it for our judge. The
9%/35% figure measures *distractor*-robustness, not pure run-to-run reproducibility. Tool flags are
version-sensitive. "Golden judge scores are an anti-pattern" is a *synthesis* of the cited premises
(Jest's determinism requirement + the judge-variance results), not a verbatim claim in any one source.

**Implication for the harness:** the earlier "live baseline double-run" idea (**B2**) largely
dissolves; what replaces it is a smaller **noise-aware judge gate** — tri-state pass/fail/needs-review,
a per-skill variance-sized vote count, and calibration-driven gate-vs-report demotion. See
`v1/tasks.md`.

### References (this pass)

Primary:
- Haldar & Hockenmaier, "Rating Roulette: Self-Inconsistency in LLM-as-a-Judge…", arXiv 2510.27106 (EMNLP 2025 Findings) — `arxiv.org/abs/2510.27106`
- McDonald et al., "…" arXiv 2412.12509 — `arxiv.org/abs/2412.12509`
- Lee et al., "…" arXiv 2511.21140 (ICML 2026) — `arxiv.org/abs/2511.21140`
- Chen et al., "…" arXiv 2601.05420 — `arxiv.org/abs/2601.05420`
- Tripathi et al., "Pairwise or Pointwise?", arXiv 2504.14716 (COLM 2025) — `arxiv.org/abs/2504.14716`
- "The Comparative Trap…", arXiv 2406.12319 — `arxiv.org/html/2406.12319v4`
- Jest — Snapshot Testing — `jestjs.io/docs/snapshot-testing`
- Characterization test (→ Feathers, *Working Effectively with Legacy Code*) — `en.wikipedia.org/wiki/Characterization_test`
- Anthropic, "Demystifying evals for AI agents" (Jan 2026) — `anthropic.com/engineering/demystifying-evals-for-ai-agents`
- promptfoo — Assertions & metrics — `promptfoo.dev/docs/configuration/expected-outputs/`
- promptfoo — CI/CD integration — `promptfoo.dev/docs/integrations/ci-cd/`
- LangSmith — Pairwise evaluations — `blog.langchain.com/pairwise-evaluations-with-langsmith/`

Secondary / practitioner:
- Braintrust — What is LLM-as-a-judge — `braintrust.dev/articles/what-is-llm-as-a-judge`
- Braintrust — Baseline experiment — `braintrust.dev/encyclopedia/baseline-experiment`
- Braintrust — LLM evaluation guide — `braintrust.dev/articles/llm-evaluation-guide`
- DeepEval — Regression testing in CI/CD — `deepeval.com/guides/guides-regression-testing-in-cicd`
- ModelOp — Champion/Challenger testing — `modelop.com/ai-governance/glossary/champion-challenger-testing`
- Shaped — Golden tests in AI — `shaped.ai/blog/golden-tests-in-ai`
- Randy Coulman — Snapshot testing: use with care — `randycoulman.com/blog/2016/09/06/snapshot-testing-use-with-care/`
- Dermot Hughes — Why snapshot testing sucks — `dermothughes.com/blog/why-snapshot-testing-sucks/`
- Understand Legacy Code — Characterization vs approval tests — `understandlegacycode.com/blog/characterization-tests-or-approval-tests/`
- FutureAGI — LLM regression testing — `futureagi.com/glossary/llm-regression-testing/`
- TestQuality — LLM regression testing pipeline — `testquality.com/llm-regression-testing-pipeline/`
- OneUptime — Snapshot test failures — `oneuptime.com/blog/post/2026-01-24-snapshot-test-failures/view`
