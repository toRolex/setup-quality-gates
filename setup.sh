#!/usr/bin/env bash
#
# setup.sh — install a commit-time quality gate into a target project.
#
# Part of the setup-quality-gates skill (issue #4). The skill is this repo; the
# flow is: confirm the stack (user declaration, never auto-detected, validated
# against the tool map) -> install the tools the map declares for that stack ->
# write .claude/settings.json + copy .claude/hooks/gate.sh, gate_parse.py &
# tool_map.sh -> self-verify the written gate once.
#
# setup.sh holds no stack knowledge of its own: tool_map.sh is the stack
# registry (which stacks exist, their stage commands, their install specs).
# What lives here is environment knowledge: the install strategies ("uv",
# "node") and how to detect a node package manager.
#
# Usage:
#   setup.sh [--target <dir>] [--stack <stacks>] [--no-install] [--no-self-verify]
#
# Options:
#   --target <dir>     Target project directory (default: current directory).
#   --stack <stacks>   Confirmed stack(s): comma-separated stack names as
#                      declared in tool_map.sh (e.g. "python", "python,ts").
#                      If omitted, setup prompts interactively. The user's
#                      declaration is authoritative — setup never probes the
#                      filesystem to guess the stack.
#   --no-install       Detect missing tools but do not install them.
#   --no-self-verify   Do not run the post-write gate self-check.
#   -h, --help         Show this help.
#
# Exit codes:
#   0  setup completed (config written; a dirty/missing-tool gate is reported,
#      not silenced — it surfaces in the self-verify).
#   2  setup could not complete (bad args, missing skill files, unwritable
#      target, invalid/aborted stack, settings.json write failure).

set -u

usage() {
    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
}

# known_stacks -> newline-separated stack names declared in the tool map.
# The map is the only place stack names live.
known_stacks() {
    grep -v '^#' "$SRC_TOOL_MAP" | grep -v '^[[:space:]]*$' | cut -d: -f1 | sort -u
}

# known_stacks_display -> comma-separated stack names for prompts/errors.
known_stacks_display() {
    known_stacks | paste -sd, - | tr -d ' '
}

# normalize_stacks <raw> -> comma-separated canonical list on stdout.
# Empty entries from stray separators ("python,,ts", "python,ts,") are skipped.
# Returns 0 if every token is a stack declared in the tool map, 1 otherwise.
normalize_stacks() {
    local raw="$1" out="" tok
    local valid
    valid="$(known_stacks)"
    local IFS=','
    for tok in $raw; do
        [ -z "$tok" ] && continue
        tok="${tok//[[:space:]]/}"
        if printf '%s\n' "$valid" | grep -qx "$tok"; then
            if [ -z "$out" ]; then
                out="$tok"
            else
                out="$out,$tok"
            fi
        else
            return 1
        fi
    done
    [ -n "$out" ] || return 1
    echo "$out"
}

# resolve_stacks <raw> -> sets STACKS, or exits 2 after the interactive prompt
# gives up (3 attempts) / the --stack value is invalid.
resolve_stacks() {
    local raw="$1"
    local valid_display
    valid_display="$(known_stacks_display)"
    if [ -n "$raw" ]; then
        if ! STACKS="$(normalize_stacks "$raw")"; then
            echo "setup: invalid --stack '$raw' — expected a comma-separated subset of: $valid_display." >&2
            exit 2
        fi
        return 0
    fi
    STACKS=""
    local attempt line
    for attempt in 1 2 3; do
        printf 'Target project tech stack? (comma-separated subset of: %s — e.g. the first two): ' "$valid_display" >&2
        read -r line || true
        if STACKS="$(normalize_stacks "$line")"; then
            return 0
        fi
        echo "setup: invalid stack '$line' — expected a comma-separated subset of: $valid_display." >&2
        STACKS=""
    done
    echo "setup: no valid stack confirmed; aborting." >&2
    exit 2
}

