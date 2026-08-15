"""setup.sh quality-gate installer driven against target-project fixtures.

Issue #4 (T3): the skill setup flow — confirm stack (user declaration, never
auto-detected) -> pick tool-map commands -> install missing tools -> write
.claude/settings.json + copy gate.sh/tool_map.sh -> self-verify the written gate.

The seam is the *artifacts* setup.sh produces in a target project: the tests
assert external behavior only (JSON validity, hook wiring, executable copies,
and the post-write self-verify outcome). gate.sh's own behavior is locked by
tests/test_gate.py and is exercised here end to end via self-verify.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import GATE_SH, REPO_ROOT, SETUP_SH, TOOL_MAP, git_init, hermetic_env

GATE_PARSE = REPO_ROOT / ".claude" / "hooks" / "gate_parse.py"
SETUP_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "setup"

VALID_STACKS = ("python", "ts", "js")


def _make_target(tmp_path: Path, fixture: str, name: str | None = None) -> Path:
    """Copy a fixture into an isolated tmp dir and make it its own git repo (so
    gate.sh's repo-root detection isolates the target instead of walking up to
    this repo, matching how the gate fixtures are set up). ``name`` overrides
    the directory name — used when a test needs special characters in the
    path."""
    target = tmp_path / (name or fixture)
    shutil.copytree(SETUP_FIXTURES / fixture, target)
    git_init(target)
    return target


def run_setup(
    target: Path,
    stack: str | None = None,
    extra_args: tuple[str, ...] = (),
    stdin: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run setup.sh against a target project.

    The environment is hermetic exactly as test_gate.py does, via the shared
    conftest helper: QUALITY_GATE_STACK popped and the local TS toolchain's
    node_modules/.bin prepended to PATH so ts/js stages resolve to the real
    biome/tsc.
    """
    cmd = ["bash", str(SETUP_SH), "--target", str(target)]
    if stack is not None:
        cmd += ["--stack", stack]
    cmd += list(extra_args)
    full_env = hermetic_env()
    if env:
        full_env.update(env)
    return subprocess.run(
        cmd, input=stdin, text=True, capture_output=True, check=False, env=full_env
    )


def _load_settings(target: Path) -> dict:
    path = target / ".claude" / "settings.json"
    assert path.is_file(), f"missing {path}"
    return json.loads(path.read_text())


def _gate_entries(settings: dict) -> list[dict]:
    pretool = settings.get("hooks", {}).get("PreToolUse", [])
    return [
        e
        for e in pretool
        if any(
            isinstance(h, dict)
            and isinstance(h.get("command"), str)
            and h["command"].endswith("/.claude/hooks/gate.sh")
            for h in e.get("hooks", [])
        )
    ]


def _assert_gate_entry(settings: dict, stack: str) -> None:
    """Assert the generated PreToolUse entry is wired per the Claude Code schema."""
    assert settings["env"]["QUALITY_GATE_STACK"] == stack
    hooks = settings["hooks"]
    entries = _gate_entries(settings)
    assert len(entries) == 1, f"expected exactly one gate entry, got {len(entries)}"
    entry = entries[0]
    assert entry["matcher"] == "Bash"
    handler = entry["hooks"][0]
    assert handler["type"] == "command"
    assert handler["command"].startswith("${CLAUDE_PROJECT_DIR}")
    assert handler["command"].endswith("/.claude/hooks/gate.sh")
    assert isinstance(handler["timeout"], int) and handler["timeout"] > 0


