# Fixture: test-prompt — orchestration decisions (harness stubbed)

Exercises the **`test-prompt`** orchestrator's **decision logic**, not real eval runs. `test-prompt`
normally *runs* the eval-harness and delegates to generate-evals / generate-ground-truth — all
expensive, agentic, and (for a nested harness run) recursive. So these evals **stub that boundary**:
the baseline run is pre-computed and handed in as a canned `results.json`, the harness/sub-skills are
declared unavailable, and the skill is graded on the **plan and decisions it states** — mirroring how
the harness itself mocks its one external `claude -p` seam.

## What's covered

| Eval | Fixture | Graded decision |
|---|---|---|
| regression red → classify + halt | `regression/skill.diff` (accidentally drops the *newest-on-top* rule) + `regression/baseline-results.json` (that eval failing) | spots the failing eval, ties it to the diff, frames it **regression-vs-stale** and **halts for the human**, does **not** silently edit the eval, and picks `run` (not regrade) to re-validate |
| wording change → regrade + skip calibrate | `wording/expectation.diff` (one existing expectation reworded; no target/GT change) | picks `regrade` (re-judge saved outputs, no agent run) and **skips** `calibrate` (no new ground truth) |

## What's graded

- **Deterministic** (no LLM): the response uses the regression-vs-stale frame and names the failing
  eval (eval 1); selects `regrade` and addresses calibration (eval 2).
- **Judge** (rubric): the regression is correctly tied to the diff and surfaced for the human without
  silently editing the eval; the `run`-vs-`regrade` choice is correct in both directions; calibration
  is skipped when no ground truth changed; and the decision never commits or pushes (working tree left
  for the human).

## Not covered here (by design)

Real nested harness execution and full `generate-evals create` authoring are **not** exercised — the
harness can't load sub-skills inside its empty-`.claude` workspace, and nested real runs would be slow,
costly, and recursive. The live wiring (`test-prompt` actually invoking `eval-harness`) is verified
**manually** as a one-off smoke, not in this suite.

The fixture targets a sample `release-notes` skill, but `test-prompt` itself is target-agnostic — the
target can be any prompt file the harness runs (a skill, a workflow, an agent definition).

See `evals.json` for the exact checks.
