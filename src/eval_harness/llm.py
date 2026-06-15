"""The ``llm`` seam: a thin wrapper over headless ``claude -p``.

Turns a prompt into a validated structured response (validate + retry). The single
abstraction point for LLM calls, used by ``judge`` and ``analyzer``; swappable for the
``anthropic`` SDK in v2 (see ``project_files/v2/ideas.md`` §2).

The one external boundary is the subprocess call, injected as ``runner`` so callers (and
tests) can substitute it. ``claude -p --output-format json`` returns a single JSON envelope;
the model's answer is its ``result`` field, with cost/token/duration metrics alongside.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLAUDE_BIN = "claude"

# The subprocess boundary: build an argv and run it in an optional cwd/env. ``cwd``/``env`` are
# ``None`` for ordinary seam calls (judge/analyzer run in-process); the runner sets them to the
# isolated workspace + a scrubbed env for agentic runs.
Runner = Callable[
    [list[str], "Path | None", "dict[str, str] | None"], subprocess.CompletedProcess[str]
]

# Max characters of a rejected value to embed in a validation error message, so a large
# parsed payload can't flood the logs / exception text.
_CLIP_LEN = 200


def _clip(value: object) -> str:
    """Render ``value`` for an error message, truncated so it never floods the log."""
    text = repr(value)

    return text if len(text) <= _CLIP_LEN else text[:_CLIP_LEN] + "…"


class LLMError(Exception):
    """Base error for the ``llm`` seam."""


class LLMTransportError(LLMError):
    """The ``claude -p`` invocation itself failed (non-zero exit or error envelope)."""


class LLMParseError(LLMError):
    """The envelope, or the model's answer within it, could not be parsed."""


class LLMValidationError(LLMError):
    """The parsed answer was rejected by the caller's validator."""


@dataclass(frozen=True)
class LLMRequest:
    """What to ask ``claude -p``: the prompt plus how to run it.

    The single bundle of "what to ask" parameters, shared by ``call`` and ``call_json``. New
    universal knobs become first-class fields; one-off CLI flags ride along in ``extra_args``.
    Transport concerns (the ``runner``, ``cwd``/``env``) and parse concerns (``validate``,
    ``retries``) stay out — they belong to the call, not the request.
    """

    prompt: str
    model: str
    effort: str
    system_prompt: str | None = None
    # The call's capability: the tools it may use, available *and* auto-approved. The empty
    # default is a deliberate deny-all (``--tools ""``) — graders need no tools; only the runner
    # widens it to the agentic set. A hardcoded security policy, never a CLI knob.
    tools: tuple[str, ...] = ()
    # Extra ``claude -p`` flags as (flag, value) pairs, for call shapes the first-class fields
    # don't cover — e.g. the runner's agentic ``--session-id``.
    extra_args: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("prompt must be non-empty")


@dataclass(frozen=True)
class LLMResponse:
    """A parsed ``claude -p`` result: the model's text plus run metrics."""

    text: str
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    duration_ms: int | None
    session_id: str | None
    raw: dict[str, Any]


def _build_command(request: LLMRequest) -> list[str]:
    """Assemble the ``claude -p`` command (argument list) for a single headless call.

    Isolates the call from the user's global config — ``--setting-sources project,local`` drops the
    ``~/.claude`` user settings (and the global CLAUDE.md / hooks they configure) so grading is
    reproducible and uncontaminated by the personal environment. (``--bare`` would isolate more but
    also strips the login credential — "Not logged in" — so it can't be used here.)

    Capability is bounded explicitly, never left to ``--permission-mode default`` resolution:
    ``--tools`` sets the available toolset (``""`` = none, the deny-all default) and
    ``--allowedTools`` auto-approves that same set so the runner's tools run unattended. An empty
    set emits no allowlist.
    """
    # The available set is also the approved set (one field, one value) — so the runner's tools
    # run unattended, while the empty deny-all default has nothing to allow.
    tools_csv = ",".join(request.tools)
    argv = [
        CLAUDE_BIN,
        "-p",
        request.prompt,
        "--output-format",
        "json",
        "--setting-sources",
        "project,local",
        "--model",
        request.model,
        "--effort",
        request.effort,
        "--tools",
        tools_csv,
    ]

    if request.tools:
        argv += ["--allowedTools", tools_csv]

    if request.system_prompt is not None:
        argv += ["--append-system-prompt", request.system_prompt]

    for flag, value in request.extra_args:
        argv += [flag, value]

    return argv


