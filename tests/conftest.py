"""Shared test infrastructure for the gate and setup suites.

Issue #7 (T7): the fixture git-init, hermetic env construction, PreToolUse
payload building, and the common env-prep both runners need were duplicated in
tests/test_gate.py and tests/test_setup.py — the two copies already cross-
referenced each other in comments ("exactly as test_gate.py does"). This module
consolidates that shared infrastructure; the test modules keep only their own
runner command construction and behavior assertions.

The seam is pytest itself: the acceptance bar is the existing suite (all tests
kept verbatim, all green). No production files are touched.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE_SH = REPO_ROOT / ".claude" / "hooks" / "gate.sh"
TOOL_MAP = REPO_ROOT / ".claude" / "hooks" / "tool_map.sh"
SETUP_SH = REPO_ROOT / "setup.sh"
# Local TS toolchain (biome + tsc) installed at the repo root (package.json +
# pnpm-lock.yaml). Prepended to PATH so the ts:* tool-map commands resolve to the
# real binaries — the gate and setup are exercised end to end, never mocked.
NODE_BIN = REPO_ROOT / "node_modules" / ".bin"
# Gate fixture groups; each fixture dir is git-inited into its own repo so
# gate.sh's repo-root detection isolates the fixture instead of walking up to
# this repo.
PY_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "python"
TS_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ts"
JS_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "js"
BOTH_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "both"
FIXTURE_ROOTS = (PY_FIXTURES, TS_FIXTURES, JS_FIXTURES, BOTH_FIXTURES)


def git_init(path: Path) -> None:
    """git-init ``path`` if it is not already a repo (idempotent)."""
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)


@pytest.fixture(scope="session", autouse=True)
def git_init_fixture_repos() -> None:
    """Make each gate fixture its own git repo for repo-root detection isolation.

    Replaces the old import-time side effect: the fixture `.git` dirs are
    gitignored and not versioned, so a fresh clone ships without them; tests
    (re)create them on demand. Session-scoped and idempotent — the first test
    that runs triggers it once for the whole session.
    """
    for root in FIXTURE_ROOTS:
        for fixture in root.iterdir():
            if fixture.is_dir():
                git_init(fixture)


def hermetic_env() -> dict[str, str]:
    """Environment copy with the caller's QUALITY_GATE_STACK popped (so tests
    never inherit a stray value) and the local TS toolchain's node_modules/.bin
    prepended to PATH."""
    env = dict(os.environ)
    env.pop("QUALITY_GATE_STACK", None)
    if NODE_BIN.is_dir():
        env["PATH"] = f"{NODE_BIN}:{env['PATH']}"
    return env


def make_pre_tool_payload(command: str, tool_name: str = "Bash", cwd: str | None = None) -> dict[str, object]:
    """The PreToolUse stdin JSON payload a Claude Code hook receives."""
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"command": command},
        "cwd": cwd,
    }
