"""Tests for the runner — execution model B.

The target file's content becomes the run's **system prompt** and the eval's task is the user
prompt; the run happens in an isolated workspace whose only ``.claude`` is an empty marker (nothing
is copied in). The runner's one dependency is the ``llm`` executor (``llm.call``), faked via an
injected ``call`` that returns an ``LLMResponse``; seeding, the marker, and RunResult assembly run
for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval_harness import llm, runner
from eval_harness.schemas import Eval

SESSION = "11111111-2222-3333-4444-555555555555"

RESPONSE = llm.LLMResponse(
    text="Critical: SQL injection in run_query (app.py:8). Use parameterized queries.",
    cost_usd=0.21, input_tokens=900, output_tokens=300,
    cache_read_tokens=None, cache_creation_tokens=None,
    duration_ms=4200, session_id=SESSION, raw={},
)


def make_eval() -> Eval:
    return Eval(id=1, name="planted", prompt="Review app.py.", files=("files/app.py",))


def target_file(tmp_path: Path, content: str = "# code-review\nFind the bug.") -> Path:
    f = tmp_path / "SKILL.md"
    f.write_text(content)

    return f


def fixture_dir_with_app(tmp_path: Path) -> Path:
    seed = tmp_path / "evals"
    (seed / "files").mkdir(parents=True)
    (seed / "files" / "app.py").write_text("# vulnerable code\n")

    return seed


def constant_call(response: llm.LLMResponse = RESPONSE) -> runner.CallFn:
    def _call(
        request: llm.LLMRequest, *, cwd: object = None, env: object = None
    ) -> llm.LLMResponse:
        return response

    return _call


def capturing_call() -> tuple[runner.CallFn, dict[str, object]]:
    captured: dict[str, object] = {}

    def _call(
        request: llm.LLMRequest, *, cwd: object = None, env: object = None
    ) -> llm.LLMResponse:
        captured["request"] = request
        captured["cwd"] = cwd
        captured["env"] = env

        return RESPONSE

    return _call, captured


def run_target(
    tmp_path: Path,
    *,
    target_path: Path | None = None,
    system_prompt: str | None = None,
    call: runner.CallFn | None = None,
) -> runner.RunResult:
    return runner.run(
        make_eval(),
        fixture_dir_with_app(tmp_path),
        target="code-review",
        target_path=target_path or target_file(tmp_path),
        model="claude-opus-4-8", effort="xhigh",
        system_prompt=system_prompt, session_id=SESSION,
        runs_root=tmp_path / "runs",
        call=call or constant_call(),
    )


# --- find_transcript (kept for the judge audit record) ------------------------


def test_find_transcript_locates_session_file(tmp_path: Path) -> None:
    # Arrange
    proj = tmp_path / "slug"
    proj.mkdir()
    (proj / f"{SESSION}.jsonl").write_text("{}")

    # Act, Assert
    assert runner.find_transcript(tmp_path, SESSION) == proj / f"{SESSION}.jsonl"


def test_find_transcript_returns_none_when_absent(tmp_path: Path) -> None:
    # Act, Assert
    assert runner.find_transcript(tmp_path, SESSION) is None


# --- model B: target content is the system prompt -----------------------------


def test_run_uses_target_content_as_system_prompt(tmp_path: Path) -> None:
    # Arrange
    call, captured = capturing_call()

    # Act
    run_target(tmp_path, target_path=target_file(tmp_path, "FIND THE PLANTED BUG"), call=call)

    # Assert
    request = captured["request"]
    assert isinstance(request, llm.LLMRequest)
    assert request.system_prompt == "FIND THE PLANTED BUG"


def test_run_appends_eval_directive_after_target_content(tmp_path: Path) -> None:
    # Arrange — an eval directive (e.g. an autonomous instruction) follows the target's own text
    call, captured = capturing_call()

    # Act
    run_target(
        tmp_path, target_path=target_file(tmp_path, "TARGET INSTRUCTIONS"),
        system_prompt="RUN AUTONOMOUSLY", call=call,
    )

    # Assert
    request = captured["request"]
    assert isinstance(request, llm.LLMRequest)
    sp = request.system_prompt or ""
    assert "TARGET INSTRUCTIONS" in sp and "RUN AUTONOMOUSLY" in sp
    assert sp.index("TARGET INSTRUCTIONS") < sp.index("RUN AUTONOMOUSLY")


def test_run_user_prompt_is_the_eval_task_verbatim(tmp_path: Path) -> None:
    # Arrange — no "Use the X skill" / "/command" wrapper under model B
    call, captured = capturing_call()

    # Act
    run_target(tmp_path, call=call)

    # Assert
    request = captured["request"]
    assert isinstance(request, llm.LLMRequest)
    assert request.prompt == "Review app.py."


def test_run_workspace_claude_marker_is_empty(tmp_path: Path) -> None:
    # Act — nothing is copied into the workspace; the .claude is only a project-root marker
    result = run_target(tmp_path)

    # Assert
    marker = result.workspace / ".claude"
    assert marker.is_dir()
    assert list(marker.iterdir()) == []


def test_run_seeds_workspace_with_fixture_files(tmp_path: Path) -> None:
    # Act — "files/app.py" is placed at workspace root as app.py
    result = run_target(tmp_path)

    # Assert
    assert (result.workspace / "app.py").read_text() == "# vulnerable code\n"


# --- capture + request shape --------------------------------------------------


def test_run_captures_output_and_metrics(tmp_path: Path) -> None:
    # Act
    result = run_target(tmp_path)

    # Assert
    assert "SQL injection" in result.output_text
    assert result.cost_usd == pytest.approx(0.21)
    assert result.input_tokens == 900
    assert result.session_id == SESSION


def test_run_builds_agentic_request_through_the_seam(tmp_path: Path) -> None:
    # Arrange
    call, captured = capturing_call()

    # Act
    run_target(tmp_path, call=call)

    # Assert — model/effort + session-id ride along; run isolated to its workspace + scrubbed env
    request = captured["request"]
    assert isinstance(request, llm.LLMRequest)
    assert request.model == "claude-opus-4-8"
    assert request.effort == "xhigh"
    assert ("--session-id", SESSION) in request.extra_args
    assert captured["cwd"] == tmp_path / "runs" / SESSION
    env = captured["env"]
    assert isinstance(env, dict)
    assert "CLAUDECODE" not in env


def test_run_constrains_toolset_and_drops_bypass(tmp_path: Path) -> None:
    # Arrange
    call, captured = capturing_call()

    # Act
    run_target(tmp_path, call=call)

    # Assert — SEC1: bounded to the explicit allowlist, never bypassPermissions
    request = captured["request"]
    assert isinstance(request, llm.LLMRequest)
    assert request.tools == runner.RUNNER_TOOLS
    assert all(flag != "--permission-mode" for flag, _ in request.extra_args)


def test_run_wraps_llm_error_as_run_error(tmp_path: Path) -> None:
    # Arrange — the seam raises; the runner must surface it as RunError, not leak an LLMError
    def failing(
        request: llm.LLMRequest, *, cwd: object = None, env: object = None
    ) -> llm.LLMResponse:
        raise llm.LLMParseError("model answer was not JSON")

    # Act, Assert
    with pytest.raises(runner.RunError):
        run_target(tmp_path, call=failing)