def _pinned_session_id(request: LLMRequest) -> str | None:
    """The caller-supplied ``--session-id`` riding in ``extra_args``, if any."""
    return next((value for flag, value in request.extra_args if flag == "--session-id"), None)


def _default_runner(  # pragma: no cover
    argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``claude -p`` for real in an optional cwd/env, capturing stdout/stderr as text.

    The real subprocess boundary — exercised by the live smoke check, not unit tests (which
    inject a fake ``runner``); covering it here would only test ``subprocess.run`` itself.
    """
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def call(
    request: LLMRequest,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    runner: Runner = _default_runner,
) -> LLMResponse:
    """Run one headless ``claude -p`` call and parse its JSON envelope.

    ``cwd``/``env`` are forwarded to the transport — ``None`` (the seam default) inherits the
    process's directory and environment; the runner passes an isolated workspace + scrubbed env.
    Raises ``LLMTransportError`` if the process failed or returned an error envelope, and
    ``LLMParseError`` if stdout was not the expected JSON shape.

    Every call runs under a known ``--session-id`` — the caller's, or a generated one — so its
    transcript is locatable afterwards; errors embed the id so even a failed call can be audited.
    """
    pinned = _pinned_session_id(request)
    session_id = pinned if pinned is not None else str(uuid.uuid4())
    argv = _build_command(request)

    if pinned is None:
        argv += ["--session-id", session_id]

    completed = runner(argv, cwd, env)

    if completed.returncode != 0:
        raise LLMTransportError(
            f"claude -p exited {completed.returncode}"
            f" (session {session_id}): {completed.stderr.strip()}"
        )

    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"stdout was not JSON: {exc}") from exc

    if not isinstance(envelope, dict):
        raise LLMParseError("envelope was not a JSON object")

    if envelope.get("is_error"):
        raise LLMTransportError(
            f"claude -p returned an error envelope (session {session_id}):"
            f" {envelope.get('subtype')}"
        )

    if "result" not in envelope:
        raise LLMParseError("envelope had no 'result' field")

    usage = envelope.get("usage") or {}

    return LLMResponse(
        text=envelope["result"],
        cost_usd=envelope.get("total_cost_usd"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_input_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        duration_ms=envelope.get("duration_ms"),
        session_id=envelope.get("session_id") or session_id,
        raw=envelope,
    )


def _strip_code_fence(text: str) -> str:
    """Drop a surrounding ```` ```json ... ``` ```` fence, if present.

    Handles both the standard multiline fence and the degenerate single-line form. The payload
    is JSON, so once the outer ``` markers are gone the value starts at the first ``{`` or ``[``;
    anything before it (e.g. a ``json`` language tag) is dropped.
    """
    stripped = text.strip()

    if not stripped.startswith("```"):
        return stripped

    body = stripped[3:]

    if body.endswith("```"):
        body = body[:-3]

    body = body.strip()

    for index, char in enumerate(body):
        if char in "{[":
            return body[index:]

    return body


def call_json(
    request: LLMRequest,
    *,
    validate: Callable[[Any], bool] | None = None,
    retries: int = 1,
    runner: Runner = _default_runner,
) -> tuple[Any, LLMResponse]:
    """Run ``claude -p`` and parse the model's answer as JSON, retrying on bad output.

    Makes up to ``retries + 1`` attempts. Each attempt runs the model, parses its ``result``
    as JSON (tolerating a ```` ```json ```` fence), and — if ``validate`` is given — checks the
    parsed value. Malformed JSON or a rejected value triggers a retry; the last such failure is
    raised as ``LLMParseError`` / ``LLMValidationError`` once attempts are exhausted. Transport
    failures are not retried — they propagate immediately.
    """
    if retries < 0:
        raise ValueError("retries must be >= 0")

    last_error: LLMError | None = None

    for _ in range(retries + 1):
        response = call(request, runner=runner)

        try:
            parsed = json.loads(_strip_code_fence(response.text))
        except json.JSONDecodeError as exc:
            # Keep the raw answer in the error so a later reader can see what the model actually
            # returned (empty? prose? a refusal?) — otherwise the failure is undiagnosable.
            last_error = LLMParseError(
                f"model answer was not JSON: {exc}; raw={_clip(response.text)}"
            )
            continue

        if validate is not None and not validate(parsed):
            last_error = LLMValidationError(f"parsed answer rejected by validator: {_clip(parsed)}")
            continue

        return parsed, response

    assert last_error is not None
    raise last_error
