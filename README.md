# knowledge-graph-code

Proof of concept: make several local repositories queryable from a Claude Code
session through one merged code graph — graphify's stdio MCP server for cheap,
token-budgeted retrieval, plus a read-only Neo4j MCP server for precise Cypher —
and measure whether that actually saves tokens over plain file reading.
Design: `docs/specs/2026-10-03-multi-repo-code-graph-design.md`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python >= 3.11
- A Neo4j instance (AuraDB Free was used; it pauses after 3 idle days)
- The five repos in `repos.yaml` checked out under `~/Projects`

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env            # then fill in the four NEO4J_* values; .env is gitignored, mode 0600
.venv/bin/kgc doctor            # connection check; prints no secrets
.venv/bin/kgc init              # writes .mcp.json (no secrets inside) and prints the CLAUDE.md block
```

`kgc init --claude-md CLAUDE.md` appends the block instead of printing it. This
project's own `CLAUDE.md` is exactly that block, so a fresh checkout needs only
the `.mcp.json`.

## Run order

```bash
.venv/bin/kgc build             # extract every repo in repos.yaml (zero tokens), ~40 s for five repos
.venv/bin/kgc push              # reap + upsert each repo into Neo4j; ensures indexes; writes a Repo node last
.venv/bin/kgc serve             # the MCP server Claude Code spawns via .mcp.json (stdio)
```

Run from the project root: `.env` and `repos.yaml` are resolved relative to the
current directory. `kgc serve --http PORT` serves over HTTP on localhost with no
authentication, for experiments only.

The nine tools: `search`, `describe`, `callers`, `callees`, `impact`,
`externals`, `cross_repo`, `stats`, `cypher` (read-only). `stats` names the
file extensions the extractor did not parse — the graph's blind spots.

## Verification

The harness has three arms: control (no MCP), treatment (graphify +
mcp-neo4j-cypher) and kgc (this server).

```bash
.venv/bin/kgc push                                       # once after Plan 1 lands: creates kgc_text + Repo nodes
.venv/bin/python setup_mcp.py --out verify/treatment-mcp.json
.venv/bin/kgc init --out verify/kgc-mcp.json
.venv/bin/python verify/run_arm.py --questions 1 --runs 1 --arm kgc --allowed-tools "mcp__kgc Read Grep Glob"
```

Headless runs cannot approve tool calls, so without `--allowed-tools` every
MCP call is a permission denial and the run is invalid.

1. Earlier builds wrote `<repo>/graphify-out/` into the target repos; delete any
   that remain (they are untracked). The user's global graphify skill treats a
   question as a graphify query whenever that directory exists, which would
   contaminate the control arm.
2. `.venv/bin/python verify/run_arm.py --dry-run` — prints every `claude -p` command; check cwd and flags.
3. Pre-flight: `.venv/bin/python verify/run_arm.py --questions 1 --runs 1 --arm treatment`, then confirm
   in `verify/runs/treatment-q01-r1.json` that `graphify_calls > 0`, `permission_denials == 0`,
   and `init.mcp_servers` names exactly `graphify` and `neo4j`.
4. `.venv/bin/python verify/run_arm.py --runs 2` (3 recommended) — spends real tokens.
5. Judge every answer against the code and fill in `verify/results.md`.

## Known blind spots

An import/call graph has edges only where the extractor can name a concrete
target at parse time. It is blind to dependency injection, registries and
config-driven wiring (the coupling style of most enterprise frameworks), to
languages graphify has no extractor for (kdb+/q, SAS), and to coupling that
crosses a language boundary at runtime (TypeScript MCP client to Python MCP
server). Zero matching nodes proves the extractor did not look, not that the
code is absent; `verify/questions.md` Q9–Q12 check the graph says so rather than guessing.
