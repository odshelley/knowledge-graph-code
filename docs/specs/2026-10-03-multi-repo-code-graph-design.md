# Multi-Repo Code Graph — Design

**Date:** 2026-10-03
**Status:** Proof of concept
**Author:** Osian Shelley (with Claude)

## Goal

Make several codebases queryable from inside a Claude Code session at materially
lower token cost than letting the agent read files, and prove the approach well
enough to decide whether to build it at work.

This is a POC run against local repositories in `~/Projects`. The target
environment is a large investment bank with 50–200 repositories, a self-hosted
Neo4j Community instance on AWS, and no code egress. The POC must therefore be
*transferable*: the build and push steps must not depend on anything local to
this machine.

## Success criteria

The POC succeeds if, across roughly ten real questions spanning several repos:

1. Graph-backed answers cost materially fewer tokens than file-reading answers.
2. Graph-backed answers are correct — cheaper and wrong is a failure.
3. Cross-repo questions ("which of my repos use X") are answerable at all, which
   they currently are not.

If (1) and (2) do not both hold, the approach is rejected. This must be
measurable within a day of the build completing.

## Non-goals

Explicitly out of scope, recorded so they are decisions rather than omissions:

- **Merge/CI triggers.** Refresh is a manual `build.py` run. At work the correct
  trigger is CI on push to master, pushing to Neo4j with credentials from CI
  secrets — a local git hook only fires when the developer merges or pulls, so a
  shared graph would silently go stale. `build.py` and `push.py` are therefore
  plain CLIs with no knowledge of their caller, so swapping a manual run for a
  CI runner is configuration, not a rewrite.
- **kdb+/q, SAS.** graphify recognises no `.q` extension, so kdb+ is invisible to
  the graph. `extractors/` is a genuine plugin architecture (`base.py`,
  `engine.py`, ~34 language modules including COBOL and Fortran), so a `q.py`
  extractor is bounded work — but it is not POC work. The blind spot is
  documented, not fixed.
- **Prose, docs and runbooks.** Measured on `knowledge-retrieval` 2026-10-03:
  353 document-derived nodes produced **zero** edges into any Python symbol, at a
  cost of ~663k tokens. Prose does not self-link to code. Code-only extraction is
  free (pure AST, no LLM). This graph is code-only.
- **Team hosting.** Single user, stdio MCP. graphify ships an HTTP transport with
  API-key auth (`serve_http`), so this is a later switch-on, not a redesign.
- **Writes from the agent.** Neo4j MCP is configured read-only.

## Architecture

Four components. The build is a pipeline of plain CLIs; the query path is two
MCP servers with different cost profiles.

```
repos.yaml
    │
    ▼
build.py ──► graphify extract (per repo, AST only, zero tokens)
    │              └─► <repo>/graphify-out/graph.json
    │
    └──────► graphify global add --as <tag>
                   └─► ~/.graphify/global-graph.json
                         (ID prefixing, stale pruning, cross-repo linking)
                             │
                             ├──► graphify stdio MCP ──► Claude Code  [cheap path]
                             │
                             └──► push.py ──► Neo4j Aura
                                                  │
                                                  └──► mcp-neo4j-cypher ──► Claude Code
                                                                            [precise path]
```

### Why two query paths

They answer different shapes of question and have different costs.

**graphify stdio MCP** is the default. Its `query_graph` tool does IDF-weighted
term scoring, uses a trigram index for substring candidates, selects BFS seeds
with a score gap ratio, and renders the result subgraph through
`_subgraph_to_text(..., token_budget)`, which hard-cuts at roughly three
characters per token. It is purpose-built for the token-cost goal. Tools
available: `query_graph`, `get_node`, `get_neighbors`, `get_community`,
`god_nodes`, `graph_stats`, `shortest_path`.

**mcp-neo4j-cypher** (official, `neo4j/mcp`) is the escape valve: `get-schema`
and `read-cypher` for questions the graph tools cannot express — cross-repo
aggregates, "every repo referencing this deprecated API", arbitrary traversal.
Cypher returns rows and the agent pays full token price for them, so this is
deliberately the second choice, not the default.

Neo4j's role is **durable shared store plus precise query**, not retrieval. It
also removes the single-JSON-file size ceiling for breadth queries.