# ensure_tool <tool> <pm> [pm args...]
# Install <tool> with <pm> when it is missing. Never fails the setup: a tool
# that can't be installed surfaces explicitly (WARNING + GATE FAILED at the
# gate/self-verify) instead of failing silently.
ensure_tool() {
    local tool="$1"; shift
    local pm="$1"; shift
    # TS/JS tools (biome/tsc) land in the project's node_modules/.bin, which is
    # NOT on this script's PATH — gate.sh prepends it at gate time. Accept it as
    # present so a re-run doesn't reinstall, and don't warn after a successful
    # npm/pnpm install just because the bin dir isn't on PATH here.
    local local_bin="$TARGET/node_modules/.bin/$tool"
    if command -v "$tool" >/dev/null 2>&1 || [ -x "$local_bin" ]; then
        echo "  ok: $tool is available"
        return 0
    fi
    if ! command -v "$pm" >/dev/null 2>&1; then
        echo "  WARNING: cannot install $tool — '$pm' not found on PATH." >&2
        return 0
    fi
    echo "  installing $tool: $pm $*"
    (cd "$TARGET" && "$pm" "$@") || {
        local rc=$?
        echo "  WARNING: '$pm $*' failed (exit $rc); $tool may still be missing." >&2
        return 0
    }
    if command -v "$tool" >/dev/null 2>&1 || [ -x "$local_bin" ]; then
        echo "  ok: $tool is now available"
    else
        echo "  WARNING: $tool installed but not found on PATH or in node_modules/.bin — check the package manager's bin dir." >&2
    fi
    return 0
}

pick_node_pm() {
    if command -v pnpm >/dev/null 2>&1; then
        echo "pnpm"
    elif command -v npm >/dev/null 2>&1; then
        echo "npm"
    fi
}

# install_tools <stack> — read the stack's install rows from the tool map and
# dispatch each to its strategy. setup.sh knows strategies (environment
# knowledge), never stacks: adding a stack that reuses an existing strategy is
# a tool_map.sh-only change.
install_tools() {
    local stack="$1"
    local row tool strategy package
    grep -F "${stack}:install:" "$SRC_TOOL_MAP" | while IFS= read -r row; do
        tool="$(printf '%s' "$row" | cut -d: -f3)"
        strategy="$(printf '%s' "$row" | cut -d: -f4)"
        package="$(printf '%s' "$row" | cut -d: -f5-)"
        case "$strategy" in
            uv)
                ensure_tool "$tool" uv tool install "$package"
                ;;
            node)
                local pm add_cmd
                pm="$(pick_node_pm)"
                if [ -z "$pm" ]; then
                    echo "  WARNING: cannot install $tool — neither pnpm nor npm on PATH." >&2
                    continue
                fi
                # pnpm installs devDeps with `add`; npm has no `add` subcommand,
                # it uses `install`. The command verb must match the pm.
                add_cmd="add"
                [ "$pm" = "npm" ] && add_cmd="install"
                ensure_tool "$tool" "$pm" "$add_cmd" -D "$package"
                ;;
            *)
                echo "  WARNING: unknown install strategy '$strategy' in tool map row '$row'." >&2
                ;;
        esac
    done
}

