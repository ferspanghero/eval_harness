# v2 Ideas & Backlog

> Parking lot for post-v1 work. **Not a plan** — unresolved ideas and deferred decisions. The defining
> v1→v2 difference is the **orchestrator**; everything else here is opportunistic. See
> `../research.md` for full rationale and `../v1/plan.md` for what v1 actually builds.

## 1. Deterministic orchestrator (the defining v2 change — replaces v1/M2)

Replace the copied prose orchestrator with a deterministic one so phase order is guaranteed. Two options:

- **Command-driven (soft):** a command that drives phases in fixed order via discrete subagents.
- **Script-driven (hard):** real code calls each phase as a fixed step; needs a separate `orchestrator/` dir.

**The gates become code, not prose.** The vision: *deterministic orchestration + skills (each with its
own evals) + deterministic checks* — most of the Definition of Done (coverage met, build/tests pass,
files exist, lint/typecheck) is **automatable as deterministic checks on artifacts**, run live by the
orchestrator instead of asserted by prose gates. This replaces v1/M2's "run the prose pipeline and grade
artifacts" with "run a deterministic pipeline that self-checks its outputs."

The v1 eval harness is the safety net that proves the deterministic rewrite didn't regress behavior.

**LLM transport — done as part of this work:** switch the **judge (only)** from headless `claude -p`
to the **`anthropic` SDK**. Pros that matter: **forced structured outputs** (guaranteed schema vs
prompt-for-JSON + parse + retry), **`temperature=0` + exact model/version pinning** (reproducibility
the research flagged as important), lower per-call overhead. Cost to accept: **separate per-token API
billing** for judge calls — the subscription stops shielding them. The `llm` seam makes it a
**one-module swap** — nothing else changes; OBS1's audit record (shipped) is the evidence base if the
swap's value needs validating against judge noise first.

## 2. Prompt versioning / registry (for A/B testing)

Git history is not prompt versioning. Need: track prompt versions independently, pull previous
versions easily, A/B two versions through the same fixtures.

- **Approach:** a **prompt registry** — versioned snapshots (`prompts/<name>/<semver>/`) + a
  `manifest.json` mapping logical name → active version → content hash. Decoupled from `git checkout`.
- **Harness A/B mode:** materialize versions A and B, run the **same fixtures** against each, diff their
  profiles/verdicts (promptfoo-style variant comparison).
- **Prior art** (named in research, NOT independently verified): promptfoo (variant comparison),
  LangSmith / Langfuse / PromptLayer / Humanloop (prompt registries).

## 3. Self-improvement loop

Today's `analyze` command is read-only diagnosis (one LLM pass over the benchmark → observations).
Grow it into a loop that **closes the gap to a fix**: diagnose failure patterns → **propose concrete
edits** to the pipeline/skills → (optionally) **apply** them → re-run evals → keep the best. This is
skill-creator's improve step, which there is **agent + human-in-the-loop** (no autonomous body-editing
script); the only fully-automated example is `scripts/run_loop`, scoped to trigger-description tuning
— a structural template for an eventual `--apply` mode. Start suggest-only; keep the human driving.

## 4. Inline fail-fast guardrails

A hook that **halts a live run** the moment a phase skips its skill or a gate fails, instead of only
catching it after the fact in evals. Most useful once the orchestrator is deterministic (§1), where the
gate checks already exist as code.

## 5. From tripwire to gauge — measurement upgrades

Every current eval passes at 1.0 on a **single agent sample**, so the harness catches gross breakage
but can't see gradual drift or show improvement. Three upgrades with a real dependency order —
**PERF1 (v1 backlog) → N-run sampling → trending** — plus discriminative evals supplying the gradient
in parallel. Building trending before sampling produces a noisy chart; building sampling without
PERF1 produces an unaffordable sweep.

### N-run sampling — pass-rate per eval (subject variance)

Today the **agent run happens once** per eval; only the judge repeats (`--judges N`). That treats
*grader* variance but leaves *subject* variance unmeasured — a pass/fail can flip on the agent's own
stochasticity, not the skill edit (the flaky convention evals were exactly this; the fix made the
*evals* variance-tolerant, which sidesteps the variance rather than measuring it). The research already
points at the treatment: per-task success as a **rate over repeated trials** (pass@k / pass^k —
`../research.md` §7). Add a `--runs N` that repeats the agent run + deterministic checks, reports a
per-eval **pass-rate**, and gates on a threshold instead of a single sample.

**Decision driver: cost** — this multiplies the most expensive tier. Most useful targeted (`--eval`) at
behaviorally-variant evals rather than as the default sweep; **PERF1 is the enabler** (N× agent runs
are only affordable parallelized). Absorbs the former "fixtures × N cost/scale tuning" deferred item.

