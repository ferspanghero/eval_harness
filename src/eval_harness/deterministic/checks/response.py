"""Checks over the agent's response text (its final message).

For text targets whose deliverable *is* the message — e.g. a review's findings or an audit's
report — rather than a produced file. Matching is case-insensitive: these
assert the *presence of a signal* (a symbol name, a vulnerability class), not exact wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eval_harness.deterministic.base import Check, CheckResult, RunArtifacts


@dataclass(frozen=True)
class ResponseContains(Check):
    """Assert a substring appears in the response (case-insensitive)."""

    description: str
    value: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponseContains:
        return cls(description=data["description"], value=data["value"])

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        passed = self.value.lower() in artifacts.response.lower()
        evidence = f"{'found' if passed else 'missing'}: {self.value!r} in response"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)


@dataclass(frozen=True)
class ResponseContainsAny(Check):
    """Assert at least one of several substrings appears in the response (case-insensitive)."""

    description: str
    values: tuple[str, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponseContainsAny:
        return cls(description=data["description"], values=tuple(data["values"]))

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        haystack = artifacts.response.lower()
        hits = [v for v in self.values if v.lower() in haystack]
        passed = bool(hits)
        evidence = f"matched {hits!r}" if passed else f"none matched: {list(self.values)!r}"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)