# write_settings <target> <stack> — generate/merge .claude/settings.json.
# The PreToolUse hook entry points at .claude/hooks/gate.sh. QUALITY_GATE_STACK
# is passed via the settings top-level "env" block: command hooks have no "env"
# field in the Claude Code schema, so the documented settings-level env is the
# mechanism (applies to the session and its hook subprocesses). An existing
# settings.json is merged, not clobbered; the gate entry is replaced on re-run.
write_settings() {
    local target="$1" stack="$2"
    local script
    script=$(cat <<'PY'
import json
import os
import sys

target, stack = sys.argv[1], sys.argv[2]
settings_path = os.path.join(target, ".claude", "settings.json")

if os.path.exists(settings_path):
    with open(settings_path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            print(f"setup: existing {settings_path} is not valid JSON ({exc}); refusing to overwrite. Fix or remove it, then re-run.", file=sys.stderr)
            sys.exit(2)
    if not isinstance(data, dict):
        print(f"setup: existing {settings_path} is not a JSON object; refusing to overwrite.", file=sys.stderr)
        sys.exit(2)
else:
    data = {}

data.setdefault("env", {})["QUALITY_GATE_STACK"] = stack

gate_entry = {
    "matcher": "Bash",
    "hooks": [
        {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/gate.sh",
            "args": [],
            "timeout": 120,
        }
    ],
}

pretool = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
if not isinstance(pretool, list):
    print(f"setup: existing {settings_path} has hooks.PreToolUse that is not a list; refusing to overwrite.", file=sys.stderr)
    sys.exit(2)

replaced = False
kept = []
for entry in pretool:
    is_gate = (
        isinstance(entry, dict)
        and entry.get("matcher") == "Bash"
        and isinstance(entry.get("hooks"), list)
        and any(
            isinstance(h, dict)
            and isinstance(h.get("command"), str)
            and h["command"].endswith("/.claude/hooks/gate.sh")
            for h in entry["hooks"]
        )
    )
    if is_gate:
        if not replaced:
            kept.append(gate_entry)
            replaced = True
        # duplicate gate entries from a previous run are dropped
    else:
        kept.append(entry)
if not replaced:
    kept.append(gate_entry)
data["hooks"]["PreToolUse"] = kept

with open(settings_path, "w", encoding="utf-8") as f:
    # Tab-indent on purpose: the TS/JS gate's biome format stage (a tool-map
    # command) uses tabs by default and would flag a space-indented
    # settings.json on the very first commit. Writing tabs keeps
    # setup's own artifact clean under the gate it installs.
    json.dump(data, f, indent="\t")
    f.write("\n")
PY
)
    if ! python3 -c "$script" "$target" "$stack"; then
        return 1
    fi
    echo "  wrote $target/.claude/settings.json"
    return 0
}

# self_verify <target> <stacks> — simulate a PreToolUse commit event against the
# just-written gate so wiring problems surface before the first real commit.
self_verify() {
    local target="$1" stacks="$2"
    echo ""
    echo "== Self-verify =="
    local abs_target payload out rc
    abs_target="$(cd "$target" && pwd)"
    # Build the PreToolUse payload with python3's json serializer instead of
    # hand-escaping: a target path that contains quotes, backslashes or other
    # JSON specials (common on macOS/Windows) must still yield valid JSON.
    # python3 is already a hard dependency of the setup flow; the payload
    # schema (hook_event_name, tool_name, tool_input.command, cwd) is unchanged.
    payload="$(python3 - "$abs_target" <<'PY'
import json
import sys

command = 'git commit -m "self-verify"'
payload = {
    "hook_event_name": "PreToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": command},
    "cwd": sys.argv[1],
}
print(json.dumps(payload))
PY
)"
    out="$(printf '%s' "$payload" | QUALITY_GATE_STACK="$stacks" bash "$target/.claude/hooks/gate.sh" 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        echo "self-verify: OK — the quality gate chain is green for stack(s) [$stacks]."
        return 0
    fi
    if [ "$rc" -eq 2 ]; then
        echo "self-verify: the quality gate chain is LIVE and currently BLOCKS commits for stack(s) [$stacks]."
        printf '%s\n' "$out" | sed 's/^/    /'
        echo "  Expected if the project code isn't clean yet or a tool is missing. Fix the reported stages before your first commit — this is the gate working as intended."
        return 0
    fi
    echo "self-verify: ERROR — the written gate did not run correctly (exit $rc)." >&2
    printf '%s\n' "$out" | sed 's/^/    /' >&2
    return 1
}

