# Quality Gates

Context for the `setup-quality-gates` skill: a reusable skill that installs a commit-time lint → format → typecheck quality gate into a target project, configured to the project's confirmed tech stack.

## Language

**Quality gate**:
A commit-time check chain — lint → format → typecheck — that must all pass before a commit is allowed.
_Avoid_: static check, CI check, gate check

**Gate loop**:
The failure-handling cycle: when any stage fails, the failure is returned to the agent to fix, and the whole chain restarts from lint.
_Avoid_: retry loop, re-run

**Step chain**:
The ordered pre-commit sequence of stages a quality gate runs (lint → format → typecheck).
_Avoid_: checklist, workflow

**Red line**:
Hard rules in the AFK issue loop (`afk-issue-loop`) that agents may not violate; a violation counts as a process violation.
_Avoid_: hard rule (too generic)

**Tool map**:
The stack registry: the single place that declares which stacks exist, each stack's lint/format/typecheck step-chain commands, and each tool's install spec (strategy + package). The gate reads the stage commands; the setup flow reads the same map to validate declared stacks and install missing tools.
_Avoid_: config table, linter setup

**Install strategy**:
The package-manager family a tool is installed with — `uv` or `node` (probed pnpm/npm). Environment knowledge that lives in the setup flow, not in the tool map: the map names the strategy, the setup flow implements it.
_Avoid_: installer, package manager config
