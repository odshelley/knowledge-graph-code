# Multi-Repo Code Graph — Design

**Date:** 2026-10-03
**Status:** Proof of concept — built on `feat/multi-repo-code-graph`; this spec
was updated 2026-10-04 to match what was built, with measured numbers where
the original had estimates.
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

- **Merge/CI triggers.** Refresh is a manual `kgc build` run. At work the
  correct trigger is CI on push to master, pushing to Neo4j with credentials
  from CI secrets — a local git hook only fires when the developer merges or
  pulls, so a shared graph would silently go stale. `kgc build` and `kgc push`
  are therefore plain CLIs with no knowledge of their caller, so swapping a
  manual run for a CI runner is configuration, not a rewrite.
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
- **A framework-aware extractor.** Dependency injection, registries and
  config-driven wiring are invisible to an import/call graph (see Known blind
  spots). Resolving them into real edges is the named gap this POC's result is
  read against, not something it attempts.

## Architecture

One small Python package, `kgc`, wrapping graphify's library API, plus a usage
instruction for the agent. The build is a pipeline of plain CLIs; the query
path is two MCP servers with different cost profiles.

```
repos.yaml
    │
    ▼
kgc build ──► graphify detect + extract (per repo, AST only, zero tokens)
    │              └─► ~/.graphify/repos/<tag>/graphify-out/graph.json
    │
    ├──────► graphify global_add(<tag>)
    │              └─► ~/.graphify/global-graph.json
    │                    (ID prefixing, stale pruning, cross-repo linking)
    │
    └──────► two post-merge passes graphify does not do:
                 normalize_direction  (kgc/direction.py)
                 retag_shared_stubs   (kgc/globalgraph.py)
                             │
                             ├──► graphify stdio MCP ──► Claude Code  [cheap path]
                             │
                             └──► kgc push ──► Neo4j Aura
                                                  │
                                                  └──► mcp-neo4j-cypher ──► Claude Code
                                                                            [precise path]

setup_mcp.py ──► .mcp.json (both servers; password via env, mode 0600)
CLAUDE.md    ──► tells the agent to use the servers, and how
verify/      ──► the measurement harness (questions, headless runner, results form)
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

**mcp-neo4j-cypher** (official, `neo4j/mcp`, pinned at 0.6.0 in
`setup_mcp.py`) is the escape valve: `get_neo4j_schema` and
`read_neo4j_cypher` for questions the graph tools cannot express — cross-repo
aggregates, "every repo referencing this deprecated API", arbitrary traversal.
Cypher returns rows and the agent pays full token price for them, so this is
deliberately the second choice, not the default. Impact and reverse-dependency
questions ("what would break if X changed") go here too, not to graphify's
`affected` command, for the direction reasons under Component 2.

Neo4j's role is **durable shared store plus precise query**, not retrieval. It
also removes the single-JSON-file size ceiling for breadth queries.

### Why the agent needs telling

An audit of the graph-tool literature (CodeCompass and its successors) found
that agents rarely reach for graph tools unprompted: given both a graph server
and a file system, they grep. The project's `CLAUDE.md` is therefore a
first-class component, not documentation — it instructs the agent to use the
graphify server first for structure questions and the Neo4j server for
cross-repo aggregates, and records the schema facts (direction, labels, the
`source_file = ''` stub convention) a correct Cypher query depends on. The
verification harness runs treatment with this file in scope and control
without it; that asymmetry is the thing being measured.

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
orphans that repo's nodes in Neo4j. A tag may not contain `::` (graphify's ID
separator), tags must be unique, and an entry missing `tag` or `path` fails
loading with an error naming the entry.

No per-repo include/exclude globs in the POC. **No per-repo `.graphifyignore`
is written either** — the original design had one as belt-and-braces, but the
category-zeroing in Component 2 is what actually enforces code-only for `kgc`'s
own runs, and `kgc` now writes nothing into the target repos at all. A bare
`graphify update` run by hand inside a repo is outside `kgc`'s control.

### 2. `kgc build`

Per enabled repo (`kgc/extract.py`, `kgc/globalgraph.py`, `kgc/cli.py`):

1. `graphify.detect.detect(root=<repo>, cache_root=~/.graphify/repos/<tag>,
   extra_excludes=["*.pdf"])`, then zero the `document`, `paper`, `image` and
   `video` entries in the returned `files` dict. This is graphify's own
   documented code-only fast path: with no non-code files present the semantic
   stage has nothing to do and only the deterministic AST pass runs. The `*.pdf`
   exclude only silences a stray warning for work that is discarded anyway.
2. `graphify.extract.extract(code_paths, cache_root=<same>, root=<repo>)`.
   `root` anchors node ids and `source_file` relativisation; `cache_root` only
   decides where graphify's cache is written. The result's `input_tokens` /
   `output_tokens` are asserted to be zero — the guarantee is checked, not
   trusted.
3. `build_from_json`, then `cluster()` and a `community` attribute stamped on
   every node (graphify's MCP server reconstructs communities from it).
4. `graph.json` is written by `kgc` directly as node-link JSON with a `links`
   key — **not** through `graphify.export.to_json`, which carries the #479
   shrink guard and would refuse to write a graph that legitimately got smaller
   after code was deleted. Keeping the `_src`/`_tgt` markers on every link is
   load-bearing: they are the only record of true edge direction (below).
5. `graphify.global_graph.global_add(graph_path, tag)`.

**Where outputs go.** Each repo's `graph.json` and graphify's extraction cache
land under `~/.graphify/repos/<tag>/graphify-out/`, beside graphify's own global
graph. Nothing is written into the analysed repository. The original design
wrote to `<repo>/graphify-out/`; that dirtied five checkouts and, worse for the
measurement, left a ready-made graph in the control arm's working directory
(the user's global graphify skill treats any question as a graphify query when
`graphify-out/` exists).

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
  calls that cross a repo boundary). `global_add` reports a `cross_repo_calls`
  count. **Across the five repos it is 0**: none of them imports another as a
  library, so there is no real cross-repo code coupling for the pass to find.

#### Edge direction

graphify stores true edge direction in each link's `_src`/`_tgt` markers; the
graph itself is undirected and `(source, target)` order is arbitrary. Its own
writers disagree about this: `export.to_json` normalises `source`/`target`
from the markers (#563), but the global-graph writer does not, and the
consumers `affected` and `serve._load_graph` trust the raw order. Measured
before normalisation, **39.2% of links in the merged graph were reversed**,
which made `graphify affected` roughly a coin toss on "what calls X".

`kgc` therefore reorients links from their markers twice: once in each repo's
`graph.json` right after extraction, and once over the whole global graph at
the end of every build (`normalize_direction`). The pass is idempotent.

Markers go stale under dedup (next paragraph): `global_add` rewires an incident
edge's `source`/`target` onto the surviving node but does not update that
edge's `_src`/`_tgt`. At five repos, 1,343 links had exactly one marker still
naming a real node, and in every one the raw order contradicted it. Reorienting
onto a stale marker would materialise a phantom, attribute-less node, so
`kgc/direction.py` handles the three cases separately: both markers valid →
rewrite from the markers; exactly one valid and it names one of the link's own
endpoints → that endpoint keeps the marker's role, the other endpoint takes the
other role, and the stale marker is **repaired** to it (so the markers become
self-consistent and the Neo4j push, which reads them, stores the right
direction); anything else → left as written and counted skipped. On the
rebuilt five-repo graph: 1,343 repaired, 0 skipped, 0 links whose raw order
disagrees with their markers.

Both behaviours — the global writer / `affected` ignoring the markers, and
dedup rewiring edges without updating them — are graphify bugs and are
candidates for upstream issues.

#### Dedup of sourceless nodes

`global_add` dedups **any sourceless node by label** across repos: its
predicate is `not source_file`, not `external`. So a repo-local unresolved
reference — `Exception`, `Path`, `parametrize`, `fixture` — collapses onto the
first-merged repo's copy, which keeps that repo's tag and a `local_id`. At five
repos that left 11 such survivors, all tagged `knowledge-retrieval` or
`graphify`, with 1,343 incident edges from other repos; a naive Cypher query
for cross-repo coupling counted 1,239 edges from `knowledge-retrieval` to
`graphify` that were really every repo inheriting from `Exception`, and a
scoped `kgc push --repo knowledge-retrieval` would have reaped the survivors
along with every other repo's edges onto them.

`kgc build` therefore ends with `retag_shared_stubs`: any node with no
`source_file` and a `repo` whose incident edges come from two or more distinct
repos is semantically a shared stub, and is marked `external: True` with its
`repo` removed. Reaps then leave it alone and every push includes it. Shared
stubs of both kinds are recognisable in Cypher by `source_file = ''`; real
cross-repo coupling is `WHERE a.source_file <> '' AND b.source_file <> ''`.

Output: a per-repo line (nodes, edges, wall time, skipped/added/removed,
cross-repo edges), then the two pass summaries and the global totals. A repo
that fails extraction is reported and skipped; the run continues.

Code extraction uses no LLM and costs zero tokens. Measured, 2026-10-04: five
repos, 3,509 code files → **40,398 nodes / 122,277 edges**, 77.9 MB of
`global-graph.json`, ~40 s wall on a warm cache (the first build is longer).

### 3. `kgc push`

Pushes the global graph to Neo4j, per repo tag (`kgc/neo4j_io.py`).

Rather than graphify's `push_to_neo4j`, `kgc` does its own push so that the
result is Cypher-friendly: every node gets the label `CodeNode` (one index on
`id`, one on `repo`), nodes are `MERGE`d on `id` in batches of 1,000, and edges
are written **one statement per relationship type** (`calls`, `imports`,
`inherits`, …; 27 types at five repos) so `MATCH ()-[:imports]->()` works in the
MCP server. The type is interpolated — Cypher cannot parameterise it — through
a whitelist sanitiser. Direction comes from the `_src`/`_tgt` markers, falling
back atomically to the raw order if the pair does not name the edge's own
endpoints. Properties beginning with `_` (graphify internals: `_origin`,
`_callable`, the markers themselves) are stripped from both nodes and edges.
Re-running is safe: MERGE does not duplicate.

**But MERGE never deletes.** A function removed from the code keeps its Neo4j
node forever. So each tag's push is preceded by a scoped reap:

```cypher
MATCH (n:CodeNode) WHERE n.repo = $tag DETACH DELETE n
```

This is safe precisely because `prefix_graph_for_global` sets `repo` on every
repo-local node and on no external node, and `retag_shared_stubs` has removed
it from the dedup survivors. External nodes are shared and must survive. They
do, however, orphan when the last repo referencing them is removed, so a
separate sweep runs after all pushes:

```cypher
MATCH (n:CodeNode) WHERE n.external = true AND NOT (n)--() DELETE n
```

An edge is written when **at least one** endpoint is in the push. If the other
endpoint is not in the database the `MATCH` is a harmless no-op; when that repo
is pushed later the same row lands. This is what makes `kgc push --repo <tag>`
converge on the full edge set across partial pushes — the original design's
"both endpoints present" filter did not.

Pre-flight: count nodes and relationships and compare each against the
instance caps (see Constraints) — fail loudly rather than half-push. Every
error path scrubs the password before it can reach a log. `--dry-run` plans
without connecting.

### 4. `.mcp.json` and `setup_mcp.py`

Project-scoped MCP configuration for both servers. `setup_mcp.py` reads `.env`
and writes `.mcp.json` so credentials are never committed: the file is created
with mode 0600 (not written then chmod'd), the Neo4j password goes through the
server's environment rather than argv, and `mcp-neo4j-cypher` is pinned at
0.6.0 — the version whose tool names `CLAUDE.md` gives — with `--read-only` and
`--token-limit 4000`.

### 5. `CLAUDE.md`

See "Why the agent needs telling". Also records the schema facts a correct
Cypher query depends on: relationship direction `(caller)-[:calls]->(callee)`,
the `CodeNode` label and properties, and that shared stubs have
`external: true`, no `repo`, and `source_file = ''`.

### 6. `kgc doctor`, `kgc status`

`doctor` proves the connection and resolves the database name (Aura supplies an
instance id where the database is often just `neo4j`), and reports counts.
`status` lists the repos registered in the global graph. Neither is on the
build path.

### 7. `verify/`

`questions.md` (the twelve questions with grader notes), `run_arm.py` (the
headless runner), `results.md` (the form a human fills in). See Verification.

## Failure modes

| Failure | Handling |
|---|---|
| One repo fails extraction | Skip, record in the run summary, continue. A single bad repo must never abort the run or leave the global graph half-updated. |
| Malformed `repos.yaml` entry | `ValueError` naming the entry and the missing key; nothing runs. |
| Aura instance paused (3 days idle) | Detect connection failure on push, report plainly with the console URL. Do not retry indefinitely. |
| Node or relationship cap exceeded | Pre-flight count of both; refuse to push and report which limit. |
| Bad or renamed tag | Reap is scoped to `n.repo = $tag`; a tag matching nothing deletes nothing. Tag renames are called out as orphan-producing in `repos.yaml` docs. |
| Graph file exceeds size cap | graphify's own `check_graph_file_size_cap` raises; surface it rather than swallowing. |
| Partial push interrupted | MERGE is idempotent and an edge is written whenever either endpoint is in the push, so re-running `kgc push --repo <tag>` converges. Reap-then-push is not atomic; an interrupt between them leaves that tag empty until the next run. Acceptable for a POC; noted as a production concern. |
| Stale `_src`/`_tgt` after dedup | Repaired when the surviving marker names an endpoint, else skipped and counted; never reoriented onto a phantom. |

## Verification

The premise is token saving, so it gets measured rather than assumed.

**Questions.** Twelve, weighted to what deterministic code graphs are actually
good at: 5 cross-repo/impact, 3 single-repo "how does X work" (where grep is
expected to win), 2 hidden-dependency probes (dependency-injected callees with
no static edge), 2 blind-spot (kdb+/q, which no extractor sees; TypeScript ↔
Python, which no per-language AST can join). The weighting follows an audit of
the graph-tool literature, which found graphs reliably beat grep on transitive
impact, cross-repo and hidden dependencies, and "where does this live" at
scale, and reliably lose on "how does X work". Each question's grader note
(the verified answer and grading rule) lives in an indented sub-bullet that the
runner never sends to the model.

**Arms.** Each question is answered in separate headless sessions:

- **Control:** cwd is the question's repo; an empty `--mcp-config` with
  `--strict-mcp-config`, so no MCP server; the agent reads files normally.
  Cross-repo and blind-spot questions get a preamble naming the five repos'
  paths, so those classes are not impossible for control merely because of its
  starting directory; single-repo and hidden-dependency questions get none.
- **Treatment:** cwd is this project, so `CLAUDE.md` loads; `--mcp-config
  .mcp.json --strict-mcp-config`, so `graphify` and `neo4j` are the only MCP
  servers in the session.
- **Both:** `--disallowedTools Skill --disable-slash-commands`, so no user skill
  (notably the graphify skill) can take part. The init event's `mcp_servers`,
  `skills`, `memory_paths` and `cwd` are recorded per run, and a
  `contamination` counter tallies built-in tool calls whose input mentions
  `graphify-out`, so isolation is checked, not assumed.

**Replication.** At least two runs per question per arm (three recommended):
Claude models flip their answer to the same prompt at temperature 0 roughly 9%
of the time per instance, so one run per arm cannot separate a real difference
from noise. Report the median and the spread; a wrong answer in any run counts
against that arm.

**The token figure** is `context_tokens = input_tokens + cache_creation_tokens
+ cache_read_tokens`. With prompt caching nearly everything the model reads
arrives as cache creation or cache read — the harness's one real sample had
`input_tokens = 10` against `cache_creation_input_tokens = 25,641` — so
input+output alone omits ~99% of the context. `total_cost_usd` is reported
beside it. A run with `permission_denials > 0` is invalid (a denied MCP call is
indistinguishable in the answer from the agent choosing not to use the graph)
and is re-run with an allow-list; a single pre-flight treatment run must show
`graphify_calls > 0`, `permission_denials == 0` and exactly the two servers
before the batch is spent.

Record per run: `context_tokens` and its components, cost, wall time, turns,
which server answered (`graphify_calls` / `neo4j_calls`), and a correctness
verdict judged against the actual code. Report the distribution, not just the
mean — a good average hiding one confidently wrong answer is a failure, because
the failure mode of a graph is plausible-but-stale.

## Known blind spots

An import/call graph has an edge only where the extractor could name a concrete
target at parse time. It is therefore **structurally blind to dependency
injection, registries and configuration-driven wiring** — any coupling decided
at runtime by a parameter, a config value, a dynamic `getattr`, or a framework's
own container. That is the dominant coupling style in the kind of enterprise
(Spring-style) codebase this POC is meant to inform a decision about. Two
worked examples already exist in `knowledge-retrieval`: `push_one(client, ...)`
takes an untyped injected client, so no static edge links the Zotero push path
to `ZoteroClient`; `resolve_concepts` calls an injected `adjudicate` whose live
LLM call is bound by the caller. The graph understates real coupling in both.
Questions 9 and 10 check that treatment says so rather than fabricating a
plausible edge; questions 11 and 12 do the same for a language graphify does
not parse and for a coupling that crosses a language boundary at runtime. A
framework-aware extractor that resolves these into real edges is out of scope
and is the named gap the result should be read against.

## Constraints and known risks

- **Aura Free caps.** Neo4j's support docs state 200,000 nodes / 400,000
  relationships; their pricing page states 50,000 / 175,000. Treat the lower pair
  as the risk case, which `kgc push` pre-flights against. **Measured: 40,398
  nodes / 122,277 relationships at five repos — 81% and 70% of the pessimistic
  caps.** A sixth repo of this size trips them; the POC set is the maximum this
  tier takes. Free instances **pause after 3 days idle and are deleted after
  30**. Neo4j Community (the work target) has no such cap.
- **The POC environment differs from the target.** Testing runs on managed Aura;
  work runs self-hosted Neo4j Community. Community has a single database (no
  isolation for the code graph), **no RBAC** (one credential, write access for
  anyone holding it — read-only cannot be enforced by the database, only by how
  the MCP server is configured), and offline-only backups. A clean POC here does
  not validate the work deployment's scale or access model.
- **Merged-graph ceiling.** Measured: 77.9 MB for 3,509 files = **21.7 KB per
  source file** (12–44 KB depending on the repo), not the ~11 KB originally
  estimated. The file loads in ~0.5 s and the graphify MCP server process sits
  at ~444 MB RSS with it loaded; graphify caps the graph file at 512 MiB. So the
  practical ceiling as one JSON file is about **10 repos of this size** before
  per-session memory binds, not the 10–20 first assumed; ~50 is not viable.
  Past that, either route queries per-repo via the MCP server's LRU context
  cache (`GRAPHIFY_MAX_CONTEXTS`, default 8) or move retrieval onto Neo4j — at
  which point the token-budgeting layer would need reimplementing on Cypher.
- **Credentials in transcript.** The Aura password for this instance was pasted
  into a Claude Code session and should be rotated when the POC ends.
- **`scip_ingest.py` is not usable.** It self-describes as a skeleton, "NOT a
  full SCIP protobuf implementation", and "not wired to the CLI in this phase".
  Higher-fidelity C#/Java indexing via SCIP is not available today.
- **graphify upstream bugs found.** (1) The global-graph writer, `affected` and
  `serve._load_graph` ignore the `_src`/`_tgt` direction markers that `to_json`
  honours; 39.2% of merged links were reversed. (2) `global_add` dedups
  sourceless nodes by label regardless of `external`, keeps the first repo's tag
  on the survivor, and rewires edges without updating their markers. `kgc`
  works around both after every build; neither is fixed upstream.

## Open questions

1. Does the Neo4j path earn its place in the POC at all, or would graphify's
   stdio MCP alone answer every question? Worth measuring, since a negative
   result simplifies the work build considerably. Neo4j stays in scope for the
   POC precisely so this can be answered with evidence — `results.md` tallies
   which server answered each question.
