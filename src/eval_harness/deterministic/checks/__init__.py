"""The deterministic check library: the table mapping a fixture ``type`` to its check class.

Each concrete check lives in a module here; this package collects them into the single ``REGISTRY``
table and exposes :func:`parse_check`, which builds the right check from a fixture's check entry.
**Adding a check = adding its class in a module here and one line to the table below** — both edits
in plain sight, no import-time side effects.
"""

from __future__ import annotations

from typing import Any

from eval_harness.deterministic.base import Check
from eval_harness.deterministic.checks.execution import (
    AcceptanceTest,
    CommandSucceeds,
    CoverageAtLeast,
)
from eval_harness.deterministic.checks.files import FileContains, FileExists
from eval_harness.deterministic.checks.response import ResponseContains, ResponseContainsAny

# Fixture type string → check class. The single source of truth for dispatch; a duplicate type is
# visible right here (unlike scattered self-registration), so no runtime guard is needed.
REGISTRY: dict[str, type[Check]] = {
    "response_contains": ResponseContains,
    "response_contains_any": ResponseContainsAny,
    "file_exists": FileExists,
    "file_contains": FileContains,
    "command_succeeds": CommandSucceeds,
    "coverage_at_least": CoverageAtLeast,
    "acceptance_test": AcceptanceTest,
}


def parse_check(data: dict[str, Any]) -> Check:
    """Build the concrete :class:`Check` for ``data['type']`` from its fixture JSON entry.

    The single ``type``-dispatch in the tier — a table lookup. Any malformed check definition
    (missing ``type``, an unknown type, or a check missing a required field) surfaces as a
    ``ValueError`` naming the problem, never a bare ``KeyError``.
    """
    try:
        type_name = data["type"]
    except KeyError:
        raise ValueError("check definition missing 'type'") from None

    try:
        cls = REGISTRY[type_name]
    except KeyError:
        raise ValueError(f"unknown deterministic check type: {type_name!r}") from None

    try:
        return cls.from_dict(data)
    except KeyError as exc:
        raise ValueError(f"check {type_name!r} missing required field {exc.args[0]!r}") from None
