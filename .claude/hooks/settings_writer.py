"""settings_writer.py — the settings.json merge logic for setup.sh (issue #10, T8).

Extracted from setup.sh's write_settings inline Python heredoc so the merge
logic (env + PreToolUse gate-entry replace/dedupe) lives in a real module
covered by ruff/ty, with syntax highlighting and type checking, instead of a
bash string that needs double escaping.

Contract (unchanged from the heredoc — setup.sh drives it the same way):
  * argv: <settings_path> <stack>
  * reads <settings_path>; a missing file is treated as an empty object.
  * fail-closed, exit 2 + "setup:"-prefixed stderr (never overwrites):
      - the file exists but is not valid JSON
      - the file is a JSON value that is not an object
      - the file's hooks.PreToolUse is not a list
  * otherwise merges QUALITY_GATE_STACK into env and replaces/dedupes the gate
    entry in hooks.PreToolUse, then writes the result back with tab
    indentation and a trailing newline.

Standard library only (python3 is setup's sole dependency class).
"""

from __future__ import annotations

import json
import os
import sys

GATE_ENTRY = {
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


def _is_gate_entry(entry: object) -> bool:
    return (
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


def merge_settings(existing: dict, stack: str) -> dict:
    """Merge QUALITY_GATE_STACK and the gate entry into ``existing``.

    Non-gate PreToolUse entries and unrelated top-level keys survive; the
    existing gate entry (if any) is replaced in place, duplicates from a
    previous run are dropped, and a missing gate entry is appended. Callers
    validate ``existing`` (JSONDecodeError / non-dict / PreToolUse non-list)
    before calling; merge_settings is I/O-free.
    """
    data = dict(existing)
    data.setdefault("env", {})["QUALITY_GATE_STACK"] = stack

    pretool = data.setdefault("hooks", {}).setdefault("PreToolUse", [])

    replaced = False
    kept = []
    for entry in pretool:
        if _is_gate_entry(entry):
            if not replaced:
                kept.append(GATE_ENTRY)
                replaced = True
            # duplicate gate entries from a previous run are dropped
        else:
            kept.append(entry)
    if not replaced:
        kept.append(GATE_ENTRY)
    data["hooks"]["PreToolUse"] = kept
    return data


def main() -> None:
    settings_path, stack = sys.argv[1], sys.argv[2]

    if os.path.exists(settings_path):
        with open(settings_path, encoding="utf-8") as f:
            try:
                existing = json.load(f)
            except json.JSONDecodeError as exc:
                print(
                    f"setup: existing {settings_path} is not valid JSON ({exc}); refusing to overwrite. Fix or remove it, then re-run.",
                    file=sys.stderr,
                )
                sys.exit(2)
        if not isinstance(existing, dict):
            print(
                f"setup: existing {settings_path} is not a JSON object; refusing to overwrite.",
                file=sys.stderr,
            )
            sys.exit(2)
        pretool = existing.get("hooks", {}).get("PreToolUse", [])
        if not isinstance(pretool, list):
            print(
                f"setup: existing {settings_path} has hooks.PreToolUse that is not a list; refusing to overwrite.",
                file=sys.stderr,
            )
            sys.exit(2)
    else:
        existing = {}

    data = merge_settings(existing, stack)

    with open(settings_path, "w", encoding="utf-8") as f:
        # Tab-indent on purpose: the TS/JS gate's biome format stage (a
        # tool-map command) uses tabs by default and would flag a space-indented
        # settings.json on the very first commit. Writing tabs keeps setup's own
        # artifact clean under the gate it installs.
        json.dump(data, f, indent="\t")
        f.write("\n")


if __name__ == "__main__":
    main()
