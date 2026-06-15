---
name: sample-skill
description: A tiny stand-in skill used as eval-harness's own worked example and structural self-test.
---

# sample-skill

A minimal placeholder for "the skill under test". It exists so the harness ships a runnable,
self-contained example of the eval layout — `evals/evals.json` (one eval with a deterministic check
and a judge expectation) plus `ground_truth/sample-skill.jsonl` (one frozen calibration case).

Point `--skill` at a real skill directory to evaluate it; scaffold a new one with `eval-harness init`.
