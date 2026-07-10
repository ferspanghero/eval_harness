---
name: generate-ground-truth
description: Generate, augment, or analyze a judge/grader calibration ground-truth file for a given skill — frozen {skill, topic, task, expectation, output, human_label, notes} cases that measure judge-vs-human agreement. Two modes — `create` (default: author/augment cases) and `analyze` (read-only coverage report). Takes [mode] + a target skill + an output file path, and writes JSONL in that format. Use before running an eval harness's calibrate/grader command. This skill PRODUCES/AUDITS the file only — it never runs the grader; the project does that.
---

# Generate Ground Truth — judge calibration cases

Produce or audit a **ground-truth file** of frozen calibration cases for one skill under test. These
cases are what an LLM **grader** is measured against: a separate "calibrate" command (NOT this skill)
later re-grades each frozen output and reports judge-vs-human agreement. **You generate the file; the
project tests it.**

Each case freezes the skill's **OUTPUT** + a self-contained **expectation** + the **human verdict**
the grader should reach. The **grader is the thing under test** — never the skill that produced the
output. Calibrating the grader is what lets the project trust (or distrust) its verdicts.

## Inputs

Invoked as `generate-ground-truth [create|analyze] <skill> <output-file>`
(e.g. `generate-ground-truth code-review calibration/code-review.jsonl`, or
`generate-ground-truth analyze code-review calibration/code-review.jsonl`).

- **mode** — `create` (default) or `analyze`. See Modes.
- **`skill`** — the skill under test, whose produced output the grader judges.
- **`output`** — the ground-truth JSONL file to write/augment (create) or audit (analyze).

If `skill` or `output` is missing, ask for it before doing anything.

## Modes

- **`create`** (default) — run the coverage assessment, then **generate only the missing cases** and
  append them (or generate from scratch if the file is new). If the set already meets the bar, write
  **nothing** and say so. **High-confidence cases are appended to `output`; uncertain ones are held
  in context and surfaced to the human at the end** (see Uncertain cases).
- **`analyze`** — **read-only.** Run the same coverage assessment and **report** it: the topics,
  quadrants present, pass/fail balance, distinct seeds/shapes, the concrete **gaps**, and a verdict
  (sufficient / needs roughly N more of kind X). **Generate nothing, write nothing.**

Both modes begin with the **coverage assessment** below.

## The format (one JSON object per line)

```json
{"skill":"code-review","topic":"command-injection","task":"<exact prompt the skill was run with>","expectation":"<self-contained pass/fail criterion that encodes its own ground truth>","output":"<the skill's produced artifact, frozen byte-exact>","human_label":"pass","notes":"<short tag: quadrant + real|synthetic>"}
```

