# tool_map.sh — single source of truth for the quality-gate tool map.
#
# This is the ONE place the stack -> lint/format/typecheck commands live.
# gate.sh reads it at every gate invocation; the setup flow (#4) reuses it to
# install/verify the tools for a project's confirmed stack. Do not duplicate
# these commands anywhere else.
#
# Format (one stage per line):
#   <stack>:<stage>:<command>
#
# A stage command runs from the repo root. gate.sh reads a line with a
# fixed-string match on "<stack>:<stage>:" and keeps the remainder.
#
# Python (managed with uv in the target project). Any tool missing from the
# environment makes its stage fail and the gate reports that stage.

python:lint:ruff check .
python:format:ruff format --check .
python:typecheck:ty check .

# TypeScript (issue #3). biome lint / biome format run from the repo root; the
# fixture/project is expected to carry its own biome defaults or config and a
# tsconfig.json for tsc. Any tool missing from the environment makes its stage
# fail and the gate reports that stage.

ts:lint:biome lint .
ts:format:biome format .
ts:typecheck:tsc --noEmit

# JavaScript (issue #4). A JS stack is linted/formatted by biome exactly like
# TS, but it has no typechecker: the typecheck stage is a documented no-op
# (`true`) so gate.sh's uniform lint -> format -> typecheck chain stays intact
# for every stack. Setup maps a user-declared "js" stack to these entries.

js:lint:biome lint .
js:format:biome format .
js:typecheck:true
