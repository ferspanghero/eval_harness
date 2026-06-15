"""S1 smoke check: the package and every module import cleanly."""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "eval_harness",
    "eval_harness.cli",
    "eval_harness.runner",
    "eval_harness.deterministic",
    "eval_harness.judge",
    "eval_harness.analyzer",
    "eval_harness.benchmark",
    "eval_harness.llm",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports_cleanly(module_name: str) -> None:
    # Act
    module = importlib.import_module(module_name)

    # Assert
    assert module is not None
