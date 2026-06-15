"""Analyzer: one read-only LLM pass over a benchmark, producing classified observation notes.

Standalone ``analyze`` command (not part of ``all``). Surfaces patterns/anomalies the aggregate
numbers don't show — it does not propose fixes. Each note carries a ``severity`` (``ok`` /
``warning`` / ``issue``) so the CLI can render it with a clear icon. Goes through the ``llm`` seam.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from eval_harness import llm

ANALYZER_PROMPT_VERSION = "analyzer_v1"
SEVERITIES = ("ok", "warning", "issue")

CallJson = Callable[..., "tuple[Any, llm.LLMResponse]"]


@dataclass(frozen=True)
class AnalysisNote:
    """One observation about a benchmark, with a severity the CLI maps to an icon."""

    severity: str
    text: str


def _instructions() -> str:
    return files("eval_harness.prompts").joinpath(f"{ANALYZER_PROMPT_VERSION}.md").read_text()


def _build_prompt(bench: dict[str, Any]) -> str:
    return f"{_instructions()}\n\n## BENCHMARK\n{json.dumps(bench, indent=2)}\n"


def _valid_notes_payload(value: Any) -> bool:
    """Notes must be a list of objects, each with a ``text`` and a ``severity`` in SEVERITIES."""
    if not (isinstance(value, dict) and isinstance(value.get("notes"), list)):
        return False

    return all(
        isinstance(note, dict) and note.get("severity") in SEVERITIES and "text" in note
        for note in value["notes"]
    )


def analyze(
    bench: dict[str, Any],
    *,
    model: str,
    effort: str,
    call_json: CallJson = llm.call_json,
) -> list[AnalysisNote]:
    """Run one analyzer pass over a benchmark dict and return its classified observation notes."""
    request = llm.LLMRequest(prompt=_build_prompt(bench), model=model, effort=effort)
    payload, _ = call_json(request, validate=_valid_notes_payload)

    return [
        AnalysisNote(severity=str(n["severity"]), text=str(n["text"]))
        for n in payload["notes"]
    ]
