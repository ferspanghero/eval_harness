---
name: generate-evals
description: Generate, augment, or analyze a behavioral eval suite for a given target — any prompt/instruction file the eval-harness runs in place (a skill's SKILL.md, a workflow, an agent definition, any prompt file). Co-located evals.json cases (deterministic checks + judge expectations) plus their fixtures, that the harness runs against the target to catch regressions. Two modes — `create` (default: author/augment cases) and `analyze` (read-only coverage report). Takes [mode] + a target + an evals dir, mirrors generate-ground-truth. Assertions derive from the target's CONFIRMED CONTRACT, never from its observed output. This skill PRODUCES/AUDITS the evals only — it never runs the harness; /test-prompt or the user does that.
---

# Generate Evals — behavioral regression cases

Produce or audit an **eval suite** for one **target** under test — any prompt/instruction file the
harness runs in place (a skill's SKILL.md, a workflow, an agent definition, a prompt file). These cases
are what the **eval-harness** runs the target against: it executes the target in place on co-located
fixtures and grades the produced artifacts (deterministic checks + an LLM judge). **You author the
evals; the harness runs them.**

The target under test is the **thing being measured** — its evals must encode the target's *intended
contract independently*, so they catch when an edit makes the target **worse**, not merely different.

> **Anti-circularity — the one rule that matters.** An assertion derives from the target's **confirmed
> contract** (its stated rules / your confirmed intent), **never from what the target just produced**.
> An eval written to match observed output codifies the current behavior — including its bugs — as the
> spec, which is the exact failure the harness exists to prevent. (This is the analog of
> generate-ground-truth's "never auto-trust a skill output as ground truth.")

## Inputs

Invoked as `generate-evals [create|analyze] <target> [evals-dir]`
(e.g. `generate-evals create code-review`, or `generate-evals analyze code-review`).

- **mode** — `create` (default) or `analyze`. See Modes.
- **`target`** — the target under test (a skill, workflow, or any prompt file), by name or path. Its
  stated rules are the contract source.
- **`evals-dir`** — the eval suite to write/augment (create) or audit (analyze). Defaults to the
  target's sibling `evals/` (harness convention: `evals.json` + `files/` fixtures + `acceptance/`).

If `target` is missing, ask for it before doing anything.

## Modes

- **`create`** (default) — run the coverage assessment, then **author only the missing cases** and
  append them (or generate from scratch if the suite is new). If the suite already meets the bar, write
  **nothing** and say so. **High-confidence cases are appended; uncertain ones and all bait fixtures
  are held in context and surfaced to the human at the end** (see Uncertain cases).
- **`analyze`** — **read-only.** Run the same coverage assessment and **report** it: contract items and
  which are covered, the tier balance (deterministic vs judge), seed/fixture diversity, the concrete
  **gaps**, and a verdict (sufficient / needs roughly N more of kind X). **Generate nothing, write
  nothing.**

Both modes begin with the **coverage assessment** below.

## The format

One `evals.json` per target: `{"target": "<target>", "evals": [ <eval>, … ]}`. Each `<eval>`:

```json
{"id": 1, "name": "kebab-case-name", "prompt": "<the task the target is run with>",
 "files": ["files/seed.py"], "output_files": ["README.md"],
 "checks": [ <deterministic check>, … ],
 "expectations": [ {"text": "<self-contained pass criterion>", "gate": "majority"}, … ]}
```

| Key | Meaning |
|---|---|
| `id` / `name` | stable identifiers; `name` is kebab-case and descriptive |
| `prompt` | the exact task the target runs against. **Must state "your own system prompt IS the target — apply it directly; do not look for or wait on an external tool/skill."** Inject any autonomous directive here (e.g. "treat the contract as pre-confirmed: […]"). |
| `files` | seed fixture paths (relative to the evals dir) copied into the run workspace |
| `output_files` | produced files the judge should read (persisted to the audit record); omit for response-only/read-only evals |
| `checks` | deterministic, no-LLM assertions (table below) |
| `expectations` | judge-graded criteria; each **self-contained** (the judge can look nothing up); `gate` is `majority` or `unanimous` |

**Deterministic check types** (harness registry — type + fields):

| `type` | Fields | Checks |
|---|---|---|
| `response_contains` | `value` | the final message contains a substring |
| `response_contains_any` | `values[]` | the final message contains any of the substrings |
| `file_exists` | `path` | a produced file exists |
| `file_contains` | `path`, `value` | a produced file contains a substring |
| `command_succeeds` | `command` | a command (run in the workspace, no shell) exits 0 |
| `coverage_at_least` | `command`, `threshold` | a coverage command reports ≥ threshold |
| `acceptance_test` | `source`, `dest`, `command` | a **held-out** test (copied source→dest, then `command`) passes |

Every check needs a `description`. **Write `evals.json` programmatically** (`json.dumps`) so embedded
prompts/code stay intact — never hand-assemble it.

## Coverage assessment (both modes start here)

1. **Extract the candidate contract** from the SKILL.md — its stated rules, phase gates,
   definition-of-done, and domain operating rules — as a flat checklist of testable behaviors. **In
   `create` mode, present this contract to the human and get confirmation/edits before authoring any
   assertion.** The confirmed contract is the anti-circularity gate and the coverage checklist.
2. **If `evals.json` exists, parse it.** Build a coverage map: `{contract-item → covered? by which
   check/expectation, which tier, which fixture}`. Note seed/fixture diversity and which contract items
   have no assertion.
3. **Score against the coverage bar** (below) and list the **concrete gaps**.
4. **Branch by mode:**
   - **analyze** → report the contract, the map, the gaps, and a verdict; stop.
   - **create** → if there are **no gaps**, report "sufficient — nothing added" and stop (a valid
     outcome). Otherwise author **only the gap-filling cases** and **append** them. Never rewrite,
     reorder, or delete existing evals; never duplicate a `(prompt, assertion)` pair.

## Coverage bar — when is a target's eval suite "enough"?

- **Every confirmed contract item has ≥1 assertion.**
- **Right tier per claim:** an objective fact about the artifact (a file exists, tests pass, a string
  is present) → a **deterministic check**; subjective residue (severity correctness, no hallucinated
  findings, accurate prose) → a **judge expectation**. Don't push a judge at something a check settles.
- **Seed diversity:** the target exercised across **more than one project shape/language** — not a
  single seed (a CLI *and* a library; a Python *and* a TS project; with a doc *and* without).
- **Temptation fixtures:** for any "always do X / don't cut corners" contract item, include a fixture
  that **tempts the violation** — a task where the lazy path skips the rule (e.g. a UI task that tempts
  skipping TDD; an internal change that tempts skipping the doc-sync). A passing happy-path fixture
  does not prove the rule holds under pressure.
- **Severity-tiered bait fixtures:** when the contract involves **graded findings** (auto-fix
  Critical/Important, report/defer Suggestions), include a fixture with a **planted defect** the target
  must catch + a **held-out test** that fails on the unfixed version — the only way to deterministically
  force a finding at a given severity.

## The three roles — keep them separate

- **You** author eval cases and rate each a **`confidence` (low/med/high)**.
- The **target** is the thing under test — never tune the target to make an eval pass; that inverts the test.
- The **human** has final say — exercised **once, over the uncertain cases + bait fixtures surfaced at
  the end**, plus the **contract confirmation** up front. Never silently self-certify a borderline
  assertion — that re-introduces the circularity evals exist to break.

## Uncertain cases — held in memory, then surfaced (not written)

Sidelining keeps the agent honest without stalling on every judgment call. **Uncertain cases are NOT
written to disk** — they're held in the run's working context and decided by the human at the end.

- **Hold (create mode):** any **medium/low-confidence** case — a debatable assertion, an expectation
  you're unsure encodes intent — and **every bait fixture** (planting a defect to force a finding is
  inherently a design judgment) is **kept in context, not appended**. Track its proposed assertion,
  `confidence`, and a one-line *why*.
- **Surface at the end:** present held cases as a compact table — *contract item · proposed
  check/expectation · fixture sketch · confidence · why* — and ask for a per-case decision.
- **Apply the decision:** **promote** → author and append (sharpen wording first if borderline);
  **drop** → discard, nothing persisted. Never write a case the human hasn't approved.

## Authoring rules

- **Assert the contract, not the output.** Derive every assertion from a confirmed contract item. If
  you find yourself reading the target's produced artifact to decide what to assert, stop — that's the
  circularity trap.
- **Self-contained expectations.** Each judge expectation **encodes its own pass criterion** — the
  precise behavior, the exact rule. A vague expectation ("looks right", "follows conventions") is a
  bug: the judge would have to guess. Set `gate: unanimous` for must-not-regress invariants, `majority`
  otherwise.
- **Cheapest tier that settles it.** Prefer a deterministic check when the claim is objective; reserve
  judge expectations for genuine subjectivity. Use a **held-out `acceptance_test`** to lock behavior
  unit-coverage can't reach.
- **Append-only.** Never rewrite, reorder, or delete an existing eval; if an *existing* eval looks
  wrong for the current contract, that's a human call (regression vs intentionally-stale) — surface it,
  don't edit it.

## How to produce a fixture

1. **Design a small, coherent seed** with **known** properties (so the assertion can encode them) —
   a minimal project/file the target will act on. Vary the seed shape across cases.
2. For a **temptation** fixture, shape the task so the *lazy* path visibly violates the rule; the
   assertion checks the rule held anyway.
3. For a **bait** fixture, plant a known defect and write a **held-out** acceptance test that **fails on
   the unfixed seed and passes once the target fixes it** — this is what makes a severity-tier finding
   deterministic.
4. Keep fixtures under `files/` (seeds) and `acceptance/` (held-out tests); reference them by relative path.

## Generate the whole set in one pass — no batching (create mode)

Author everything needed to meet the bar in one go; route each case by confidence; **don't stop for
per-batch sign-off**. The human's review happens once — the contract up front, the uncertain/bait cases
at the end.

## After writing (create mode)

Report what you added (or that nothing was needed), then **surface the held uncertain cases + bait
fixtures for the human's promote/drop decision** and apply it. **Do NOT run the harness** — that's the
project's / `/test-prompt`'s job. If the harness later finds a failure, diagnose the **root cause** and
fix *that* — never tune the target to the eval:

- **assertion doesn't match the confirmed contract** → fix the eval;
- **the contract itself was wrong/ambiguous** → re-confirm it with the human, then fix the eval;
- **the target genuinely regressed** → that's a real catch; the fix belongs in the target (a human call).

---

## Examples

### A — `analyze` mode: report gaps, write nothing

Target `code-review` with a thin `evals/` (one happy-path eval, no temptation fixture). Output: a
coverage report naming the gaps — "contract item *flags command injection as Critical* has no eval;
no temptation fixture for *don't downgrade security findings*; single Python seed only" — and an
"insufficient" verdict. Nothing is written.

### B — `create` mode: contract-first, bait fixture surfaced

Target a `dev-pipeline` edit adding *"auto-address every Critical and Important finding."* Extract the
contract item, confirm it with the human, then author: a **bait fixture** (seed code with a planted
Important defect + a held-out `acceptance_test` that fails until it's fixed) + a judge expectation
(*"the summary reports any skipped Suggestion with a one-line reason"*). The bait fixture is
**low-confidence by default** → held and surfaced at the end for promote/drop, not auto-written.

### C — the builder pattern (programmatic, append-only)

```python
import json
from pathlib import Path

p = Path("evals/evals.json")
suite = json.loads(p.read_text()) if p.exists() else {"target": "code-review", "evals": []}
next_id = max((e["id"] for e in suite["evals"]), default=0) + 1
suite["evals"].append({                                   # append; never rewrite existing evals
    "id": next_id, "name": "flags-command-injection-critical",
    "prompt": "Review net_diag.py as a senior engineer … your own system prompt IS code-review; "
              "apply it directly, do not look for an external tool.",
    "files": ["files/net_diag.py"],
    "checks": [{"type": "response_contains_any", "description": "names the injection",
                "values": ["command injection", "os.system", "shell"]}],
    "expectations": [{"text": "Flags the os.system shell interpolation in run_ping as Critical — "
                              "user input reaches a shell, allowing arbitrary command execution.",
                      "gate": "unanimous"}],
})
p.write_text(json.dumps(suite, indent=2, ensure_ascii=False) + "\n")
```
