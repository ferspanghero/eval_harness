"""Execution checks — run a command in the produced workspace and assert on the result.

Unlike the string/file checks, these *execute* the produced artifacts: run its tests,
typecheck/lint it, or run a held-out acceptance test against it. Commands are fixture-declared (no
defaults — a fixture that declares none runs nothing), run with no shell (``shlex.split``) in the
workspace, under a timeout. Executing produced code shares the agent run's trust boundary; runs stay
in the isolated out-of-repo workspace (an OS sandbox is deferred — SEC1).
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_harness.deterministic.base import Check, CheckResult, RunArtifacts

# Hard cap on how long any single fixture-declared command may run before it's killed and the check
# fails — bounds a hung or runaway produced test suite so one command can't stall the whole sweep.
_TIMEOUT_SECONDS: float = 300
# On failure, how many trailing lines of a command's output to keep as the check's evidence — enough
# to show the error without flooding the result with a multi-thousand-line pytest/coverage dump.
_EVIDENCE_TAIL_LINES = 10


def _execute(command: str, workspace: Path) -> tuple[int, str]:
    """Run ``command`` (no shell) in ``workspace``; return ``(returncode, combined_output)``.

    Any way the command can't run — a launch failure (missing binary, ``OSError``), a malformed
    string (unbalanced quotes → ``ValueError`` from ``shlex.split``), an empty command, or a
    timeout — is reported as ``returncode -1`` with the reason as output, so a bad command fails its
    check rather than crashing the whole sweep.
    """
    try:
        argv = shlex.split(command)
        if not argv:
            return -1, "empty command"

        proc = subprocess.run(
            argv,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, f"timed out after {_TIMEOUT_SECONDS}s"
    except (OSError, ValueError) as exc:
        return -1, str(exc)

    return proc.returncode, proc.stdout + proc.stderr


def _tail(output: str) -> str:
    """The last few lines of command output, for bounded failure evidence."""
    lines = output.strip().splitlines()

    return "\n".join(lines[-_EVIDENCE_TAIL_LINES:])


def _parse_coverage_total(output: str) -> float | None:
    """Pull the percentage off coverage.py's ``TOTAL`` summary line, or None if absent."""
    for line in output.splitlines():
        if line.strip().startswith("TOTAL"):
            match = re.search(r"(\d+(?:\.\d+)?)%", line)
            if match:
                return float(match.group(1))

    return None


@dataclass(frozen=True)
class CommandSucceeds(Check):
    """Run a command in the produced workspace; pass iff it exits 0 (pytest, mypy, ruff, …)."""

    description: str
    command: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandSucceeds:
        return cls(description=data["description"], command=data["command"])

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        code, output = _execute(self.command, artifacts.workspace)
        passed = code == 0
        evidence = f"exit {code}: {self.command!r}"
        if not passed:
            evidence += f"\n{_tail(output)}"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)


@dataclass(frozen=True)
class CoverageAtLeast(Check):
    """Run a coverage command; pass iff the reported ``TOTAL`` coverage ≥ ``threshold`` percent."""

    description: str
    command: str
    threshold: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoverageAtLeast:
        return cls(
            description=data["description"],
            command=data["command"],
            threshold=float(data["threshold"]),
        )

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        code, output = _execute(self.command, artifacts.workspace)
        if code != 0:
            return CheckResult(
                self.description, False, f"command exit {code}: {self.command!r}\n{_tail(output)}"
            )

        total = _parse_coverage_total(output)
        if total is None:
            return CheckResult(
                self.description, False, f"no TOTAL coverage line in output of {self.command!r}"
            )

        passed = total >= self.threshold
        evidence = f"coverage {total:g}% vs threshold {self.threshold:g}%"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)


@dataclass(frozen=True)
class AcceptanceTest(Check):
    """Copy a held-out test into the workspace and run it — an independent check we own.

    ``source`` is a path in the fixture dir; the file is copied to ``dest`` (workspace-relative),
    run via ``command``, then **removed**. Pass iff it exits 0. Because it isn't seeded before the
    run, the agent never sees it and can't tune its code to it; because it's cleaned up after, it
    can't pollute other checks (lint/typecheck/coverage) — so check order doesn't matter.
    """

    description: str
    source: str
    dest: str
    command: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AcceptanceTest:
        return cls(
            description=data["description"],
            source=data["source"],
            dest=data["dest"],
            command=data["command"],
        )

    def evaluate(self, artifacts: RunArtifacts) -> CheckResult:
        source = artifacts.fixture_dir / self.source
        if not source.is_file():
            return CheckResult(self.description, False, f"held-out test missing: {self.source!r}")

        target = artifacts.workspace / self.dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        try:
            code, output = _execute(self.command, artifacts.workspace)
        finally:
            target.unlink(missing_ok=True)

        passed = code == 0
        evidence = f"exit {code}: {self.command!r}"
        if not passed:
            evidence += f"\n{_tail(output)}"

        return CheckResult(description=self.description, passed=passed, evidence=evidence)
