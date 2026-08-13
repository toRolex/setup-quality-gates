#!/usr/bin/env bash
#
# gate.sh — Claude Code PreToolUse hook enforcing a commit-time quality gate.
#
# Contract (ADR-0001):
#   * Input:  PreToolUse event JSON on stdin (tool_name="Bash").
#   * Non-commit commands fast-pass: exit 0, the gate never runs.
#   * A command that invokes `git commit` (directly or inside a compound
#     command like `git add … && git commit …`) runs the full step chain
#     lint -> format -> typecheck from the repo root for every configured
#     stack. QUALITY_GATE_STACK is a comma-separated stack list — a single
#     stack ("python" or "ts") or both ("python,ts").
#   * The first failing stage stops the chain; stderr gets
#         GATE FAILED at <stack>:<stage>:
#           <tool output>
#     and the hook exits 2, so Claude Code blocks the tool and the agent sees
#     the failure as actionable feedback.
#   * All stages green -> exit 0.
#   * Stateless by design: every invocation restarts from lint (gate loop),
#     so there is no cross-call state to maintain or corrupt.
#
# Portability decision (issue #2):
#   * No jq. The stdin JSON is parsed with python3, which ships on macOS and
#     on Linux; Python is already the gated stack, so this adds no new class
#     of dependency.
#   * Bash 3.2-compatible on purpose (macOS system bash): no associative
#     arrays, no bash-4-only syntax.
#   * If python3 is missing the gate fails OPEN (exit 0) with a warning — a
#     broken parser must not block every Bash tool call.
#   * The tool commands are NOT embedded here; they live in tool_map.sh, the
#     stack registry the setup flow (#4) also reads. The gate only ever looks
#     up lint/format/typecheck rows; install rows are invisible to it.

set -u

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL_MAP="$HOOK_DIR/tool_map.sh"
STACKS="${QUALITY_GATE_STACK:-python}"

# --- parse the PreToolUse payload ------------------------------------------
# Emits two lines on stdout:
#   line 1: "1" if the command invokes `git commit`, else "0"
#   line 2: the cwd from the event
PY_PARSE=$(cat <<'PY'
import json
import shlex
import sys

SEPARATORS = {"&&", "||", ";", "|"}
GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"}
GIT_PREFIXES = {"sudo", "env", "command", "nohup", "time", "builtin"}


def command_contains_commit(command):
    try:
        # punctuation_chars is a constructor arg (read-only property on Python 3.14+).
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    for i, token in enumerate(tokens):
        if token != "git":
            continue
        prev = tokens[i - 1] if i > 0 else None
        if prev is not None and prev not in SEPARATORS and prev not in GIT_PREFIXES:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j] not in SEPARATORS:
            t = tokens[j]
            if t in GIT_OPTS_WITH_VALUE:
                j += 2
            elif t.startswith("-") and "=" not in t:
                j += 1
            else:
                break
        if j < len(tokens) and tokens[j] == "commit":
            return True
    return False


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        print("0")
        print(".")
        return
    if data.get("tool_name") != "Bash":
        print("0")
        print(".")
        return
    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or ""
    cwd = data.get("cwd") or "."
    print("1" if command_contains_commit(command) else "0")
    print(cwd)


main()
PY
)

if ! command -v python3 >/dev/null 2>&1; then
    echo "GATE WARNING: python3 not found; quality gate disabled (fail open)." >&2
    exit 0
fi

HOOK_JSON="$(cat)"
COMMIT_FLAG=0
CWD=.
{
    read -r COMMIT_FLAG
    read -r CWD
} < <(printf '%s' "$HOOK_JSON" | python3 -c "$PY_PARSE")

# Non-commit commands fast-pass — including git status/log/push/add/diff.
if [ "$COMMIT_FLAG" != "1" ]; then
    exit 0
fi

# Locate the repo root so the gate checks the whole repo, not just the cwd.
REPO_ROOT="$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || printf '%s' "$CWD")"

# The TS/JS stages run biome/tsc, which the setup flow (#4) installs as project
# devDependencies (node_modules/.bin). Prepend the repo's own node_modules/.bin
# so the gate resolves project-local tools without requiring a global install or
# a manual PATH edit. A missing dir is a harmless no-op entry.
if [ -d "$REPO_ROOT/node_modules/.bin" ]; then
    export PATH="$REPO_ROOT/node_modules/.bin:$PATH"
fi

tool_map_lookup() {
    # usage: tool_map_lookup <stack> <stage>
    # Fixed-string match (no regex surprises) then keep everything after the
    # "<stack>:<stage>:" prefix.
    grep -F "${1}:${2}:" "$TOOL_MAP" | head -n 1 | cut -d: -f3-
}

run_stage() {
    local stack="$1" stage="$2" command="$3"
    local output rc
    output="$(cd "$REPO_ROOT" && eval "$command" 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'GATE FAILED at %s:%s:\n' "$stack" "$stage" >&2
        printf '%s\n' "$output" | sed 's/^/  /' >&2
        exit 2
    fi
}

# Full step chain per stack: lint -> format -> typecheck, stacks in the order
# given. QUALITY_GATE_STACK is a comma-separated stack list ("python,ts"); a
# single stack is just a list of one. Fail-fast on the first failing stage
# across all stacks; feedback distinguishes <stack>:<stage> so a monorepo's
# python and TS ends are both covered. Every invocation restarts from lint
# (gate loop) — no state is carried over.
IFS=',' read -ra STACK_LIST <<< "$STACKS"
for stack in "${STACK_LIST[@]}"; do
    stack="${stack//[[:space:]]/}"
    # Tolerate stray separators ("python,,ts", "python,ts,"): an empty entry
    # must not fall through to a tool-map substring match under an empty label.
    [ -z "$stack" ] && continue
    for stage in lint format typecheck; do
        cmd="$(tool_map_lookup "$stack" "$stage")"
        if [ -z "$cmd" ]; then
            printf 'GATE FAILED at %s:%s: no tool map entry for stack %s\n' "$stack" "$stage" "$stack" >&2
            exit 2
        fi
        run_stage "$stack" "$stage" "$cmd"
    done
done

exit 0