## Components

### 1. `repos.yaml`

The only file that decides what is in scope. One entry per repository.

First-run set, decided 2026-10-03 — five repos giving both cross-repo and
cross-language signal:

```yaml
repos:
  - path: ~/Projects/knowledge-retrieval
    tag: knowledge-retrieval
    enabled: true
  - path: ~/Projects/graphify
    tag: graphify
    enabled: true
  - path: ~/Projects/neural_particle_method
    tag: neural-particle-method
    enabled: true
  - path: ~/Projects/diffusion-bridge
    tag: diffusion-bridge
    enabled: true
  - path: ~/Projects/claude
    tag: claude
    enabled: true
```

`claude` is TypeScript-dominant and the other four are Python, so the set
exercises multi-language extraction as well as cross-repo linking. Querying
`graphify` through a graph built by graphify is a deliberate self-test.

Adding a repo is a one-line edit. `tag` is the namespace for that repo's nodes
and the key the Neo4j reaper scopes on, so tags must be stable — renaming a tag
orphans that repo's nodes in Neo4j.

No per-repo include/exclude globs in the POC. If a repo needs files excluded, it
gets a `.graphifyignore`, which is graphify's own mechanism and is what the
code-only enforcement below already uses.

### 2. `build.py`

Per enabled repo: run graphify's code-only extraction into that repo's own
`graphify-out/` (keeping each repo self-contained and independently
refreshable), then `graphify global add --as <tag>`.

**How code-only is enforced** (this matters — the default pipeline would invoke
LLM subagents on every doc and cost hundreds of thousands of tokens): run
`graphify.detect.detect()`, then zero the `document`, `paper`, `image` and
`video` entries in the returned `files` dict before extraction, and write an
empty semantic result file. This is graphify's own documented code-only fast
path: with no non-code files present, the semantic stage is skipped entirely and
only the deterministic AST pass runs. Verified on `knowledge-retrieval`:
0 tokens, ~15 seconds, 1321 nodes.

Each repo additionally gets a `.graphifyignore` excluding `docs/`, prose formats
and `graphify-out/`, so a later bare `graphify update` in that repo cannot
silently pull documents back in.

Relies on verified graphify behaviour:

- `global_add` computes a hash of the source graph and **returns
  `skipped: True` when unchanged**, so re-running is cheap.
- `global_add` calls `prune_repo_from_graph(G, repo_tag)` before merging, so a
  repo's stale nodes are removed on re-add. **The JSON global graph needs no
  reaper from us.**
- `prefix_graph_for_global` rewrites repo-local node IDs to `{tag}::{id}`, sets a
  `repo` attribute on every repo-local node, preserves the original in
  `local_id`, and offsets community IDs so communities from different repos do
  not collide.
- **External stubs are intentionally left unprefixed** and carry `external: true`
  with no `repo` attribute, so stdlib and third-party modules unify into one node
  across all repos. This is what makes "which of my repos depend on X"
  answerable, and it has a consequence for the reaper (below).
- Cross-repo edges are added by `link_cross_repo_member_calls` (resolves member
  calls that cross a repo boundary) and `link_shared_type_declarations` (joins
  identically declared types across repos). `global_add` reports a
  `cross_repo_calls` count.

Output: a per-run summary of nodes, edges, wall time, repos skipped, repos
failed, and cross-repo edges added.

Code extraction uses no LLM and costs zero tokens. Reference measurement:
`knowledge-retrieval`, 148 files → 1321 nodes → 1.7 MB, ~15 seconds.

### 3. `push.py`

Pushes the global graph to Neo4j, per repo tag.

`graphify`'s `push_to_neo4j` uses `MERGE` for nodes and edges, so re-running is
safe and does not duplicate. It also creates `CREATE INDEX IF NOT EXISTS FOR
(n:<label>) ON (n.id)` for every distinct label up front, and sanitises labels
and relationship types against Cypher injection.

**But MERGE never deletes.** A function removed from the code keeps its Neo4j
node forever. So each tag's push is preceded by a scoped reap:

```cypher
MATCH (n) WHERE n.repo = $tag DETACH DELETE n
```

