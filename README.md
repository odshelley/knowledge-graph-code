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

    uv venv && uv pip install -e ".[dev]"
    cp .env.example .env          # fill in NEO4J_URI / USERNAME / PASSWORD / DATABASE
    .venv/bin/python -m pytest    # all green

`.env` and the generated `.mcp.json` hold the Neo4j password; both are gitignored
and written mode 0600. Rotate the Aura password when the POC ends — it was pasted
into a Claude Code session once.

## Run order

Run every `kgc` command **from this directory**: `.env` and `repos.yaml` are
resolved relative to the current working directory.

1. `.venv/bin/kgc doctor` — proves the Neo4j connection and resolves the database name.
2. `.venv/bin/kgc build` — zero-token AST extraction of each repo into
   `~/.graphify/repos/<tag>/graphify-out/graph.json`, merged into
   `~/.graphify/global-graph.json`, then direction normalisation and shared-stub
   re-tagging. Nothing is written into the target repos.
3. `.venv/bin/kgc push` — reap each repo's slice from Neo4j and re-push it.
4. `.venv/bin/python setup_mcp.py` — writes `.mcp.json` (graphify stdio and
   `mcp-neo4j-cypher@0.6.0`, read-only).
5. Restart Claude Code in this directory and run `/mcp` to confirm both servers connected.

## Verification

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
