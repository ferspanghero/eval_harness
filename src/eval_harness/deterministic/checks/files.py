"""Checks over files the run produced in its workspace.

For file-producing skills — ``create-readme``, ``write-project-docs``, ``create-claude-md`` — whose
deliverable is a written file, not the agent's final message. ``path`` is workspace-relative; the
workspace root is supplied at run time via :class:`~eval_harness.deterministic.base.RunArtifacts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eval_harness.deterministic.base import Check, CheckResult, RunArtifacts


@dataclass(frozen=True)
class FileExists(Check):
    """Assert a workspace-relative path exists."""

    description: str
    path: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileExists:
        return cls(description=data["description"], path=data["path"])

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        passed = (artifacts.workspace / self.path).is_file()
        evidence = f"{'present' if passed else 'absent'}: {self.path!r}"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)


@dataclass(frozen=True)
class FileContains(Check):
    """Assert a workspace-relative file exists and contains a substring (case-insensitive)."""

    description: str
    path: str
    value: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileContains:
        return cls(description=data["description"], path=data["path"], value=data["value"])

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        target = artifacts.workspace / self.path
        if not target.is_file():
            return CheckResult(self.description, False, f"file missing: {self.path!r}")

        content = target.read_text(encoding="utf-8", errors="replace").lower()
        passed = self.value.lower() in content
        evidence = f"{'found' if passed else 'missing'}: {self.value!r} in {self.path!r}"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)
