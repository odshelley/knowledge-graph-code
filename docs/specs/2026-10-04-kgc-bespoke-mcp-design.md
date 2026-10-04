# kgc — Bespoke Code-Graph MCP: Design

**Date:** 2026-10-04
**Status:** Approved design. Two implementation plans follow: Plan 1 (MCP
server) then Plan 2 (vendored, stripped build side).
**Author:** Osian Shelley (with Claude)
**Supersedes:** the serve/query half of
`docs/specs/2026-10-03-multi-repo-code-graph-design.md`. The extraction and
push halves of that spec remain the baseline this one replaces.

## Goal

Replace the two off-the-shelf MCP servers wired together in the first POC
(graphify's in-memory `serve.py` and `mcp-neo4j-cypher`) with one server we own,
backed only by Neo4j, and replace the `graphifyy` pip dependency with a vendored,
stripped copy of its extraction core. The result is a single package, `kgc`,
with three commands (`build`, `push`, `serve`) and nothing on the box that the
bank's security review would need to read past.

Target environment is unchanged: a large investment bank, 50–200 repositories,
Neo4j Community on AWS, no code egress, CI runs the build.

## Decisions taken in brainstorming

| Decision | Choice | Why |
|---|---|---|
| Fork depth | **Hard fork.** Vendor the extraction core, delete the rest, drop the pip dependency. | What is *installed* matters at a bank as much as what is *called*. graphify ships an LLM module with network paths, an installer that writes into `~/.claude`, and a file watcher. A pruned tree is auditable. The q/SAS extractor gap means we will be inside this code anyway. |
| Name | `kgc` | Already the project and CLI name. Says nothing about upstream. |
| Query store | **Neo4j only.** The merged global JSON graph is deleted from the design. | Its only reader was graphify's server. Every bug found in the first POC lived in the merge step. The bank's CI model is one job per repo; no job needs the whole graph in memory. |
| Strip depth | **Module level.** Delete standalone extractors needing optional extras; keep the three monolithic files whole. | Bank languages (Python, C, C++, C#, Java, Scala) are interleaved with JS/TS, Go, Rust, Ruby, Swift, PHP, Kotlin inside ~20,000 lines. Carving them apart is high-risk surgery for no security gain: grammar wheels are inert. The `claude` test repo is TypeScript. |
| Server shape | **Thin server over Neo4j.** No graph in memory; lift only two pure helpers from graphify's server. | The alternative (load the graph from Neo4j into NetworkX and reuse graphify's engine) re-introduces per-session memory load and a stale snapshot. |
| Sequencing | **Server first (Plan 1), fork second (Plan 2).** | The server works against today's database immediately and gives the harness its third arm. Nothing in it depends on the fork. A 27,000-line vendoring diff would bury a 500-line server in review. |

## Non-goals

Stated so they are not accidental:

- Notes / wiki layer. The schema must not preclude it (a future `Note` label
  linked to `CodeNode`), but nothing is built.
- HTTP authentication and multi-tenancy. The HTTP transport flag exists because
  the SDK gives it for free; securing it does not.
- Language-level stripping of the monolithic extractor files.
- CI wiring. See "CI contract" for what CI relies on; the trigger is bank-side.
- The incremental extraction cache, if it falls outside the keep closure. Full
  rebuild per repo is the CI model.
- kdb+/q and SAS extractors. Future work, in our tree.
- Any LLM-generated content anywhere in the pipeline.

## Architecture

### Package layout

```
kgc/
  extract/      vendored, stripped extraction core          (Plan 2)
                detect.py, extract.py, build.py, cluster.py, export.py,
                extractors/{engine, resolution, bash, json_config, sql, sln,
                            go, rust}.py  (+ whatever the closure query keeps)
                LICENSE, LICENSE-MIT, NOTICE, VENDORED.md
  graph/        Neo4j write side                             (today's neo4j_io.py,
                neo4j_io.py, schema.py                       + Plan 1 additions)
  server/       MCP server                                  (Plan 1)
                app.py      MCPServer instance, nine tools
                queries.py  Cypher builders, one per tool
                resolve.py  symbol resolution tiers (lifted, adapted to Cypher)
                format.py   token-budgeted text formatter (lifted)
  config.py     Repo, Settings, load_settings, load_repos   (unchanged)
  cli.py        kgc doctor | build | push | serve | init | status
```

### Data path

```
kgc build --repo TAG
  detect → extract → build_from_json(directed=True) → cluster
  → ~/.kgc/repos/<tag>/graph.json              (the only build output)

kgc push --repo TAG
  read graph.json → prefix sourced ids "tag::" → preflight capacity
  → reap tag → upsert nodes → upsert edges per type → orphan sweep
  → ensure constraints/indexes → write Repo node (last)

kgc serve
  stdio MCP server; Claude Code spawns it per session via .mcp.json
```

Deleted relative to the first POC: `~/.graphify/global-graph.json` and its
manifest, `kgc/direction.py`, `kgc/globalgraph.py` (including
`retag_shared_stubs`), `setup_mcp.py` (folded into `kgc init`), the
`mcp-neo4j-cypher` dependency, the `graphifyy` dependency.

### Credentials

`.env` (mode 0600, gitignored) is the only credential store. `build`, `push`
and `serve` read it directly via `load_settings`. `.mcp.json` carries only the
command to run and the absolute `.env` path — no username, no database name.
This is a step up from the first POC, where `.mcp.json` carried both.

### CI contract

The bank's CI job is two lines on merge to master of each repo, with the Neo4j
URI, username, password and database supplied as CI secrets in the environment:

```
kgc build --repo <tag>
kgc push  --repo <tag>
```

CI relies on four properties, which the implementation must preserve:

1. **Idempotent.** Push is reap-by-tag then upsert. Re-running after a failed
   half-push converges to the same graph.
2. **Repos are independent.** Reap touches only nodes carrying that repo's tag,
   so jobs for different repos may run concurrently. Two jobs for the *same*
   repo must not overlap (one concurrency-group line in the CI config).
3. **The artefact is a plain file.** `graph.json` can be stored as a build
   artefact for diffing or audit without the database.
4. **Installable from an internal index.** No git dependency; all dependencies
   are ordinary wheels.

## Plan 1 — the MCP server

### SDK

`mcp` 2.3.0 (already in the venv). The server class is
`mcp.server.mcpserver.MCPServer` (v2 renamed FastMCP). `MCPServer.run(transport)`
accepts `"stdio"` (default) and `"streamable-http"`. Tools are registered with
the `@server.tool()` decorator.

### Tools

Nine tools. Fewer than the thirteen across today's two servers: every tool
description is loaded into every session, so the surface is itself a token
cost. Each tool is one parameterised Cypher query in a read transaction plus a
formatter. Output is compact text, never JSON. Every tool except `stats` takes
`max_tokens: int = 1500`; `depth` parameters are capped at 4.

| Tool | Signature | Returns | Harness Qs |
|---|---|---|---|
| `search` | `(query: str, repo: str \| None = None, limit: int = 10)` | Ranked code nodes: `repo  label  source_file:Lnn  c<community>`. Full-text over labels, paths and docstrings. The entry point. | 6, 7, 8 |
| `describe` | `(symbol: str, repo: str \| None = None)` | One node: label, repo, file:line, community, docstring (via `rationale_for`), edge counts in/out by type. | 8 |
| `callers` | `(symbol: str, repo: str \| None = None, depth: int = 1)` | Incoming `calls`, `indirect_call`, `imports`, `imports_from`, `references`, `uses`, `inherits`, grouped by repo. Edge lines `A  -calls->  B  (file:Lnn)`. | 3, 9 |
| `callees` | `(symbol: str, repo: str \| None = None, depth: int = 1)` | Outgoing `calls`, `indirect_call`, `imports`, `imports_from`, `references`, `uses`. | 9, 10 |
| `impact` | `(symbols: list[str] \| None = None, files: list[str] \| None = None, depth: int = 2)` | Transitive incoming closure over the `callers` relation set, grouped by repo with counts and the top 20 nodes per repo. `files` matches `source_file` so a git diff can be fed straight in. | 3 |
| `externals` | `(repo: str \| None = None, min_repos: int = 1)` | Per repo: third-party packages imported (top-level dotted segment of the external node's label). With `min_repos ≥ 2`: packages shared by that many repos, with the repo list. | 1, 2, 4 |
| `cross_repo` | `(repo_a: str, repo_b: str \| None = None)` | Edges whose two endpoints are *sourced* nodes (`source_file <> ''`) in different repos. Returns the literal line `none` when there are none. | 5, 12 |
| `stats` | `(repo: str \| None = None)` | Per repo: node/edge counts by relation type, externals count, `pushed_at`, `built_at_commit`, skipped file extensions with counts (or `not recorded` until Plan 2), and `last push incomplete` when tagged nodes exist without a `Repo` node. | 11 |
| `cypher` | `(query: str)` | Read-only escape hatch. Description carries the schema and direction convention and says to prefer the specific tools. | anything else |

Symbol parameters accept `name` or `path/to/file.py::name`; `repo` narrows.

### Symbol resolution (`resolve.py`)

Lifted from graphify's `_find_node_tiers`, `_resolve_path_scoped_symbol` and
`find_node_ambiguity`, re-expressed as Cypher. Tiers, first non-empty wins:

1. exact `label` match (and `label + "()"` for callables);
2. path-scoped: `source_file` ends with the path part and `label` matches;
3. case-insensitive label match;
4. full-text search, top hit only if its score is ≥ 2× the second.

Zero hits → error with the three nearest search hits. More than one hit at the
winning tier → the candidate list (repo, label, file:line); never a guess.

### Search

Neo4j full-text index `kgc_text` on `CodeNode(label, source_file)`. Query
terms come from graphify's `_query_terms` tokeniser (lifted), Lucene-escaped,
joined with `OR`, the exact phrase boosted. Ranking: Lucene score × 1.0 for
sourced nodes, × 0.5 for externals. Hits on `file_type = 'rationale'` nodes
are followed through their `rationale_for` edge and reported as the code node
they describe, deduplicated. `repo` filter is a Cypher `WHERE`.

### Output budget (`format.py`)

Lifted from graphify's `_cut_lines_to_budget` and the line shapes of
`_subgraph_to_text`. Tokens are estimated at 4 characters per token. When the
budget is hit, output is truncated at a line boundary and ends with
`… truncated; narrow by repo or reduce depth`.

### Read-only enforcement

Twice over: every query runs via `session.execute_read` (Neo4j rejects writes
in a read-mode transaction server-side), and `cypher` additionally refuses
queries before sending when, after removing single- and double-quoted string
literals, they match
`\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD CSV|CALL\s*\{)\b`
(case-insensitive). Removing literals first means `WHERE n.label = 'set'` is
not refused. Escaped quotes inside literals are not handled and can cause a
false refusal; that is documented, and the agent's recourse is the specific
tools. Transaction timeout 10 s on every call. `depth` is capped at 4 on
`callers`, `callees` and `impact`.

### Runtime

- `kgc serve [--env PATH] [--http PORT]`. stdio unless `--http`.
- One driver per process, created lazily on the first tool call. A paused Aura
  instance therefore yields a clear error, not a dead server.
- At startup, best-effort (3 s timeout): read `Repo.tag` values and render them
  into the `search` tool description ("Repos indexed: knowledge-retrieval,
  graphify, …"). On failure, a generic description. This is the cheapest nudge
  against the agent never calling the tool when a user names a repo.
- `kgc init` writes `.mcp.json` (0600) with
  `{"mcpServers": {"kgc": {"command": "<venv>/bin/kgc", "args": ["serve", "--env", "<abs .env>"]}}}`
  and prints the CLAUDE.md block to paste (or appends with `--claude-md`).

### Write-side additions in Plan 1

Small, so the server has what it reads:

- `graph/schema.py`: uniqueness constraints on `CodeNode.id` and `Repo.tag`,
  range indexes on `CodeNode.repo` and `CodeNode.label`, full-text index
  `kgc_text`. Today's plain index on `id` must be dropped (found via
  `SHOW INDEXES`) before the constraint is created, because Neo4j refuses a
  constraint when an equivalent index exists.
- Push writes one `Repo` node per repo, **last**, so its presence proves the
  push completed. Fields in "Schema" below. `skipped_extensions` is written as
  the JSON string `"null"` until Plan 2 supplies it, and `stats` prints
  `not recorded` for that value.
- Push is otherwise unchanged in Plan 1: it still reads the merged global graph
  from `~/.graphify/global-graph.json`, and `build` still writes to
  `~/.graphify/repos/<tag>/graphify-out/graph.json`. The per-repo push path and
  the `~/.kgc` location arrive with Plan 2.

### Harness

`verify/run_arm.py` gains arm `kgc`: `.mcp.json` with only the `kgc` server,
same isolation as the treatment arm. Metrics unchanged (`context_tokens`,
`permission_denials`, `contamination`, tool-call counts, now keyed by `kgc`).

## Plan 2 — the build side

### Fork method

Subtractive, from the whole `graphifyy==0.9.74` wheel (upstream tag `v0.9.74`,
repository `https://github.com/Graphify-Labs/graphify`; the local clone at
`~/Projects/graphify` is one commit past it at `0b60d47e`). The keep-set is
computed, not guessed:

1. Query the graphify repo's own graph in Neo4j for the transitive
   `imports`/`imports_from`/`calls` closure from the entry points we use:
   `detect.detect`, `extract.collect_files`, `extract.extract`,
   `build.build_from_json`, `build.prefix_graph_for_global`, `cluster.cluster`,
   `export.to_json`. Modules outside the closure are deleted.
2. Within the closure, apply the module-level rule to the standalone
   extractors (the languages still inside the monolithic `extract.py`,
   `engine.py` and `resolution.py` are untouched). **Keep:** bash, json_config,
   sln, sql, go, rust, fortran, powershell — shell, config wiring, C#
   solutions and SQL are everywhere in a bank; Go and Rust at your request;
   Fortran for legacy quant libraries; PowerShell for Windows estates. `sql`'s
   grammar `tree-sitter-sql` is promoted from optional extra to core
   dependency. **Delete:** apex, blade, cobol, commonlisp, dart, dm, elixir,
   erlang, julia, markdown, objc, ocaml, pascal, pascal_forms, r, razor, robot,
   solidity, terraform, verilog, vbnet, zig. Deleting an extractor also removes
   its entry in `extractors/__init__.py`'s `LANGUAGE_EXTRACTORS` registry and
   its facade re-export in `extract.py`; files with its extensions then fall
   into `skipped_extensions`. A grammar wheel remains a dependency iff some
   kept module imports it; the closed-world test enforces this, so wheels used
   only by deleted modules (for example `tree-sitter-zig`) leave with them.
   This list is a starting point and is cheap to change later: each entry is
   one file plus one registry line.
3. Delete everything the closure never reaches: `llm.py`, `serve.py`, `cli.py`,
   `__main__.py`, `install.py`, `watch.py`, `hooks.py`, `prs.py`, `reflect.py`,
   `wiki.py`, `analyze.py`, `callflow_html.py`, `tree_html.py`,
   `exporters/html.py`, all `skill*.md`, `always_on/`, `skills/`.
4. Remove the `graphifyy` dependency from `pyproject.toml`; add the surviving
   core dependencies explicitly.

Dynamic imports (`importlib`) are invisible to the closure query; the golden
test below is what catches a missed module.

### Changes at source

Each deletes a workaround from the first POC:

| Change | Where | Deletes |
|---|---|---|
| Build with `directed=True`; exporter writes `source`/`target` as-is and emits no `_src`/`_tgt` markers. `cluster` already accepts a DiGraph. | `extract.py` (our wrapper), `export.py` | `kgc/direction.py`, `normalize_direction` |
| No global merge. Push prefixes sourced ids with `tag::` and sets `repo` using a reduced `prefix_graph_for_global` (no `community_offset`: communities are per-repo and read with `repo`). Externals stay unprefixed and so merge by id in the database. | `graph/neo4j_io.py` | `kgc/globalgraph.py`, `retag_shared_stubs`, global manifest |
| Exporter emits `callable` and `origin` (not `_callable`, `_origin`); push stops stripping underscore props because there are none. | `export.py` | the strip in `push_global_graph` |
| Blind-spot record: `detect` gains an `unhandled` list — files that pass the ignore rules (`.gitignore`, `.graphifyignore`, build-output and secret-prone filters) but match no kept extractor's extension set, excluding the doc, paper, image, office and video classes we exclude on purpose. Build writes it as `skipped_extensions: {".q": 3, …}` (extension → file count) into `graph.json`; push copies it to the `Repo` node. | `detect.py`, our wrapper, `export.py` | nothing; new |

### Vendoring metadata

`kgc/extract/VENDORED.md` records: upstream URL, tag `v0.9.74`, wheel
`graphifyy-0.9.74`, the date, the full list of deleted modules, the list of
kept modules with line counts, and every local modification by file. Upstream
`LICENSE` (Apache-2.0), `LICENSE-MIT` and `NOTICE` travel with the tree
unchanged. The root `pyproject.toml` declares `kgc` under Apache-2.0 with a
NOTICE reference.

### Dependencies after the strip

Core: `networkx>=3.4`, `numpy>=1.21`, `rapidfuzz>=3.0`, `tree-sitter<0.26,>=0.23`,
the 25 grammar wheels upstream pins as core, plus `tree-sitter-sql`, `neo4j>=5.28`,
`pyyaml>=6.0`, `mcp>=2.3,<3`. Nothing else. A test asserts the declared list
equals the set of third-party imports in the tree.

## Neo4j schema

A superset of what is in the database today (40,398 nodes, 122,277 edges at
2026-10-04), so Plan 1 runs against it unchanged.

### `CodeNode`

| Property | Meaning |
|---|---|
| `id` | `tag::local_id` for sourced nodes; bare `local_id` for externals, so two repos referencing the same library symbol merge onto one node |
| `label` | display name, e.g. `validate_analysis()` |
| `source_file`, `source_location` | repo-relative path; `Lnn` |
| `repo` | tag. **Absent** on externals — this is what makes reap-by-tag safe |
| `external` | boolean |
| `file_type` | `code`, `rationale`, `external`, `package` |
| `community` | per-repo cluster id (int) |
| `callable`, `origin` | today's `_callable`, `_origin`, emitted clean by the Plan 2 exporter |

### Relationships

Native types, as today: `calls`, `indirect_call`, `imports`, `imports_from`,
`contains`, `method`, `inherits`, `references`, `uses`, `rationale_for`.
Direction convention: caller → callee, importer → imported, container →
contained, class → method, subclass → superclass, docstring → code. Properties:
`confidence` (`EXTRACTED` | `INFERRED`), `weight`, `source_file`,
`source_location`, `context`.

### `Repo` (new)

One per repo, replaced on every push, written last.

| Property | Meaning |
|---|---|
| `tag` | unique |
| `path` | filesystem path at build time |
| `pushed_at` | ISO-8601 UTC |
| `built_at_commit` | git HEAD of the repo at build, or `null` |
| `node_count`, `edge_count` | as pushed |
| `skipped_extensions` | JSON string `{".q": 3, …}`, or `"null"` before Plan 2 |

### Constraints and indexes

```
CREATE CONSTRAINT codenode_id IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.id IS UNIQUE
CREATE CONSTRAINT repo_tag    IF NOT EXISTS FOR (r:Repo)     REQUIRE r.tag IS UNIQUE
CREATE INDEX codenode_repo  IF NOT EXISTS FOR (n:CodeNode) ON (n.repo)
CREATE INDEX codenode_label IF NOT EXISTS FOR (n:CodeNode) ON (n.label)
CREATE FULLTEXT INDEX kgc_text IF NOT EXISTS FOR (n:CodeNode) ON EACH [n.label, n.source_file]
```

Aura Free caps: 50,000 nodes / 175,000 relationships. Plan 1 adds at most one
`Repo` node per repo.

## Error handling

### Server

Never dies on a tool call; never returns a traceback. One-line shapes:

| Condition | Response |
|---|---|
| Neo4j unreachable | `cannot reach Neo4j at <host>; is the instance paused?` — next call retries the connection |
| Transaction timeout (10 s) | `timed out; narrow by repo or reduce depth` |
| Symbol not found | `no node matches <X>` + three nearest search hits |
| Ambiguous symbol | candidate list (repo, label, file:line) |
| `cypher` with a write keyword | `refused: read-only server` (and the read transaction would refuse it anyway) |
| Output over budget | truncated at a line boundary + narrowing hint |

All error text passes through the existing `_scrub` so the password cannot
appear in output.

### Build

Per-repo isolation as today: one repo's parse failure does not stop the others;
the CLI reports per-repo status and exits non-zero if any failed. The runtime
token-spend assertion is replaced by a structural test (below).

### Push

Preflight before reap, so a repo is never deleted unless its replacement fits.
A push that dies mid-way leaves the repo partially present with no `Repo`
node; re-running converges, and `stats` reports `last push incomplete` until
it does.

## Testing

### Plan 1 (no database required)

- `format.py`: truncation exactly at budget, hint appended, never mid-line.
- `resolve.py`: each tier wins in turn; ambiguity returns candidates; zero hits
  returns nearest three. Driver is a fake by injection (as today's push tests).
- `queries.py`: each Cypher builder as a string snapshot; parameter names
  stable.
- `cypher` guard: every keyword in the regex refused, in any case; a query
  whose only occurrence of a keyword is inside a quoted string literal is
  **not** refused; a literal containing an escaped quote followed by a keyword
  is refused (the documented false positive, asserted so a later fix is
  deliberate).
- `kgc init`: file mode 0600; no credential strings in the written JSON.
- Startup description rendering: with and without a reachable driver.

### Plan 1 (database, opt-in)

`KGC_INTEGRATION=1` runs the nine tools read-only against the real instance and
asserts shape, not content.

### Plan 2

- **Golden regression test** — the single thing that makes deleting 49,000
  lines safe. *Before any deletion*, extract the five repos with upstream
  0.9.74 and record per-repo multisets of `(id, label, file_type)` nodes and
  `(source, target, relation)` edges, with direction taken from `_src`/`_tgt`.
  After the strip, the vendored extractor must reproduce them exactly.
- No-network invariant: no module under `kgc/extract` imports `requests`,
  `httpx`, `urllib.request`, `urllib3`, `openai`, `anthropic`, `boto3`, `socket`
  or `subprocess`-with-network. Static test over the AST.
- Closed-world invariant: every import in `kgc/extract` resolves to stdlib,
  `kgc.extract`, or a declared dependency.
- `skipped_extensions`: a fixture repo with `.py` and `.q` files yields
  `{".q": 1}`.

### Whole suite

Existing 107 tests continue to pass under `-W error` throughout both plans.

## Verification experiment

Third arm `kgc` in `verify/run_arm.py`: same twelve questions, `--runs 2`,
same metrics as the treatment arm (`context_tokens`, correctness judged by the
user against the code, `permission_denials`, `contamination`, graph-tool-call
count).

**Precondition:** the treatment baseline (human steps 3 and 4 from the first
POC) must exist first. Without it there is nothing to compare against.

**Acceptance:** the `kgc` arm matches or beats the treatment arm on correct
answers while using no more context tokens, and the fraction of runs in which
the agent called at least one graph tool is reported alongside (the
CodeCompass failure mode: agents that never reach for the tool).

## Risks

| Risk | Mitigation |
|---|---|
| Closure query misses a dynamically imported module | Golden test fails loudly on `ImportError` or output diff |
| Full-text relevance worse than graphify's tuned in-memory search | Measured directly by the `kgc` arm against the treatment arm |
| Grammar wheel pins (`tree-sitter<0.26`) absent from the bank's internal index | Dependency list is explicit and short; mirror once |
| Startup description rendering needs the database | 3 s timeout, generic fallback |
| `cypher` guard false positive on escaped quotes inside literals | Documented; agent can use the specific tools |

## Appendix — module map, first POC → this design

| First POC | This design |
|---|---|
| `kgc/extract.py` (wrapper over `graphify.*`) | `kgc/extract/` (vendored core) + thin wrapper |
| `kgc/direction.py` | deleted |
| `kgc/globalgraph.py` | deleted; `prefix` function moves into `kgc/graph/neo4j_io.py` |
| `kgc/neo4j_io.py` | `kgc/graph/neo4j_io.py` + `kgc/graph/schema.py` |
| `setup_mcp.py` | `kgc init` |
| `graphify.serve` MCP + `mcp-neo4j-cypher` | `kgc/server/` |
| `~/.graphify/repos/<tag>/graphify-out/graph.json` | `~/.kgc/repos/<tag>/graph.json` |
| `~/.graphify/global-graph.json`, `global-manifest.json` | deleted |