This is safe precisely because `prefix_graph_for_global` sets `repo` on every
repo-local node and on no external node. External nodes are shared and must
survive. They do, however, orphan when the last repo referencing them is
removed, so a separate sweep runs after all pushes:

```cypher
MATCH (n) WHERE n.external = true AND NOT (n)--() DELETE n
```

Pre-flight: count nodes to be written and compare against the instance cap
(see Constraints) — fail loudly rather than half-push.

### 4. `.mcp.json` and `setup-mcp.py`

Project-scoped MCP configuration for both servers. `setup-mcp.py` reads `.env`
and writes `.mcp.json` so credentials are never committed. The Neo4j server is
configured **without** `write-cypher`.

## Failure modes

| Failure | Handling |
|---|---|
| One repo fails extraction | Skip, record in the run summary, continue. A single bad repo must never abort the run or leave the global graph half-updated. |
| Aura instance paused (3 days idle) | Detect connection failure on push, report plainly with the console URL. Do not retry indefinitely. |
| Node cap exceeded | Pre-flight count; refuse to push and report which tags would exceed it. |
| Bad or renamed tag | Reap is scoped to `n.repo = $tag`; a tag matching nothing deletes nothing. Tag renames are called out as orphan-producing in `repos.yaml` docs. |
| Graph file exceeds size cap | graphify's own `check_graph_file_size_cap` raises; surface it rather than swallowing. |
| Partial push interrupted | MERGE is idempotent, so re-running `push.py --repo <tag>` converges. Reap-then-push is not atomic; an interrupt between them leaves that tag empty until the next run. Acceptable for a POC; noted as a production concern. |

## Verification

The premise is token saving, so it gets measured rather than assumed.

Pick ~10 questions spanning several repos, including at least three genuinely
cross-repo ("which repos use `dagster`", "where else is this pattern
implemented"). Answer each twice in separate sessions:

- **Control:** agent reads files normally.
- **Treatment:** agent uses only the two MCP servers.

Record per question: tokens consumed, wall time, and a correctness verdict
judged against the actual code. Report the distribution, not just the mean — a
good average hiding one confidently wrong answer is a failure, because the
failure mode of a graph is plausible-but-stale.

Also record, as a known-limitation check: at least one question the graph gets
*wrong* because of a structural blind spot. A worked example already exists —
`push_one(client, ...)` takes an untyped injected client, so no static edge links
the Zotero push path to `ZoteroClient`, and the graph understates real coupling.
The POC should demonstrate it knows where it is blind.

## Constraints and known risks

- **Aura Free caps.** Neo4j's support docs state 200,000 nodes / 400,000
  relationships; their pricing page states 50,000 / 175,000. Treat the lower pair
  as the risk case. Free instances **pause after 3 days idle and are deleted
  after 30**. At ~1300 nodes per 150-file repo, a 15-repo POC fits comfortably
  either way.
- **The POC environment differs from the target.** Testing runs on managed Aura;
  work runs self-hosted Neo4j Community. Community has a single database (no
  isolation for the code graph), **no RBAC** (one credential, write access for
  anyone holding it — read-only cannot be enforced by the database, only by how
  the MCP server is configured), and offline-only backups. A clean POC here does
  not validate the work deployment's scale or access model.
- **Merged-graph ceiling.** At ~11 KB of graph per source file, 10–20
  bank-sized repos is 60–115 MB and comfortable; ~50 repos is ~290 MB and
  painful; 200 is not viable as one JSON file. Past that, either route queries
  per-repo via the MCP server's LRU context cache (`GRAPHIFY_MAX_CONTEXTS`,
  default 8) or move retrieval onto Neo4j — at which point the token-budgeting
  layer would need reimplementing on Cypher.
- **Credentials in transcript.** The Aura password for this instance was pasted
  into a Claude Code session and should be rotated when the POC ends.
- **`scip_ingest.py` is not usable.** It self-describes as a skeleton, "NOT a
  full SCIP protobuf implementation", and "not wired to the CLI in this phase".
  Higher-fidelity C#/Java indexing via SCIP is not available today.

## Open questions

1. Does the Neo4j path earn its place in the POC at all, or would graphify's
   stdio MCP alone answer every question? Worth measuring, since a negative
   result simplifies the work build considerably. Neo4j stays in scope for the
   POC precisely so this can be answered with evidence.