# ---------------------------------------------------------------------------
# generated .claude/settings.json: valid JSON + correct PreToolUse hook wiring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "stack"),
    [
        ("python-clean", "python"),
        ("ts-clean", "ts"),
        ("js-clean", "js"),
        ("both-clean", "python,ts"),
    ],
)
def test_setup_writes_valid_settings_with_correct_hook(tmp_path: Path, fixture: str, stack: str) -> None:
    target = _make_target(tmp_path, fixture)
    proc = run_setup(target, stack=stack, extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    settings = _load_settings(target)
    _assert_gate_entry(settings, stack)


def test_setup_copies_gate_and_tool_map_and_makes_gate_executable(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    proc = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    copied_gate = target / ".claude" / "hooks" / "gate.sh"
    copied_parser = target / ".claude" / "hooks" / "gate_parse.py"
    copied_map = target / ".claude" / "hooks" / "tool_map.sh"
    assert copied_gate.is_file()
    assert copied_parser.is_file()
    assert copied_map.is_file()
    assert os.access(copied_gate, os.X_OK), "gate.sh must be executable"
    # The copies are byte-identical to the skill's own hooks — setup must not
    # drift from the single source.
    assert copied_gate.read_text() == GATE_SH.read_text()
    assert copied_parser.read_text() == GATE_PARSE.read_text()
    assert copied_map.read_text() == TOOL_MAP.read_text()


def test_setup_merges_existing_settings_without_clobbering(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    existing = {
        "env": {"MY_VAR": "keep"},
        "permissions": {"allow": ["Bash(git status)"]},
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "/other/hook.sh"}]}
            ]
        },
    }
    settings_dir = target / ".claude"
    settings_dir.mkdir(exist_ok=True)
    (settings_dir / "settings.json").write_text(json.dumps(existing, indent=2))

    proc = run_setup(target, stack="python,ts", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    settings = _load_settings(target)
    # The other hook entry and unrelated top-level keys survive.
    assert settings["permissions"] == {"allow": ["Bash(git status)"]}
    assert settings["env"]["MY_VAR"] == "keep"
    assert settings["env"]["QUALITY_GATE_STACK"] == "python,ts"
    other = [
        e for e in settings["hooks"]["PreToolUse"] if e.get("matcher") == "Edit"
    ]
    assert len(other) == 1
    _assert_gate_entry(settings, "python,ts")


def test_setup_is_idempotent_on_rerun(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    first = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert first.returncode == 0, first.stdout + first.stderr
    second = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert second.returncode == 0, second.stdout + second.stderr
    settings = _load_settings(target)
    # Exactly one gate entry even after a second run — no duplication.
    assert len(_gate_entries(settings)) == 1
    _assert_gate_entry(settings, "python")


# ---------------------------------------------------------------------------
# fail-closed settings.json merging: invalid configs are refused, never
# clobbered; the written bytes keep setup's own artifact biome-clean
# ---------------------------------------------------------------------------


def _write_existing_settings(target: Path, content: str) -> None:
    settings_dir = target / ".claude"
    settings_dir.mkdir(exist_ok=True)
    (settings_dir / "settings.json").write_text(content)


@pytest.mark.parametrize(
    ("content", "marker"),
    [
        ("{ not valid json", "not valid JSON"),
        ("[1, 2, 3]", "not a JSON object"),
        ('{"hooks": {"PreToolUse": "oops"}}', "hooks.PreToolUse that is not a list"),
    ],
)
def test_setup_refuses_invalid_existing_settings(
    tmp_path: Path, content: str, marker: str
) -> None:
    # A broken/hand-written .claude/settings.json must never be overwritten:
    # setup exits 2 with an explicit "refusing to overwrite" and the file is
    # left byte-identical.
    target = _make_target(tmp_path, "python-clean")
    _write_existing_settings(target, content)
    proc = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert marker in proc.stderr
    assert "refusing to overwrite" in proc.stderr
    assert (target / ".claude" / "settings.json").read_text() == content


def test_setup_writes_tab_indented_settings_with_trailing_newline(tmp_path: Path) -> None:
    # The written bytes matter: biome's format stage (tabs by default) must not
    # flag setup's own settings.json on the very first commit. The other tests
    # only json.loads the result back, so this contract is locked explicitly.
    target = _make_target(tmp_path, "python-clean")
    proc = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    raw = (target / ".claude" / "settings.json").read_text()
    indented = [line for line in raw.splitlines()[1:] if line[:1] in ("\t", " ")]
    assert indented, "settings.json must contain indented lines"
    assert all(line.startswith("\t") for line in indented)
    assert raw.endswith("\n")


# ---------------------------------------------------------------------------
# stack confirmation: user declaration is authoritative, never auto-detected
# ---------------------------------------------------------------------------


def test_setup_prompts_for_stack_and_uses_the_declaration(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    proc = run_setup(target, stdin="python\n", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    settings = _load_settings(target)
    assert settings["env"]["QUALITY_GATE_STACK"] == "python"


def test_setup_does_not_auto_detect_stack(tmp_path: Path) -> None:
    # This target looks like a TS project (package.json + tsconfig.json + .ts).
    # Declaring python must win — setup never probes the filesystem to guess.
    target = _make_target(tmp_path, "ts-clean")
    proc = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    settings = _load_settings(target)
    assert settings["env"]["QUALITY_GATE_STACK"] == "python"


def test_setup_rejects_invalid_stack(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    proc = run_setup(target, stack="rust", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 2
    assert "invalid --stack" in proc.stderr


def test_setup_normalizes_stack_listing(tmp_path: Path) -> None:
    # Spaces around the comma (and empty entries) are tolerated, mirroring the
    # gate's QUALITY_GATE_STACK parsing.
    target = _make_target(tmp_path, "python-clean")
    proc = run_setup(target, stack="python, ts,", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    settings = _load_settings(target)
    assert settings["env"]["QUALITY_GATE_STACK"] == "python,ts"


# ---------------------------------------------------------------------------
# self-verify: the written gate is exercised once, problems surface pre-commit
# ---------------------------------------------------------------------------


def test_setup_self_verify_clean_passes(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    proc = run_setup(target, stack="python", extra_args=("--no-install",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-verify: OK" in proc.stdout
    assert "GATE FAILED" not in proc.stdout + proc.stderr


def test_setup_self_verify_js_clean_passes(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "js-clean")
    proc = run_setup(target, stack="js", extra_args=("--no-install",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-verify: OK" in proc.stdout


def test_setup_self_verify_dual_stack_clean_passes(tmp_path: Path) -> None:
    # The dual-stack clean path is the strongest wiring check: it runs both
    # step chains, including biome format over the just-written settings.json
    # (which setup writes biome-clean so the gate doesn't block on its own
    # artifact on the first commit).
    target = _make_target(tmp_path, "both-clean")
    proc = run_setup(target, stack="python,ts", extra_args=("--no-install",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-verify: OK" in proc.stdout
    assert "GATE FAILED" not in proc.stdout + proc.stderr


@pytest.mark.parametrize(
    ("fixture", "stack", "expected_block"),
    [
        ("python-dirty", "python", "GATE FAILED at python:lint"),
        ("both-dirty", "python,ts", "GATE FAILED at ts:lint"),
    ],
)
def test_setup_self_verify_blocks_on_dirty_project(
    tmp_path: Path, fixture: str, stack: str, expected_block: str
) -> None:
    # Setup completes (exit 0) but the self-verify proves the gate is LIVE and
    # currently blocking — the problem surfaces before the first commit.
    target = _make_target(tmp_path, fixture)
    proc = run_setup(target, stack=stack, extra_args=("--no-install",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-verify: the quality gate chain is LIVE" in proc.stdout
    assert expected_block in proc.stdout


def test_setup_self_verify_reaches_judgment_with_special_chars_in_target_path(tmp_path: Path) -> None:
    # A target path whose characters are special in JSON (space, double quote,
    # backslash) must still yield a valid self-verify payload. A hand-built
    # payload would break the JSON and the gate would silently fast-pass, so
    # self-verify must reach the real OK/LIVE judgment instead of misreporting.
    target = _make_target(tmp_path, "python-dirty", name='proj "quoted" \' \\ back')
    proc = run_setup(target, stack="python", extra_args=("--no-install",))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "self-verify: the quality gate chain is LIVE" in proc.stdout
    assert "GATE FAILED at python:lint" in proc.stdout
    assert "self-verify: OK" not in proc.stdout


def test_setup_self_verify_skipped_with_flag(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-dirty")
    proc = run_setup(target, stack="python", extra_args=("--no-install", "--no-self-verify"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "(--no-self-verify: skipped)" in proc.stdout
    assert "GATE FAILED" not in proc.stdout + proc.stderr


# ---------------------------------------------------------------------------
# tool installation: detect missing tools, install with the right package
# manager, and fail loudly (never silently) when installation is impossible
# ---------------------------------------------------------------------------


def _mock_tool(bin_dir: Path, name: str) -> None:
    script = f"#!/bin/sh\necho \"{name} $*\" >> \"$MOCK_LOG\"\nexit 0\n"
    tool = bin_dir / name
    tool.write_text(script)
    tool.chmod(0o755)


def _tool_minus_path(tmp_path: Path, mocks: tuple[str, ...], log: Path) -> dict[str, str]:
    """A PATH that hides the venv/TS toolchain (so every tool looks missing) and
    exposes only mock package managers plus the macOS system dirs."""
    bin_dir = tmp_path / "mock-bin"
    bin_dir.mkdir(exist_ok=True)
    for name in mocks:
        _mock_tool(bin_dir, name)
    env = hermetic_env()
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MOCK_LOG"] = str(log)
    return env


def test_setup_installs_missing_python_tools_with_uv(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "python-clean")
    log = tmp_path / "uv.log"
    env = _tool_minus_path(tmp_path, mocks=("uv",), log=log)
    proc = run_setup(target, stack="python", extra_args=("--no-self-verify",), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    invocations = log.read_text()
    assert "uv tool install ruff" in invocations
    assert "uv tool install ty" in invocations


def test_setup_js_install_uses_biome_not_typescript(tmp_path: Path) -> None:
    target = _make_target(tmp_path, "js-clean")
    log = tmp_path / "pnpm.log"
    env = _tool_minus_path(tmp_path, mocks=("pnpm",), log=log)
    proc = run_setup(target, stack="js", extra_args=("--no-self-verify",), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    invocations = log.read_text()
    assert "pnpm add -D @biomejs/biome" in invocations
    assert "typescript" not in invocations


def test_setup_ts_install_uses_pnpm_add_for_biome_and_typescript(tmp_path: Path) -> None:
    # pnpm/yarn install devDeps with `add`; the TS stack needs biome AND typescript.
    target = _make_target(tmp_path, "ts-clean")
    log = tmp_path / "pnpm.log"
    env = _tool_minus_path(tmp_path, mocks=("pnpm",), log=log)
    proc = run_setup(target, stack="ts", extra_args=("--no-self-verify",), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    invocations = log.read_text()
    assert "pnpm add -D @biomejs/biome" in invocations
    assert "pnpm add -D typescript" in invocations


def test_setup_ts_install_uses_npm_install_when_only_npm_present(tmp_path: Path) -> None:
    # npm has no `add` subcommand — the fallback package manager must use `install`.
    target = _make_target(tmp_path, "ts-clean")
    log = tmp_path / "npm.log"
    env = _tool_minus_path(tmp_path, mocks=("npm",), log=log)
    proc = run_setup(target, stack="ts", extra_args=("--no-self-verify",), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    invocations = log.read_text()
    assert "npm install -D @biomejs/biome" in invocations
    assert "npm install -D typescript" in invocations
    assert "npm add" not in invocations


def test_setup_skips_install_when_tool_already_in_node_modules_bin(tmp_path: Path) -> None:
    # A TS/JS tool already in the project's node_modules/.bin counts as present:
    # re-running setup must not reinstall (gate.sh picks it up via its own PATH
    # prepend), and a successful install must not warn just because the bin dir
    # is off this script's PATH.
    target = _make_target(tmp_path, "js-clean")
    bin_dir = target / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    _mock_tool(bin_dir, "biome")
    log = tmp_path / "pnpm.log"
    env = _tool_minus_path(tmp_path, mocks=("pnpm",), log=log)
    proc = run_setup(target, stack="js", extra_args=("--no-self-verify",), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "biome is available" in proc.stdout
    # pnpm must never have been invoked — the tool was already present.
    assert not log.exists(), f"install should be skipped; pnpm log: {log.read_text()}"


def test_setup_cannot_install_prints_explicit_warning(tmp_path: Path) -> None:
    # No uv/pnpm/npm on PATH and no tools: setup must not fail silently.
    target = _make_target(tmp_path, "python-clean")
    log = tmp_path / "none.log"
    env = _tool_minus_path(tmp_path, mocks=(), log=log)
    proc = run_setup(target, stack="python", extra_args=("--no-self-verify",), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cannot install ruff" in proc.stderr
    assert "cannot install ty" in proc.stderr
