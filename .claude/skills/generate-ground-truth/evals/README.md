# Fixture: generate-ground-truth — analyze-mode coverage assessment

Exercises the **`generate-ground-truth`** skill in its **`analyze`** mode — the read-only path that
reports a calibration set's coverage (topics, quadrants, polarity balance, seed diversity) and a
verdict, without writing anything.

Analyze mode is chosen deliberately: it produces a gradable text report and needs no sub-agent runs.
The skill's **`create`** mode degrades under this harness's execution model (it would run the *target*
skill on seeds via a sub-agent, but the workspace's empty `.claude` marker means the Skill tool can't
load that target skill), so create mode is not exercised here.

## What's covered

| Eval | Fixture | Graded behaviour |
|---|---|---|
| thin set → gaps | `gapped.jsonl` — one topic, one quadrant, all-pass, one seed | correctly identifies the **baseline gaps** (no fail cases, one defect class, one quadrant) and verdicts insufficient |
| broad set → no false gaps | `sufficient.jsonl` — six defect classes, both polarities, all eight quadrants, two seed languages | accurately reads the breadth and does **not** misreport baseline coverage as missing |

Both fixtures are calibration ground-truth for a hypothetical `code-review` set; analyze reasons about
their coverage from the tasks, labels, and notes — it never re-grades the frozen outputs.

The broad case tests **coverage-map accuracy**, not a specific verdict word: an analyze agent's job is
to surface gaps, so it may always suggest extensions (more languages, more classes). The graded signal
is that it reads what's present correctly — the real failure is misreading a broad set as thin.

## What's graded

- **Deterministic** (no LLM): the report uses coverage-assessment vocabulary; the thin case also
  surfaces a gap signal.
- **Judge** (rubric): the coverage diagnosis is accurate, the thin set's baseline gaps are named while
  the broad set's existing coverage is not misreported as missing, and the run stays read-only.

See `evals.json` for the exact checks.
