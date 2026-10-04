# Verification questions

Twelve questions spanning the five repos (`knowledge-retrieval`, `graphify`,
`neural-particle-method`, `diffusion-bridge`, `claude`), weighted toward what
deterministic code graphs are actually good at. An external audit of the
graph-tool literature found that graphs reliably beat grep on **transitive
impact**, **cross-repo / hidden dependencies**, and **"where does this live"
at scale** — and reliably *lose* to grep on **"how does X work"** questions,
where reading the actual implementation beats querying a structural
abstraction of it. The weighting below (5 cross-repo/impact, 3 single-repo
structure, 2 hidden-dependency probes, 2 blind-spot) reflects that: it is not
an attempt to make the graph look good, it is an attempt to ask the graph
mostly the kind of question it exists to answer, plus enough of the kind it
doesn't, to keep the result honest.

Each question is run **at least twice per arm** (`--runs 2`, 3 recommended)
in **separate Claude Code sessions** so context never leaks between runs:

- **Control:** MCP servers disabled (`--strict-mcp-config` + an empty
  `--mcp-config`); the agent reads files normally, cwd'd into the question's
  primary repo.
- **Treatment:** cwd'd into this project, so `.mcp.json` and `CLAUDE.md` load
  and the agent has the `graphify` and `neo4j` MCP servers available.

Record tokens, cost, wall time, and a correctness verdict judged against the
code for every run. A wrong answer in **any** run counts against that arm —
see "Why replicate" below.

## Format

Parsed by `verify/run_arm.py`. One line per question:

```
N. **class** [repo] question text
```

`class` is one of `cross-repo`, `single-repo`, `hidden-dependency`,
`blind-spot`. `repo` is the tag (matching `repos.yaml`) the **control** arm
uses as its working directory. For a cross-repo question this is just the
most central repo — `run_arm.py` prepends the on-disk path of all five repos
to every control prompt (see "Scoping note" below), so control is not
handicapped relative to treatment by cwd alone; it is handicapped by not
having a merged index, which is the thing under test.

## Cross-repo / impact (5)