| Key | Meaning |
|---|---|
| `skill` | the skill name — **identical on every line of the file** |
| `topic` | groups related cases (e.g. the two NoSQL cases, or one defect class) |
| `task` | the exact prompt the skill was run with |
| `expectation` | what the grader must verify — **self-contained** (the grader can't look anything up) |
| `output` | the produced artifact, **frozen byte-exact** (review text / audit report / produced file content) |
| `human_label` | the correct grader verdict: `pass` or `fail` |
| `notes` | short provenance: the quadrant + whether the output is real or synthetic |

**Write programmatically** (e.g. a small script using `json.dumps`) so the frozen `output` — which
contains newlines, quotes, and code fences — stays byte-exact. Never hand-assemble JSONL lines.

## Coverage assessment (both modes start here)

1. **If `output` exists, parse it.** Build a coverage map: `{topic → count}`, which **quadrants**
   appear, the **polarity balance** (pass vs fail) per topic, and the distinct **seeds / project
   shapes** used (infer from the tasks and outputs).
2. **Score it against the coverage bar** (below) and list the **concrete gaps** — e.g. "no
   out-of-scope case", "only one seed shape", "missing the under-rated polarity for defect class X".
3. **Branch by mode:**
   - **analyze** → report the map + gaps + verdict, and stop.
   - **create** → if there are **no gaps**, report "sufficient — nothing added" and stop (a valid,
     expected outcome). Otherwise generate **only the gap-filling cases** and **append** them. Never
     rewrite, reorder, or delete existing lines; never duplicate an existing `(topic, output)` pair.
     Group new cases by topic.

## Coverage bar — when is a skill's set "enough"?

- **Every quadrant, both polarities:** correct-detection · **missed** · **fabricated** ·
  **under-rated** · **over-rated** · **out-of-scope/excluded** · **partial** (one real + one
  fabricated) · cautious-decline.
- **Seed diversity:** the skill exercised across **more than one project shape/language** — not a
  single seed. (A doc skill: a CLI *and* a library; a Python *and* a TS project; a project with a
  LICENSE *and* without; one with a `plan.md` *and* without.)
- **The skill's real failure classes covered:** a security/review skill → multiple defect types
  (injection, traversal, auth, resource/logic, perf, style-out-of-scope); a doc skill → accuracy,
  invention, drift (both directions), structure, and the documentation rules (no-stale-numbers, etc.).
- Aim for an agreement target of ~85%+ when the project later calibrates; the set then doubles as a
  **regression test for the grader prompt**.

## The three roles — keep them separate

- **You** author cases, assign each a `human_label`, and rate a **`confidence` (low/med/high)**.
- The **grader** is the thing under test.
- The **human** has final say — exercised **once, over the uncertain cases the run surfaces at the
  end**, not a per-case sign-off. Never silently self-certify a borderline call — that re-introduces
  the circularity calibration exists to break.

## Uncertain cases — held in memory, then surfaced (not a file)

Sidelining keeps the agent honest without stalling on every judgment call. **Uncertain cases are NOT
written to disk** — they're held in the run's working context and decided by the human at the end, so
the `output` file only ever receives high-confidence cases.

- **Hold (create mode):** any **medium/low-confidence** case — anything you are not firmly sure of (a
  debatable severity, a subtle out-of-scope call) — is **kept in context, not appended to `output`**.
  Track its proposed `human_label`, its `confidence`, and a one-line *why*.
- **Surface at the end of the run:** present the held cases to the human as a compact table —
  *topic · expectation · output (or a synopsis) · proposed label · confidence · why* — and ask for a
  per-case decision. This is the **single** human checkpoint.
- **Apply the decision:** **promote** → append the case to `output` (optionally **sharpen** a
  borderline expectation first); **drop** → discard it, nothing is persisted. Never write a case the
  human hasn't approved.

(If a project ever wants a durable, cross-session review queue instead of an in-session decision, it
can ask you to persist the held cases to a file — but the default is **in-memory + decide-now**.)

## Authoring rules

- **Freeze the OUTPUT, not the source.** Run the skill, capture what it produced. The grader sees
  only `(grader-instructions, task, expectation, output)` — never the source the skill worked from.
- **Self-contained expectations.** Each expectation **encodes its own ground truth** — the precise
  severity, the exclusion rule, what is actually present/safe. A vague expectation ("appropriate
  severity", "follows house structure") is a bug: the grader would have to guess.
- **Real taxonomy, one severity.** Use the skill's actual categories (read its severity guidelines);
  pick **one** severity, never "High or Medium". If a severity call is genuinely debatable, run the
  real skill and let its output *inform* your pick — or write the expectation to test **detection**
  rather than the debatable severity.
- **Should-pass = real, vetted outputs; should-fail = synthetic.** Real runs don't fail, so you must
  **fabricate** the fail cases. **Never auto-trust a skill output as ground truth** — judge each real
  output against the expectation *first*, then label it; a subtly-wrong real output becomes a **fail**
  case. Promoting skill output to `pass` just because the skill emitted it calibrates the grader to
  agree with the *skill* — an echo chamber that defeats the purpose.
- **Reuse one expectation across both polarities.** A single expectation often anchors a real PASS
  (the skill met it) and one or more synthetic FAILs (under-rated / missed / fabricated against the
  same bar). That's the cleanest way to test the grader on a defect class.

## How to produce a real output

1. **Design a representative seed input** for the skill — a small but coherent project/file with
   **known** properties (so the expectation can encode them). Vary the seed shape across cases.
2. **Run the skill on it** — e.g. spawn a sub-agent that loads the skill via the Skill tool and runs
   it on the seed, returning its output.
3. **Freeze the produced artifact verbatim.** For a file-producing skill, have the sub-agent write
   the file and then **read it from disk byte-exact** (don't transcribe). For a text skill, capture
   its returned text.
4. **Vet it against the expectation** before labeling — if it's subtly wrong, it's a `fail` case.

## Generate the whole set in one pass — no batching (create mode)

Author everything needed to meet the coverage bar in one go; route each case by confidence; **don't
stop for per-batch human sign-off**. The human's review happens once, over the uncertain cases you
surface at the end.

## After writing (create mode)

Report what you added (or that nothing was needed), then **surface the held uncertain cases for the
human's promote/drop decision** (see Uncertain cases) and apply it. **Do NOT run the grader / eval
command** — that's the project's job, over `output`. If the project later finds
a disagreement, diagnose the root cause and fix **that** — never the skill under test:

- **ambiguous / under-specified expectation** → sharpen it;
- **grader genuinely lenient or strict** → fix the project's versioned grader prompt;
- **unrealistic strawman output** the skill would never emit → replace it with a real vetted one;
- **judge variance** (a flip on a *borderline* case, often a weaker/cheaper grader) → re-grade with
  the project's strong grader (majority vote) before concluding; a flip the strong grader doesn't
  reproduce is noise, not a case defect.

---

## Examples (from real calibration work)

### A — a text skill (`code-review`): one expectation, both polarities

The same expectation anchors a **real PASS** and a **synthetic under-rated FAIL** for one defect class.

`task` (both): *"Review the code in net_diag.py as a senior engineer. Report issues categorized by
severity (Critical / Important / Suggestion). Be specific about the file, function, and line."*

`expectation` (both): *"The review flags the command injection in run_ping as a Critical issue — the
user-supplied host is f-string-interpolated into a string run by a shell via os.system, allowing
arbitrary command execution."*

**PASS — real run** (`human_label: pass`, `notes: command-injection / correct-detection (real run)`),
`output` (frozen verbatim; trimmed here):
```
# Code Review — net_diag.py
## Critical
### 1. Command injection via unsanitized shell interpolation — run_ping, lines 7–9
`host` is interpolated directly into a string handed to a shell (os.system runs /bin/sh -c)…
arbitrary command execution. …
```

**FAIL — synthetic, under-rated** (`human_label: fail`, `notes: command-injection / under-rated
(synthetic)`), `output`:
```
## Code Review: net_diag.py
### Critical
None.
### Suggestions
- run_ping uses os.system, which is somewhat dated. Consider subprocess for better portability.
```
→ The injection is present but downgraded to a portability Suggestion. A trustworthy grader must
**fail** this against the expectation; the case checks that it does.

### B — a file-producing skill (`create-readme`): the OUTPUT is the produced file

Seed: a TS/Node CLI `quikfmt` **with an MIT `LICENSE` file**. The expectation encodes that ground truth.

`expectation`: *"The README's License section names the MIT license, matching the LICENSE file
present in the project — it does not omit the license or claim a different one."*

**PASS — real run** (`human_label: pass`), `output` = the produced `README.md`, read byte-exact (trimmed):
```
# quikfmt
…
## License
MIT — see [LICENSE](LICENSE).
```

**FAIL — synthetic, license-mismatch** (`human_label: fail`), `output` = a README whose License
section says `Apache License 2.0` — contradicting the LICENSE file. The grader must **fail** it.

### C — the builder pattern (byte-exact append)

```python
import json
from pathlib import Path

out = Path("calibration/code-review.jsonl")
real_review = Path("/tmp/run/review.md").read_text().rstrip("\n")   # frozen byte-exact
case = {"skill": "code-review", "topic": "command-injection",
        "task": TASK, "expectation": EXP, "output": real_review,
        "human_label": "pass", "notes": "command-injection / correct-detection (real run)"}
with out.open("a", encoding="utf-8") as f:        # append; never rewrite existing lines
    f.write(json.dumps(case, ensure_ascii=False) + "\n")
```
