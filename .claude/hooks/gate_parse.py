"""gate_parse.py — the commit detector for gate.sh (issue #7, T5).

Extracted from gate.sh's inline Python heredoc so the most complex logic in the
repo (shlex tokenizing, git option values, sudo/env prefixes, quoted-`&&`
mis-split protection) lives in a real module covered by ruff/ty, with syntax
highlighting and type checking, instead of a bash string.

Contract (unchanged from the heredoc — gate.sh consumes it line by line):
  * stdin:  the PreToolUse event JSON (tool_name="Bash").
  * stdout: two lines —
      line 1: "1" if the command invokes `git commit`, else "0"
      line 2: the cwd from the event

Standard library only (python3 is the gate's sole dependency class).
"""

from __future__ import annotations

import json
import shlex
import sys

SEPARATORS = {"&&", "||", ";", "|"}
GIT_OPTS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--config-env",
}
GIT_PREFIXES = {"sudo", "env", "command", "nohup", "time", "builtin"}


def command_contains_commit(command: str) -> bool:
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


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (ValueError, OSError):
        # Malformed/unreadable payload -> fail safe to "not a commit".
        # (JSONDecodeError and UnicodeDecodeError are ValueError subclasses.)
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


if __name__ == "__main__":
    main()
