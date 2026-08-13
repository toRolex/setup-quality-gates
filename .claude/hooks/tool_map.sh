# tool_map.sh — single source of truth for the quality-gate tool map.
#
# This is the ONE place stack knowledge lives: which stacks exist, their
# lint/format/typecheck stage commands, and how to install their tools.
# gate.sh reads the stage rows at every gate invocation; the setup flow (#4)
# reads the same file to validate declared stacks and install missing tools.
# Do not duplicate these facts anywhere else.
#
# Format (one record per line):
#   <stack>:<stage>:<command>                    stage row
#   <stack>:install:<tool>:<strategy>:<package>  install row
#
# A stage command runs from the repo root. gate.sh reads a line with a
# fixed-string match on "<stack>:<stage>:" and keeps the remainder; it only
# ever looks up lint/format/typecheck, so install rows are invisible to it.
#
# An install row tells setup.sh how to obtain <tool>: <strategy> is the
# package-manager family ("uv" or "node", dispatched in setup.sh) and
# <package> is what that family installs (may differ from <tool>, e.g.
# biome -> @biomejs/biome).
#
# Python (managed with uv in the target project). Any tool missing from the
# environment makes its stage fail and the gate reports that stage.

python:lint:ruff check .
python:format:ruff format --check .
python:typecheck:ty check .
python:install:ruff:uv:ruff
python:install:ty:uv:ty

# TypeScript (issue #3). biome lint / biome format run from the repo root; the
# fixture/project is expected to carry its own biome defaults or config and a
# tsconfig.json for tsc. Any tool missing from the environment makes its stage
# fail and the gate reports that stage.

ts:lint:biome lint .
ts:format:biome format .
ts:typecheck:tsc --noEmit
ts:install:biome:node:@biomejs/biome
ts:install:tsc:node:typescript

# JavaScript (issue #4). A JS stack is linted/formatted by biome exactly like
# TS, but it has no typechecker: the typecheck stage is a documented no-op
# (`true`) so gate.sh's uniform lint -> format -> typecheck chain stays intact
# for every stack. Setup maps a user-declared "js" stack to these entries.

js:lint:biome lint .
js:format:biome format .
js:typecheck:true
js:install:biome:node:@biomejs/biome