### Discriminative evals — escape the 1.0 ceiling

The tripwire can't show that an edit made output *marginally worse* (still green) or *better* (already
green) — no gradient to optimize against. Add a few **deliberately-hard cases** the current workflow
only partially satisfies, marked **report-only (non-gating)** — the gate-vs-report demotion the
noise-aware-gate research recommends — so sweeps stay green while improvement becomes visible.

**Don't author these from scratch:** F3's post-bug-fix fixture shapes (internal-refactor, non-TDD-able,
edge-heavy — v1 backlog) are exactly these cases; one authoring pass serves both.

Tension to manage: "fix the skill, not the fixture" applies to *regression* signal; these cases are
explicitly **aspirational targets** and must be labeled as such in the fixture README so the rule isn't
misapplied to them.

### Longitudinal trending across stored runs

`evaluations/<timestamp>/results.json` persists every run, but each run is judged in isolation —
`cli.py`'s exit-code docstring already names "worse-than-baseline," yet nothing compares runs (either
implement this or drop the claim from the docstring). Add an opt-in **trend report**: compare the
latest run's aggregate pass-rate and per-expectation vote-strength against the last accepted run, so
green→yellow→red drift surfaces *before* it crosses a gate.

**Ordering:** only meaningful after N-run sampling above — trending single-sample pass/fail trends
noise; trending pass-rates trends signal.

Stays inside the `../research.md` §7 conclusions: **absolute thresholds keep gating**; trending is a
*separate, opt-in report* over aggregates — never a frozen judge verdict used as a golden, never a
default live double-run.

## 6. Seed refresh / held-out fixtures (anti-Goodhart)

Iterating the workflow against fixed fixtures slowly **tunes it to the test set** — amplified by the
same model family both running and judging. The seed de-contamination pass was one-time; the pressure
is permanent. Two mitigations, cheapest first:

- **Periodic seed refresh:** regenerate planted-defect seeds (new domain, same defect class) once an
  eval has driven k skill edits, then re-baseline.
- **Held-out fixture set:** a small set never iterated against, run rarely (pre-release / milestone
  only), as the unbiased check on the tuned set.

## 7. Visual / UI evals with declared prerequisites (render-only defects)

Some conventions — e.g. `.conventions/testing.md`'s "look at the UI, don't just assert on it" — guard
defects that **don't live in the code text**: overflow/clipping at a breakpoint, a font that didn't
render, an interaction that blanks the screen. A faithful eval must use a **render-only** fixture whose
defect only surfaces by *driving the running UI*, so a code-reading agent can't shortcut it. (A spike —
a `code-review` eval on a dark-mode theme — failed *as a probe*: the planted bug was a contrast ratio,
which is just colour math, so the reviewer solved it statically and never had to render. Reverted. The
lesson: the target must be a **run/verify-style skill** that launches and observes the app, not
`code-review`; and the defect must be runtime-emergent, not code-derivable.)

**The idempotency problem (the crux).** A visual eval needs a system dependency (`playwright` + a
chromium build). If undeclared, the **verdict depends on ambient machine state** — present → graded,
absent → the agent can't render → FAIL — so the same eval gives different results on different boxes.
Design to remove the state-dependent FAIL:

- **Declare** a `requires` block on the eval (tools + **pinned** versions: playwright version *and*
  chromium revision — the browser build floats otherwise).
- **Unmet → skip-loudly, not fail.** A missing system dep is an *environment gap, not a behaviour
  regression*; failing on it is what breaks idempotency. Skipping keeps the **graded subset
  reproducible** (present→graded, absent→skipped, never a flaky FAIL). The skip must be logged loudly
  (per the "no silent caps" rule) so a green suite can't hide that the visual eval never ran.
- **Provision once, pinned & cached — never per-run.** Per-run `npx playwright install` is itself
  non-idempotent (network, slow, floating build). Install via a separate idempotent `setup`/provision
  step (approved one-time install, user-global or a harness cache); the **agent never installs
  mid-run** — `RUNNER_TOOLS` is network-free by design, it just drives an already-present browser.
  (Trust: harness-executed install commands from eval data are a new exec surface — prefer a
  pinned/reviewed prereq over arbitrary declared installs.)
- **Grade layout facts, not pixels.** Screenshot-equality flakes across fonts / anti-aliasing / DPI
  even with chromium present. Assert **rendered geometry** (box overflows container, text clipped,
  computed contrast) — render-derived but reproducible.

**Harness cost (new surface → Full-mode design before code):** `requires` schema field; runner
preflight → a new **`skipped`** outcome state when unmet; benchmark/summary must represent `skipped`
distinctly from pass/fail and surface it loudly; a separate idempotent `setup`/provision path decoupled
from `run`. Composes with §5's measurement work but is independent of the §1 orchestrator.
