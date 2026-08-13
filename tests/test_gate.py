"""gate.sh quality-gate core driven by a fabricated PreToolUse payload.

Issue #2 (T1): the python single-stack gate. Issue #3 (T2): the TypeScript stack
(``ts``) and dual-stack support via a comma-separated ``QUALITY_GATE_STACK``
(e.g. ``python,ts``).

The seam is ``gate.sh``: tests feed it a PreToolUse stdin JSON payload (as Claude
Code would) and assert external behavior only — exit code and stderr stage report.
No implementation details of gate.sh are asserted.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SH = REPO_ROOT / ".claude" / "hooks" / "gate.sh"
TOOL_MAP = REPO_ROOT / ".claude" / "hooks" / "tool_map.sh"
SETUP_SH = REPO_ROOT / "setup.sh"
PY_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "python"
TS_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ts"
JS_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "js"
BOTH_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "both"
FIXTURE_ROOTS = (PY_FIXTURES, TS_FIXTURES, JS_FIXTURES, BOTH_FIXTURES)
# Local TS toolchain (biome + tsc) installed at the repo root (package.json +
# pnpm-lock.yaml). Prepended to PATH so the ts:* tool-map commands resolve to the
# real binaries — the gate is exercised end to end, never mocked.
NODE_BIN = REPO_ROOT / "node_modules" / ".bin"

COMMIT_MSG = "wip"


def _ensure_fixture_repos() -> None:
    """Make each fixture its own git repo so gate.sh's repo-root detection isolates
    the fixture instead of walking up to this repo. The fixture `.git` dirs are
    gitignored and not versioned, so a fresh clone ships without them; tests
    (re)create them on demand. Idempotent. Applies to every fixture group
    (python, ts, both)."""
    for root in FIXTURE_ROOTS:
        for fixture in root.iterdir():
            if not fixture.is_dir():
                continue
            if not (fixture / ".git").exists():
                subprocess.run(["git", "init", "-q"], cwd=fixture, check=True)


_ensure_fixture_repos()


def run_gate(
    fixture: str,
    command: str,
    tool_name: str = "Bash",
    fixtures: Path = PY_FIXTURES,
    stack: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run gate.sh the way the Claude Code PreToolUse hook would: stdin JSON, exit code + stderr.

    ``stack`` maps to the QUALITY_GATE_STACK env var (None = unset, so gate.sh's
    default single-stack ``python`` applies). ``fixtures`` selects the fixture group.
    """
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "cwd": str(fixtures / fixture),
    }
    env = dict(os.environ)
    env.pop("QUALITY_GATE_STACK", None)  # hermetic: don't inherit the caller's setting
    if stack is not None:
        env["QUALITY_GATE_STACK"] = stack
    if NODE_BIN.is_dir():
        env["PATH"] = f"{NODE_BIN}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(GATE_SH)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env=env,
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
# TS stack (issue #3): biome lint / biome format / tsc --noEmit
# ---------------------------------------------------------------------------


def test_ts_clean_passes() -> None:
    proc = run_gate("clean", f'git commit -m "{COMMIT_MSG}"', fixtures=TS_FIXTURES, stack="ts")
    assert proc.returncode == 0, proc.stderr
    assert "GATE FAILED" not in proc.stderr


@pytest.mark.parametrize(
    ("fixture", "expected_stage", "diagnostic"),
    [
        ("lint-dirty", "lint", "noDebugger"),
        ("format-dirty", "format", "Formatter would have printed"),
        ("typecheck-dirty", "typecheck", "TS2322"),
    ],
)
def test_ts_dirty_fixture_fails_at_expected_stage(fixture: str, expected_stage: str, diagnostic: str) -> None:
    proc = run_gate(fixture, f'git commit -m "{COMMIT_MSG}"', fixtures=TS_FIXTURES, stack="ts")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert f"GATE FAILED at ts:{expected_stage}" in proc.stderr
    assert diagnostic in proc.stderr
    # Fail-fast: only the first failing stage is reported.
    for stage in ("lint", "format", "typecheck"):
        if stage != expected_stage:
            assert f"GATE FAILED at ts:{stage}" not in proc.stderr


def test_ts_non_commit_commands_skip_the_gate() -> None:
    proc = run_gate("lint-dirty", "git status", fixtures=TS_FIXTURES, stack="ts")
    assert proc.returncode == 0, proc.stderr
    assert "GATE FAILED" not in proc.stderr


# ---------------------------------------------------------------------------
# JS stack (issue #4): biome lint / biome format, typecheck is a documented
# no-op (`true`) because JS has no typechecker
# ---------------------------------------------------------------------------