Import-family edges (graphify #2873) stub external modules by name, shared
globally across repos; a *call* target is deliberately not stubbed (#3156).
So "which repos import X" is answerable from the merged graph; "which repos
call external function Y" is not, by design — none of the five below ask it.

1. **cross-repo** [knowledge-retrieval] Which of the five repos import `dagster`?
2. **cross-repo** [knowledge-retrieval] Which repos import `pydantic`, and which of those also import `neo4j`?
3. **cross-repo** [graphify] `graphify`'s `build_from_json` (`graphify/graphify/build.py`) is called from many places inside `graphify` itself. If its return type changed, which callers in any of the *other four* repos would be affected?
4. **cross-repo** [graphify] Which external (third-party) packages are imported by three or more of the five repos?
5. **cross-repo** [knowledge-retrieval] `knowledge-retrieval` and `graphify` both build an in-memory graph from extraction JSON. Where does each one do it, and do they share any code path?

## Single-repo structure, grep-favourable (3)

These are "how does X work" questions. A structural graph can point at the
node; it cannot explain the *why* behind an implementation choice the way the
surrounding code comments can. Expected treatment losses here are
informative, not failures — say so when recording the verdict.

6. **single-repo** [diffusion-bridge] Where is the bridge sampling step implemented in `diffusion-bridge`?
7. **single-repo** [neural-particle-method] What are the top-level modules under `src/neural_particle_method` and how do they relate?
8. **single-repo** [graphify] What does `prefix_graph_for_global` (`graphify/graphify/build.py`) do to external stub nodes, and why?

## Hidden-dependency probes (2) — coupling a static call graph cannot see

An import/call graph only has edges where the extractor could name a
*concrete* target at parse time. Dependency injection defeats that: the
callee arrives as a parameter, not a module-level reference, so there is
nothing for a static extractor to point at. Both probes below are real
production code, not contrived examples. The graph-only answer must come back
**absent or hedged, never confidently wrong** — a stale or incomplete graph
fails by being plausible, and these two questions exist to catch exactly that
failure mode.

9. **hidden-dependency** [knowledge-retrieval] `push_one(client, collection_key, record, authors, ...)` (`pipeline/zotero/push.py`) takes an untyped, injected `client` with no import tying it to a concrete class. What does `push_one` actually call on `client`?
10. **hidden-dependency** [knowledge-retrieval] `resolve_concepts` / `_resolve_one` (`pipeline/resolution/resolver.py`) take `lookup_by_key`, `nearest`, `similarity_to`, and `adjudicate` as injected callables — the function body only ever calls the *parameter*, never a module-level name. The caller (`pipeline/assets/resolved_entities.py`) happens to bind `adjudicate` to a same-named module function via a lambda closure, wrapping a live LLM call. When `_resolve_one` hits the ambiguous-similarity band and calls `adjudicate`, does that touch the network? What is it actually bound to, and can a static call graph see that binding?

## Blind-spot (2) — the graph should say "I cannot see this," not guess

11. **blind-spot** [diffusion-bridge] What does the kdb+/q code in any of the five repos do? (Verified by direct filesystem search while writing this file: there are zero `.q`/`.k` files in any of knowledge-retrieval, graphify, neural_particle_method, diffusion-bridge, or claude. No `.q` extractor exists in `graphify` either way. The correct answer has two parts: *in fact* there is no kdb+/q code here, **and** a graph with zero kdb+/q nodes does not, by itself, prove that — it proves the extractor doesn't look. Grep against the real filesystem does prove it. A treatment answer that cites "zero nodes of that kind" as if it settled the question, without naming that limitation, counts as a confidently-wrong answer, not a correct one.)
12. **blind-spot** [claude] Does the TypeScript `claude` repo's MCP client / tool-calling code share any functions, types, or call edges with `graphify`'s Python MCP server (`graphify/serve.py`) — the one this very POC wires together via `.mcp.json`? (The two languages are connected only by the MCP JSON-RPC protocol over stdio at runtime; no import or call edge between a TypeScript file and a Python file can exist in a per-language AST-based graph. The correct answer is "no shared code — the coupling is a wire protocol, which this kind of graph cannot represent," not silence and not a fabricated edge.)

## Why replicate

A single pass per question per arm cannot separate signal from noise: Claude
models flip their answer to the *same* prompt at temperature 0 roughly 9% of
the time per instance (arXiv 2607.09691). With one run per arm, a flip looks
identical to a real difference between control and treatment. Running each
question **at least twice** per arm (3 recommended) and reporting the median
*and* the spread is the minimum needed to say "treatment is cheaper" means
something other than "this one sample happened to be cheaper." A wrong answer
on any single run still counts against that arm for that question — more
runs make it *harder* to get a clean accept, not easier, which is the correct
direction for a POC gate. Even at 2–3 runs this is still a thin sample for a
~9%-flip-rate phenomenon; treat the verdict as indicative, not proof, and say
so in the results.

## The structural blind spot, stated plainly

An import/call graph — the kind this POC builds — is structurally blind to
**dependency injection, registries, and configuration-driven wiring**: any
coupling that is decided at runtime by a parameter, a config file, a
`getattr` on a dynamic key, or a framework's own container, rather than by a
name a parser can resolve at parse time. That is exactly the dominant coupling
style in the kind of enterprise (e.g. Spring-style) codebase this POC is
meant to inform a decision about at work. Questions 9 and 10 above exist to
demonstrate that the graph *knows* it is blind there, rather than silently
filling the gap with a plausible-looking wrong edge. Building a
framework-aware extractor (one that resolves DI containers, Spring
annotations, or config-driven registries into real edges) is out of scope for
this POC and is named here as the gap, not solved.
