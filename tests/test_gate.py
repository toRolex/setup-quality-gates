"""Issue #2 (T1): gate.sh quality-gate core driven by a fabricated PreToolUse payload.

The seam is ``gate.sh``: tests feed it a PreToolUse stdin JSON payload (as Claude
Code would) and assert external behavior only — exit code and stderr stage report.
No implementation details of gate.sh are asserted.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SH = REPO_ROOT / ".claude" / "hooks" / "gate.sh"
TOOL_MAP = REPO_ROOT / ".claude" / "hooks" / "tool_map.sh"
PY_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "python"

COMMIT_MSG = "wip"


def _ensure_fixture_repos() -> None:
    """Make each fixture its own git repo so gate.sh's repo-root detection isolates
    the fixture instead of walking up to this repo. The fixture `.git` dirs are
    gitignored and not versioned, so a fresh clone ships without them; tests
    (re)create them on demand. Idempotent."""
    for fixture in PY_FIXTURES.iterdir():
        if not fixture.is_dir():
            continue
        if not (fixture / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)


_ensure_fixture_repos()


def run_gate(fixture: str, command: str, tool_name: str = "Bash") -> subprocess.CompletedProcess[str]:
    """Run gate.sh the way the Claude Code PreToolUse hook would: stdin JSON, exit code + stderr."""
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "cwd": str(PY_FIXTURES / fixture),
    }
    return subprocess.run(
        ["bash", str(GATE_SH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# commit + stage outcomes
# ---------------------------------------------------------------------------


def test_commit_clean_passes() -> None:
    proc = run_gate("clean", f'git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 0, proc.stderr
    assert "GATE FAILED" not in proc.stderr


@pytest.mark.parametrize(
    ("fixture", "expected_stage", "diagnostic"),
    [
        ("lint-dirty", "lint", "F401"),
        ("format-dirty", "format", "would be reformatted"),
        ("typecheck-dirty", "typecheck", "invalid-return-type"),
    ],
)
def test_dirty_fixture_fails_at_expected_stage(fixture: str, expected_stage: str, diagnostic: str) -> None:
    proc = run_gate(fixture, f'git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert f"GATE FAILED at python:{expected_stage}" in proc.stderr
    assert diagnostic in proc.stderr
    # Fail-fast: the step chain stops at the first failing stage.
    for stage in ("lint", "format", "typecheck"):
        if stage != expected_stage:
            assert f"GATE FAILED at python:{stage}" not in proc.stderr


# ---------------------------------------------------------------------------
# non-commit commands fast-pass without running the gate
# ---------------------------------------------------------------------------


def test_non_commit_commands_skip_the_gate() -> None:
    for command in ("git status", "git log --oneline", "git push", "git add -A", "git diff"):
        # lint-dirty would fail if the gate ran — it must not run here.
        proc = run_gate("lint-dirty", command)
        assert proc.returncode == 0, f"{command!r}: {proc.stdout + proc.stderr}"
        assert "GATE FAILED" not in proc.stderr, f"{command!r} must not trigger the gate"


def test_non_bash_tool_events_pass_through() -> None:
    proc = run_gate("clean", "git commit -m 'x'", tool_name="Edit")
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# compound commands still run the gate
# ---------------------------------------------------------------------------


def test_compound_command_runs_gate_on_dirty_repo() -> None:
    proc = run_gate("lint-dirty", f'git add -A && git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 2
    assert "GATE FAILED at python:lint" in proc.stderr


def test_compound_command_passes_on_clean_repo() -> None:
    proc = run_gate("clean", f'git add -A && git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 0, proc.stderr


def test_compound_command_with_semicolon_runs_gate() -> None:
    proc = run_gate("format-dirty", f'git add -A; git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 2
    assert "GATE FAILED at python:format" in proc.stderr


def test_commit_with_ampersand_in_message_still_triggers() -> None:
    # A naive &&-split on the raw command would mis-split inside the quoted message.
    proc = run_gate("lint-dirty", 'git commit -m "a && b"')
    assert proc.returncode == 2
    assert "GATE FAILED at python:lint" in proc.stderr


def test_echo_of_git_commit_does_not_trigger() -> None:
    # "git commit" as a quoted argument of another command must not trip the gate.
    proc = run_gate("lint-dirty", 'echo "git commit"')
    assert proc.returncode == 0
    assert "GATE FAILED" not in proc.stderr


def test_echo_git_commit_unquoted_does_not_trigger() -> None:
    # A bare `echo git commit` must not be mistaken for a commit.
    proc = run_gate("lint-dirty", "echo git commit")
    assert proc.returncode == 0
    assert "GATE FAILED" not in proc.stderr


def test_git_with_option_value_or_prefix_still_triggers_gate() -> None:
    # `git -C <dir> commit` (option-with-value) and `sudo git commit` (prefix)
    # are both real commits and must run the gate.
    for command in (
        f'git -C . commit -m "{COMMIT_MSG}"',
        f'sudo git commit -m "{COMMIT_MSG}"',
    ):
        proc = run_gate("lint-dirty", command)
        assert proc.returncode == 2, f"{command!r}: {proc.stderr}"
        assert "GATE FAILED at python:lint" in proc.stderr


# ---------------------------------------------------------------------------
# gate loop: every attempt restarts from lint, no cross-call state
# ---------------------------------------------------------------------------


def test_gate_loop_restarts_from_lint_every_attempt() -> None:
    # Two consecutive attempts on a format-dirty repo must both fail at format —
    # the second attempt must not "remember" anything from the first.
    first = run_gate("format-dirty", f'git commit -m "{COMMIT_MSG}"')
    second = run_gate("format-dirty", f'git commit -m "{COMMIT_MSG}"')
    assert first.returncode == 2 and second.returncode == 2
    assert "GATE FAILED at python:format" in first.stderr
    assert "GATE FAILED at python:format" in second.stderr
    # Stateless: gate.sh must leave no trace in the repo it ran against.
    leftovers = [p for p in (PY_FIXTURES / "format-dirty").iterdir() if p.name.startswith(".gate")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# feedback readability
# ---------------------------------------------------------------------------


def test_failure_feedback_contains_error_details() -> None:
    proc = run_gate("lint-dirty", f'git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 2
    assert proc.stderr.startswith("GATE FAILED at python:lint:")
    # The actual tool diagnostic is part of the feedback, so the agent can act on it.
    assert "F401" in proc.stderr


# ---------------------------------------------------------------------------
# single tool map source
# ---------------------------------------------------------------------------


def test_tool_map_is_the_single_source_for_commands() -> None:
    map_text = TOOL_MAP.read_text()
    assert "ruff check ." in map_text
    assert "ruff format --check ." in map_text
    assert "ty check ." in map_text

    gate_text = GATE_SH.read_text()
    # The commands must live only in tool_map.sh, not be duplicated in gate.sh.
    assert "ruff check ." not in gate_text
    assert "ruff format --check ." not in gate_text
    assert "ty check ." not in gate_text
