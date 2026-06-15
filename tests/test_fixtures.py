"""Guard rails for the bundled sample skill(s): every evals.json must be valid and self-consistent.

The dev-pipeline fixtures these once guarded now live with their skills (under each skill's
``evals/``); this file validates only the in-repo ``samples/`` — the harness's worked example and
structural self-test — against the canonical ``<skill>/evals/evals.json`` layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_harness.deterministic.checks.execution import AcceptanceTest
from eval_harness.schemas import Fixture

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_DIRS = sorted(p for p in SAMPLES_DIR.iterdir() if (p / "evals" / "evals.json").is_file())


def _load(sample_dir: Path) -> Fixture:
    return Fixture.from_json((sample_dir / "evals" / "evals.json").read_text())


def test_at_least_one_sample_present() -> None:
    # Act, Assert — the harness ships a runnable worked example + structural self-test
    assert SAMPLE_DIRS, "no sample skill under samples/"


@pytest.mark.parametrize("sample_dir", SAMPLE_DIRS, ids=lambda p: p.name)
def test_sample_parses(sample_dir: Path) -> None:
    # Act, Assert
    assert _load(sample_dir).evals


@pytest.mark.parametrize("sample_dir", SAMPLE_DIRS, ids=lambda p: p.name)
def test_sample_target_matches_directory(sample_dir: Path) -> None:
    # Act, Assert
    assert _load(sample_dir).target == sample_dir.name


@pytest.mark.parametrize("sample_dir", SAMPLE_DIRS, ids=lambda p: p.name)
def test_sample_seed_files_exist(sample_dir: Path) -> None:
    # Arrange — seeds live alongside evals.json (in the evals/ dir)
    fixture = _load(sample_dir)

    # Act, Assert
    for ev in fixture.evals:
        for rel in ev.files:
            assert (sample_dir / "evals" / rel).is_file(), f"missing seed file: {rel}"


@pytest.mark.parametrize("sample_dir", SAMPLE_DIRS, ids=lambda p: p.name)
def test_sample_acceptance_sources_exist(sample_dir: Path) -> None:
    # Arrange — any held-out acceptance test must actually ship alongside the evals
    fixture = _load(sample_dir)

    # Act, Assert
    for ev in fixture.evals:
        for check in ev.checks:
            if isinstance(check, AcceptanceTest):
                assert (sample_dir / "evals" / check.source).is_file(), (
                    f"{sample_dir.name}/{ev.name}: held-out test missing: {check.source}"
                )