def test_js_clean_passes() -> None:
    proc = run_gate("clean", f'git commit -m "{COMMIT_MSG}"', fixtures=JS_FIXTURES, stack="js")
    assert proc.returncode == 0, proc.stderr
    assert "GATE FAILED" not in proc.stderr


@pytest.mark.parametrize(
    ("fixture", "expected_stage", "diagnostic"),
    [
        ("lint-dirty", "lint", "noDebugger"),
        ("format-dirty", "format", "Formatter would have printed"),
    ],
)
def test_js_dirty_fixture_fails_at_expected_stage(
    fixture: str, expected_stage: str, diagnostic: str
) -> None:
    proc = run_gate(fixture, f'git commit -m "{COMMIT_MSG}"', fixtures=JS_FIXTURES, stack="js")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert f"GATE FAILED at js:{expected_stage}" in proc.stderr
    assert diagnostic in proc.stderr
    # Fail-fast: the js:typecheck no-op stage is never reached.
    assert "GATE FAILED at js:typecheck" not in proc.stderr


def test_js_non_commit_commands_skip_the_gate() -> None:
    proc = run_gate("lint-dirty", "git status", fixtures=JS_FIXTURES, stack="js")
    assert proc.returncode == 0, proc.stderr
    assert "GATE FAILED" not in proc.stderr


# ---------------------------------------------------------------------------
# dual stack (issue #3): QUALITY_GATE_STACK="python,ts" runs both step chains
# ---------------------------------------------------------------------------


def test_dual_stack_clean_passes() -> None:
    proc = run_gate("clean", f'git commit -m "{COMMIT_MSG}"', fixtures=BOTH_FIXTURES, stack="python,ts")
    assert proc.returncode == 0, proc.stderr
    assert "GATE FAILED" not in proc.stderr


@pytest.mark.parametrize(
    ("fixture", "expected_stack", "expected_stage", "diagnostic"),
    [
        ("python-lint-dirty", "python", "lint", "F401"),
        ("python-format-dirty", "python", "format", "would be reformatted"),
        ("python-typecheck-dirty", "python", "typecheck", "invalid-return-type"),
        ("ts-lint-dirty", "ts", "lint", "noDebugger"),
        ("ts-format-dirty", "ts", "format", "Formatter would have printed"),
        ("ts-typecheck-dirty", "ts", "typecheck", "TS2322"),
    ],
)
def test_dual_stack_fails_at_expected_stack_and_stage(
    fixture: str, expected_stack: str, expected_stage: str, diagnostic: str
) -> None:
    proc = run_gate(fixture, f'git commit -m "{COMMIT_MSG}"', fixtures=BOTH_FIXTURES, stack="python,ts")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    # Feedback distinguishes stack AND stage: <stack>:<stage>.
    assert f"GATE FAILED at {expected_stack}:{expected_stage}" in proc.stderr
    assert diagnostic in proc.stderr
    # Fail-fast across both stacks: no other stack:stage is reported.
    for stack in ("python", "ts"):
        for stage in ("lint", "format", "typecheck"):
            if (stack, stage) != (expected_stack, expected_stage):
                assert f"GATE FAILED at {stack}:{stage}" not in proc.stderr


def test_dual_stack_runs_both_chains_before_failing() -> None:
    # The python side is clean and the TS side fails at its LAST stage
    # (typecheck). For the gate to report ts:typecheck, the entire python chain
    # (lint, format, typecheck) plus ts lint and ts format must all have passed
    # first — proving both stacks really ran, not just the failing one.
    proc = run_gate("ts-typecheck-dirty", f'git commit -m "{COMMIT_MSG}"', fixtures=BOTH_FIXTURES, stack="python,ts")
    assert proc.returncode == 2
    assert "GATE FAILED at ts:typecheck" in proc.stderr
    assert "TS2322" in proc.stderr
    assert "GATE FAILED at python:" not in proc.stderr


def test_default_stack_is_python() -> None:
    # QUALITY_GATE_STACK unset -> gate.sh defaults to the single python stack.
    proc = run_gate("lint-dirty", f'git commit -m "{COMMIT_MSG}"')
    assert proc.returncode == 2, proc.stderr
    assert "GATE FAILED at python:lint" in proc.stderr


