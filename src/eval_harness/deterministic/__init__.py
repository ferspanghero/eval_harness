"""Deterministic tier: objective assertions on a run's produced artifacts (no LLM, fail-fast).

A run yields a :class:`RunArtifacts` (response + produced workspace + transcript); an eval carries a
list of :class:`Check` objects (one self-contained assertion each, parsed from fixture JSON by
:func:`parse_check`). :func:`evaluate` iterates the checks against the artifacts into a
:class:`DeterministicResult` — the aggregate that gates the (expensive) judge in the ``all`` flow.

Public surface: the framework (``Check`` / ``RunArtifacts`` / results / ``parse_check`` /
``evaluate``). Concrete checks live in ``checks/``; the fixture-``type`` → class table and
``parse_check`` are in ``checks/__init__.py``.
"""

from eval_harness.deterministic.base import (
    Check,
    CheckResult,
    DeterministicResult,
    RunArtifacts,
)
from eval_harness.deterministic.checks import parse_check
from eval_harness.deterministic.checks_runner import evaluate

__all__ = [
    "Check",
    "CheckResult",
    "DeterministicResult",
    "RunArtifacts",
    "evaluate",
    "parse_check",
]
