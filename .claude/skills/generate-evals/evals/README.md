# Fixture: generate-evals — analyze + contract-extraction

Exercises the **`generate-evals`** skill in its read-only paths — the ones that produce a gradable
text artifact and need **no sub-agent runs**:

- **`analyze` mode** — extracts a skill's contract, maps the eval suite's coverage against it, and
  reports gaps + a verdict, writing nothing.
- The **contract-extraction step** of `create` mode — extract the contract, propose candidate
  assertions, and **halt at the confirmation gate**, writing nothing.

Full `create` mode (authoring + writing eval cases) is **not** exercised here, for the same reason
generate-ground-truth's create mode isn't: it would run the *target* skill on seeds via a sub-agent,
but the workspace's empty `.claude` marker means the Skill tool can't load that target skill. The
read-only paths above need no such run.

## What's covered

| Eval | Fixture | Graded behaviour |
|---|---|---|
| thin suite → gaps | `sample-skill/SKILL.md` + `thin-evals.json` (covers 1 of 5 contract items) | extracts the contract, names the **uncovered** items, verdicts insufficient |
| broad suite → no false gaps | `sample-skill/SKILL.md` + `broad-evals.json` (all 5 items + a temptation fixture) | reads the breadth correctly; does **not** misreport baseline coverage as missing |
| contract extraction is non-circular | `sample-skill/SKILL.md` + `observed-output.txt` (a buggy run) | proposes assertions from the **stated contract** (newest-on-top, ISO dates), **not** the buggy observed output; halts for confirmation; writes nothing |

The sample skill is a small `release-notes` skill with a five-item contract (newest-on-top,
version+date+category, never-rewrite-existing, create-if-missing, ISO dates). The fixture eval-suites
(`thin`/`broad`) are inert JSON the analyze run reasons about — it never executes them.

## What's graded

- **Deterministic** (no LLM): the report uses coverage/contract vocabulary; the thin case surfaces a
  gap signal; the contract-extraction case shows both an assertion proposal and a confirmation halt.
- **Judge** (rubric): the coverage diagnosis is accurate (thin gaps named, broad coverage not
  misread); the extracted assertions track the **contract, not observed output** (anti-circularity);
  every case stays read-only.

The anti-circularity case is the load-bearing one: the provided `observed-output.txt` deliberately
violates the contract (entry at the bottom, US-format date), so an agent that derives assertions from
output instead of the contract fails it.

See `evals.json` for the exact checks.
