"""Runner: execute one eval headless via ``claude -p`` and capture artifacts (execution model B).

The **target file's content is the run's system prompt** and the eval's task is the user prompt.
The instruction-under-test is fed directly; nothing is copied into the harness. Each eval runs in
its own isolated workspace **outside the repo** (seed files copied in, plus an **empty ``.claude``
marker** so the workspace is its own project root — see ``_seed_workspace`` — keeping file-producing
targets writing into the workspace, not the real tree). The run uses a fixed ``--session-id``.

The actual ``claude -p`` call goes through the one ``llm`` seam — the runner depends on its public
executor ``llm.call`` (injected as ``call`` for tests), not the private subprocess transport.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from eval_harness import llm
from eval_harness.schemas import Eval

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# SEC1: the run is bounded to this explicit allowlist instead of ``--permission-mode
# bypassPermissions``. These are the tools graded targets actually use — read/search the seeded
# files, write the produced artifacts, shell out for read-only exploration, and invoke a skill.
# Anything unlisted (network, sub-agents, MCP) is neither available nor approved. Hardcoded, never a
# CLI knob.
RUNNER_TOOLS = ("Read", "Glob", "Grep", "Edit", "Write", "Bash", "Skill", "TodoWrite")

# The runner depends on the seam's public executor, not its private subprocess transport — injected
# with a fake in tests (mirrors how judge/analyzer take ``call_json``).
CallFn = Callable[..., llm.LLMResponse]


class RunError(Exception):
    """The headless ``claude -p`` run failed (non-zero exit or unparseable output)."""


@dataclass(frozen=True)
class RunResult:
    """The captured outcome of one headless run."""

    eval_id: int
    target: str
    workspace: Path
    output_text: str
    session_id: str
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    duration_ms: int | None


def _run_env() -> dict[str, str]:
    """The environment for the run: inherit, minus CLAUDECODE so a nested ``claude -p`` works."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def _seed_workspace(ev: Eval, fixture_dir: Path, workspace: Path) -> None:
    """Make the workspace a self-contained project root: an empty ``.claude`` marker + seed files.

    ``claude -p`` writes files relative to the project root it discovers (the nearest ancestor with
    a ``.claude``), not the process cwd. So the workspace carries its own **empty** ``.claude`` — a
    marker only, nothing copied in — otherwise file-producing targets write into the real tree.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".claude").mkdir(exist_ok=True)

    for rel in ev.files:
        source = fixture_dir / rel
        dest_rel = rel[len("files/") :] if rel.startswith("files/") else rel
        dest = workspace / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(source.read_bytes())


def find_transcript(projects_dir: Path, session_id: str) -> Path | None:
    """Locate the transcript for a session by id, across project slug dirs."""
    matches = sorted(projects_dir.glob(f"*/{session_id}.jsonl"))

    return matches[0] if matches else None


def _system_prompt(target_path: Path, directive: str | None) -> str:
    """The run's system prompt: the target file's content, then any eval directive after it."""
    instructions = target_path.read_text(encoding="utf-8")

    return "\n\n".join(part for part in (instructions, directive) if part)


def run(
    ev: Eval,
    fixture_dir: Path,
    *,
    target: str,
    target_path: Path,
    model: str,
    effort: str,
    system_prompt: str | None = None,
    session_id: str | None = None,
    runs_root: Path,
    call: CallFn = llm.call,
) -> RunResult:
    """Run one eval headless and capture its output + metrics (execution model B).

    The ``target_path`` file's content becomes the run's system prompt (joined with any
    ``system_prompt`` directive, e.g. a full-pipeline autonomous instruction); the eval's ``prompt``
    is the user task. The run executes through the one ``llm`` seam (``call``) — an agentic
    ``LLMRequest`` bounded to :data:`RUNNER_TOOLS`, with the run's ``--session-id`` riding along —
    in an isolated workspace seeded with the eval's files and an empty ``.claude`` marker.
    """
    session_id = session_id or str(uuid.uuid4())
    workspace = runs_root / session_id
    _seed_workspace(ev, fixture_dir, workspace)

    request = llm.LLMRequest(
        prompt=ev.prompt,
        model=model,
        effort=effort,
        system_prompt=_system_prompt(target_path, system_prompt),
        tools=RUNNER_TOOLS,
        extra_args=(("--session-id", session_id),),
    )
    try:
        response = call(request, cwd=workspace, env=_run_env())
    except llm.LLMError as exc:
        raise RunError(str(exc)) from exc

    return RunResult(
        eval_id=ev.id,
        target=target,
        workspace=workspace,
        output_text=response.text,
        session_id=session_id,
        cost_usd=response.cost_usd,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cache_read_tokens=response.cache_read_tokens,
        cache_creation_tokens=response.cache_creation_tokens,
        duration_ms=response.duration_ms,
    )
