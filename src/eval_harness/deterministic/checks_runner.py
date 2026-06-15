"""The deterministic tier's iterator: run each check against the run's artifacts and aggregate.

All check-type knowledge lives in the check classes (``checks/``) and the registry
(``base.parse_check``); this module just loops — no branching on check type.
"""

from __future__ import annotations

from collections.abc import Sequence

from eval_harness.deterministic.base import Check, DeterministicResult, RunArtifacts


def evaluate(checks: Sequence[Check], artifacts: RunArtifacts) -> DeterministicResult:
    """Run all checks against the run's artifacts and aggregate the outcome."""
    results = tuple(check.evaluate(artifacts) for check in checks)

    return DeterministicResult.from_checks(results)