@pytest.mark.parametrize(
    ("stack_val", "expected"),
    [
        # Whitespace around the comma is tolerated.
        ("python, ts", "clean"),
        (" ts ,python ", "clean"),
        # Empty entries from stray separators are skipped — never a fall-through
        # tool-map lookup under an empty stack label.
        ("python,,ts", "clean"),
        ("python,ts,", "clean"),
        # With the python side dirty, an empty entry must not hijack the report.
        ("ts,,python", "python-lint-dirty"),
    ],
)
def test_dual_stack_parsing_robustness(stack_val: str, expected: str) -> None:
    fixture = "clean" if expected == "clean" else "python-lint-dirty"
    proc = run_gate(fixture, f'git commit -m "{COMMIT_MSG}"', fixtures=BOTH_FIXTURES, stack=stack_val)
    if expected == "clean":
        assert proc.returncode == 0, proc.stderr
        assert "GATE FAILED" not in proc.stderr
    else:
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "GATE FAILED at python:lint" in proc.stderr
        # The empty entry must not surface as "GATE FAILED at :lint:".
        assert "GATE FAILED at :" not in proc.stderr


# ---------------------------------------------------------------------------
# single tool map source
# ---------------------------------------------------------------------------


def test_tool_map_is_the_single_source_for_commands() -> None:
    map_text = TOOL_MAP.read_text()
    # Python stack (issue #2).
    assert "ruff check ." in map_text
    assert "ruff format --check ." in map_text
    assert "ty check ." in map_text
    # TypeScript stack (issue #3).
    assert "biome lint ." in map_text
    assert "biome format ." in map_text
    assert "tsc --noEmit" in map_text
    # JavaScript stack (issue #4): biome for lint/format, no-op typecheck.
    assert "js:lint:biome lint ." in map_text
    assert "js:format:biome format ." in map_text
    assert "js:typecheck:true" in map_text

    gate_text = GATE_SH.read_text()
    setup_text = SETUP_SH.read_text()
    # The commands must live only in tool_map.sh, not be duplicated in gate.sh
    # or setup.sh.
    for command in (
        "ruff check .",
        "ruff format --check .",
        "ty check .",
        "biome lint .",
        "biome format .",
        "tsc --noEmit",
        "js:lint:",
        "js:format:",
        "js:typecheck:",
    ):
        assert command not in gate_text
        assert command not in setup_text


# ---------------------------------------------------------------------------
# tool map as the stack registry: install records and stage records agree
# ---------------------------------------------------------------------------

STAGE_KEYS = ("lint", "format", "typecheck")
# Install strategies setup.sh knows how to dispatch: "uv" (uv tool install)
# and "node" (probed pnpm/npm with the add/install verb).
KNOWN_INSTALL_STRATEGIES = ("uv", "node")


def _tool_map_rows() -> list[list[str]]:
    """Data rows of tool_map.sh as colon-split fields (comments/blanks dropped)."""
    rows = []
    for line in TOOL_MAP.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(line.split(":"))
    return rows


def test_tool_map_is_the_stack_registry() -> None:
    rows = _tool_map_rows()
    # Every data row is a known record kind: a stage row or an install row.
    # A typo'd kind ("installer", "lintt") must not slip through.
    for row in rows:
        assert len(row) >= 3, f"malformed row: {row}"
        assert row[1] in (*STAGE_KEYS, "install"), f"unknown record kind: {row}"

    stacks = sorted({row[0] for row in rows})
    for stack in stacks:
        # The uniform step chain: every stack runs lint -> format -> typecheck.
        stage_rows = {row[1]: row for row in rows if row[0] == stack and row[1] in STAGE_KEYS}
        assert set(stage_rows) == set(STAGE_KEYS), f"{stack}: incomplete step chain"
        # Every stack declares at least one install record, and each one is
        # well-formed: <stack>:install:<tool>:<strategy>:<package>.
        install_rows = [row for row in rows if row[0] == stack and row[1] == "install"]
        assert install_rows, f"{stack}: no install records"
        install_tools = set()
        for row in install_rows:
            assert len(row) >= 5, f"malformed install row: {row}"
            assert row[3] in KNOWN_INSTALL_STRATEGIES, f"unknown install strategy: {row}"
            package = ":".join(row[4:])
            assert package, f"empty package in install row: {row}"
            install_tools.add(row[2])
        # Every tool a stage command invokes must be installable from the map,
        # so a clean environment can be set up. `true` (the js typecheck no-op)
        # is not a tool.
        for row in stage_rows.values():
            command = ":".join(row[2:])
            tool = command.split()[0]
            if tool == "true":
                continue
            assert tool in install_tools, f"{stack}:{row[1]} invokes {tool!r} with no install record"
