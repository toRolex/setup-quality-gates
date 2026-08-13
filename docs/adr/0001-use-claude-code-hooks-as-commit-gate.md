# Use a Claude Code PreToolUse hook as the commit gate

We enforce the quality gate with a project-level Claude Code hook — a `PreToolUse` hook intercepting `git commit` that runs the full-repo lint → format → typecheck chain and blocks with exit 2 on any failure — instead of a git hook (Lefthook), a `Stop` hook, or relying on the agent's instructions alone. The requirement is an agent loop: a failing stage must be returned to the agent with "restart from lint" instructions. The `PreToolUse` hook is the only mechanism whose block reason lands directly in the agent's context as actionable feedback, and it runs before permission-mode checks, so even `bypassPermissions` cannot skip it.

## Considered Options

- **Git hook (Lefthook)**: also blocks human/IDE commits, but a failed commit only returns stderr; the agent must parse it and does not get the "restart from lint" discipline. Adds a binary dependency.
- **Stop hook**: fires after the turn ends — after the commit has already happened — so it cannot block a bad commit. Runs every turn and has an 8-consecutive-block override cap.
- **No hook (agent step chain only)**: relies on the agent following the red line; not deterministic.

## Consequences

- The gate only fires inside Claude Code; commits made by a human in an IDE bypass it. A CI gate (GitHub Actions) would cover that, but is explicitly out of scope — that is another skill's responsibility.
- The gate is project-level (`.claude/settings.json` + `.claude/hooks/gate.sh`), committed to the target repo, so it applies to every agent working in that repo and is versioned with the code.