main() {
    TARGET="."
    STACK_RAW=""
    DO_INSTALL=1
    DO_SELF_VERIFY=1

    while [ "$#" -gt 0 ]; do
        case "$1" in
            --target)
                [ "$#" -ge 2 ] || { echo "setup: --target needs a directory" >&2; exit 2; }
                TARGET="$2"
                shift 2
                ;;
            --stack)
                [ "$#" -ge 2 ] || { echo "setup: --stack needs a value" >&2; exit 2; }
                STACK_RAW="$2"
                shift 2
                ;;
            --no-install)
                DO_INSTALL=0
                shift
                ;;
            --no-self-verify)
                DO_SELF_VERIFY=0
                shift
                ;;
            -h | --help)
                usage
                exit 0
                ;;
            *)
                echo "setup: unknown option '$1' (see --help)" >&2
                exit 2
                ;;
        esac
    done

    if [ ! -d "$TARGET" ]; then
        echo "setup: target directory '$TARGET' does not exist" >&2
        exit 2
    fi
    TARGET="$(cd "$TARGET" && pwd)" || {
        echo "setup: cannot resolve target '$TARGET'" >&2
        exit 2
    }

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SRC_GATE="$SCRIPT_DIR/.claude/hooks/gate.sh"
    SRC_GATE_PARSE="$SCRIPT_DIR/.claude/hooks/gate_parse.py"
    SRC_TOOL_MAP="$SCRIPT_DIR/.claude/hooks/tool_map.sh"
    if [ ! -f "$SRC_GATE" ] || [ ! -f "$SRC_GATE_PARSE" ] || [ ! -f "$SRC_TOOL_MAP" ]; then
        echo "setup: cannot find gate.sh/gate_parse.py/tool_map.sh next to setup.sh ($SCRIPT_DIR/.claude/hooks/)" >&2
        exit 2
    fi

    resolve_stacks "$STACK_RAW"

    echo "setup: installing quality gate into $TARGET"
    echo "  confirmed stack(s): $STACKS"

    echo ""
    echo "== Tool check / install =="
    if [ "$DO_INSTALL" -eq 1 ]; then
        local stack
        # STACKS is already normalized (comma-separated, no spaces); turn commas
        # into spaces and word-split on the default IFS. Deliberately not setting
        # IFS here: a ','-scoped IFS would leak into ensure_tool and corrupt the
        # "$*" text in its install messages.
        for stack in ${STACKS//,/ }; do
            install_tools "$stack"
        done
    else
        echo "  (--no-install: detection only, skipping installation)"
    fi

    echo ""
    echo "== Writing gate config =="
    if ! mkdir -p "$TARGET/.claude/hooks"; then
        echo "setup: cannot create $TARGET/.claude/hooks" >&2
        exit 2
    fi
    cp "$SRC_GATE" "$TARGET/.claude/hooks/gate.sh" || {
        echo "setup: failed to copy gate.sh" >&2
        exit 2
    }
    cp "$SRC_GATE_PARSE" "$TARGET/.claude/hooks/gate_parse.py" || {
        echo "setup: failed to copy gate_parse.py" >&2
        exit 2
    }
    cp "$SRC_TOOL_MAP" "$TARGET/.claude/hooks/tool_map.sh" || {
        echo "setup: failed to copy tool_map.sh" >&2
        exit 2
    }
    chmod +x "$TARGET/.claude/hooks/gate.sh"
    echo "  copied .claude/hooks/gate.sh + .claude/hooks/gate_parse.py + .claude/hooks/tool_map.sh"

    if ! write_settings "$TARGET" "$STACKS"; then
        echo "setup: aborting." >&2
        exit 2
    fi

    if ! git -C "$TARGET" rev-parse --show-toplevel >/dev/null 2>&1; then
        echo "  note: '$TARGET' is not inside a git repo — the gate applies to Claude Code sessions here but is not versioned until the project is committed."
    fi

    echo ""
    if [ "$DO_SELF_VERIFY" -eq 1 ]; then
        if ! self_verify "$TARGET" "$STACKS"; then
            echo "setup: self-verify failed; the written gate did not run." >&2
            exit 2
        fi
    else
        echo "== Self-verify =="
        echo "  (--no-self-verify: skipped)"
    fi

    echo ""
    echo "setup: done. Quality gate active for stack(s) [$STACKS]."
    echo "  hook:   $TARGET/.claude/settings.json (PreToolUse -> .claude/hooks/gate.sh)"
    echo "  commit  the .claude/ directory with your repo so the gate is versioned."
}

main "$@"
