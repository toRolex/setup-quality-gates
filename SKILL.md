---
name: setup-quality-gates
description: >-
  Install a Claude Code commit-time quality gate (lint -> format -> typecheck)
  into a target project. Interactively confirms the tech stack (user-declared,
  never auto-detected), installs missing tools (uv / pnpm / npm), writes
  .claude/settings.json (PreToolUse hook) + .claude/hooks/gate.sh, and
  self-verifies the gate. Use when a maintainer wants to add commit gates to a
  project ("setup quality gate", "加质量门禁", "install the gate").
---

# setup-quality-gates

Install a project-level commit quality gate: a Claude Code `PreToolUse` hook
that blocks `git commit` unless lint -> format -> typecheck pass for the
confirmed tech stack.

The skill is this repository. `setup.sh` is the installer; `.claude/hooks/`
holds the gate (`gate.sh`) and the single source of the stack->command map
(`tool_map.sh`).

## When to use

- A maintainer wants to enforce a commit-time lint/format/typecheck gate on a
  target project (any stack: Python, TypeScript, JavaScript, or a mix).
- The gate must apply to every agent working in the repo and be versioned with
  it (committed `.claude/`), not just a local convention.

## How to run

In the target project directory:

```bash
bash ~/.claude/skills/setup-quality-gates/setup.sh
```

`setup.sh` prompts for the tech stack (user declaration is authoritative — it
never probes the filesystem). Or drive it non-interactively:

```bash
bash ~/.claude/skills/setup-quality-gates/setup.sh \
  --target /path/to/project \
  --stack python,ts
```

Stacks: `python`, `ts`, `js`, or a comma-separated mix (e.g. `python,ts`).
`js` is linted/formatted by biome; it has no typechecker, so that stage is a
no-op.

## What the flow does

1. Confirm the stack (prompt or `--stack`).
2. Select the lint/format/typecheck commands from `tool_map.sh` (single source).
3. Detect missing tools and install them with the matching package manager
   (`uv` for python; `pnpm`/`npm` for ts/js). Uninstallable tools produce an
   explicit warning — never a silent failure.
4. Write the gate config: `.claude/settings.json` (PreToolUse hook +
   `QUALITY_GATE_STACK`) and copy `gate.sh` + `tool_map.sh` into
   `.claude/hooks/`, executable.
5. Self-verify: simulate a PreToolUse commit event against the written gate so
   wiring problems surface before the first real commit.

## Key files

- `setup.sh` — the installer (this skill's entry point).
- `.claude/hooks/gate.sh` — the PreToolUse hook (ADR-0001).
- `.claude/hooks/tool_map.sh` — the one place stack -> command mapping lives.
- `tests/test_gate.py`, `tests/test_setup.py` — behavior tests.

## Out of scope

- GitHub Actions / CI gating (another skill's job).
- Auto-detecting the tech stack — the maintainer declares it.
