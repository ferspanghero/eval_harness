"""Framework for the deterministic check library: contract, subject, results.

A check is **self-contained**: it carries its own config (parsed from fixture JSON by its
``from_dict``) and knows how to ``evaluate`` itself against a :class:`RunArtifacts` — the
encapsulated result of one headless run. The tier's iterator (``checks_runner.py``) just loops over
checks and calls ``evaluate``. This module is the contract layer (``Check`` + the data types); the
fixture-``type`` → class table and ``parse_check`` live in ``checks/__init__.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunArtifacts:
    """The context one ``claude -p`` run leaves for its checks — the subject every check inspects.

    A check reads only the slice it needs: response checks read :attr:`response`, file checks read
    :attr:`workspace`, and execution checks may read :attr:`fixture_dir` for held-out acceptance
    assets. Unused slices are ignored — that uniform contract keeps the iterator branch-free.
    """

    response: str
    workspace: Path
    fixture_dir: Path = Path(".")


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one deterministic check."""

    description: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class DeterministicResult:
    """Aggregated outcome of an eval's deterministic checks."""

    checks: tuple[CheckResult, ...]
    passed: int
    total: int
    all_passed: bool

    @classmethod
    def from_checks(cls, checks: tuple[CheckResult, ...]) -> DeterministicResult:
        passed = sum(1 for c in checks if c.passed)
        total = len(checks)

        return cls(checks=checks, passed=passed, total=total, all_passed=passed == total)


class Check(ABC):
    """One objective, no-LLM assertion over a run's :class:`RunArtifacts`.

    Concrete checks are frozen dataclasses carrying their own config, mapped from their fixture
    ``type`` by the registry table in ``checks/__init__.py``. ``from_dict`` parses that config from
    JSON; ``evaluate`` runs the assertion and returns a :class:`CheckResult`.
    """

    description: str

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict[str, Any]) -> Check:
        """Build the check from its fixture JSON entry."""

    @abstractmethod
    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        """Run the assertion against the run's produced artifacts."""
