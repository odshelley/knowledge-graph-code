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

Parsed by `verify/run_arm.py`. One line per question, optionally followed by
one or more indented sub-bullets:

```
N. **class** [repo] question text
   - _Grader note:_ grounding, the verified answer, and/or the grading rule.
```

The numbered line is parsed as the literal `-p` prompt sent to `claude` for
**both** arms — it must contain only what a real user would ask, nothing
about what answer to expect or how to grade it. `run_arm.py`'s
`_QUESTION_RE` matches only lines of the exact form above; an indented
sub-bullet never matches, so the grader note is never seen by the model
being tested. It exists for the human judging correctness in `results.md`
step 3 — do not delete it, and do not let its content leak back onto the
numbered line above it.

`class` is one of `cross-repo`, `single-repo`, `hidden-dependency`,
`blind-spot`. `repo` is the tag (matching `repos.yaml`) the **control** arm
uses as its working directory. For a `cross-repo` or `blind-spot` question,
`run_arm.py` also prepends the on-disk path of all five repos to the
**control** prompt only (never treatment's — `CLAUDE.md` already covers
that for treatment), because those two classes genuinely require knowing
sibling repos exist; without the preamble they would be impossible for
control for a reason that has nothing to do with the graph. `single-repo`
and `hidden-dependency` questions get no preamble on either arm, because
they only ever need the one repo named in `[repo]` — adding the other four
repos' paths there would be a token tax on control with no question to
justify it, which would tilt the ratio toward treatment for free. Control is
meant to be handicapped by not having a merged index, not by cwd alone and
not by an unfair prompt.

## Cross-repo / impact (5)

Import-family edges (graphify #2873) stub external modules by name, shared
globally across repos; a *call* target is deliberately not stubbed (#3156).
So "which repos import X" is answerable from the merged graph; "which repos
call external function Y" is not, by design — none of the five below ask it.

1. **cross-repo** [knowledge-retrieval] Which of the five repos import `dagster`?
   - _Grader note:_ verified by grep (excluding `.claude/worktrees/`
     duplicates): only `knowledge-retrieval` imports `dagster`. None of
     graphify, neural_particle_method, diffusion-bridge, or claude do.
2. **cross-repo** [knowledge-retrieval] Which repos import `pydantic`, and which of those also import `neo4j`?
   - _Grader note:_ verified by grep: `pydantic` is imported by
     `knowledge-retrieval` and `graphify` only; the `neo4j` Python driver is
     imported by the same two repos only. The intersection is both repos —
     a good answer reports that rather than assuming the two sets differ.
3. **cross-repo** [graphify] If `graphify`'s `build_from_json` (`graphify/graphify/build.py`) changed its return type, which callers in any of the other four repos would be affected?
   - _Grader note:_ verified: no repo other than `graphify` imports
     `graphify` as a library at all — no `import graphify`/`from graphify`
     anywhere in knowledge-retrieval, neural_particle_method,
     diffusion-bridge, or claude, and none declare it as a dependency. The
     answer is zero external callers; only call sites inside `graphify`
     itself (it has many) are affected.
4. **cross-repo** [graphify] Which external (third-party) packages are imported by three or more of the five repos?
   - _Grader note:_ verified by counting actual `import numpy`/`from numpy`
     occurrences: `numpy` is imported in graphify (2 files),
     neural_particle_method (102 files), and diffusion-bridge (14 files) —
     three repos. knowledge-retrieval and claude do not import it. (`pytest`
     is a *declared dev dependency* of all four Python repos, but that is
     not a traced import/call edge, so a graph built from import edges would
     not surface it the same way — worth noting if an answer raises it.)
5. **cross-repo** [knowledge-retrieval] `knowledge-retrieval` and `graphify` both build an in-memory graph from extraction JSON. Where does each one do it, and do they share any code path?
   - _Grader note:_ verified: `graphify`'s entry point is `build_from_json`
     in `graphify/graphify/build.py`; `knowledge-retrieval` never calls it
     (see Q3's note) and builds its own graph separately through its own
     `pipeline/` assets. They do not share a code path.

## Single-repo structure, grep-favourable (3)

These are "how does X work" questions. A structural graph can point at the
node; it cannot explain the *why* behind an implementation choice the way the
surrounding code comments can. Expected treatment losses here are
informative, not failures — say so when recording the verdict.

6. **single-repo** [diffusion-bridge] Where is the bridge sampling step implemented in `diffusion-bridge`?
   - _Grader note:_ grounded — `src/bridge_diffusion/sampling/` (`sampler.py`,
     `io.py`) is the relevant package; `models/bridge.py` also participates.
     Judge on whether the right module is found, not on an exact file list.
7. **single-repo** [neural-particle-method] What are the top-level modules under `src/neural_particle_method` and how do they relate?
   - _Grader note:_ grounded — top-level packages include `bench`,
     `calibrate`, `cli.py`, `estimators`, `experiments`, `figures`, `market`,
     `pricing`, `reference`, `simulate`, `suite`, `tracking`. Judge on
     whether the described relationships are plausible and specific, not on
     an exact module list.
8. **single-repo** [graphify] What does `prefix_graph_for_global` (`graphify/graphify/build.py`) do to external stub nodes, and why?
   - _Grader note:_ grounded — it prefixes every non-external node's id with
     `{repo_tag}::`, but deliberately leaves external (stdlib/third-party)
     stub nodes unprefixed so the same module unifies into one node across
     repos; that is what makes "which repos depend on X" answerable at all.
     A good answer should say external nodes are left alone and roughly why.

## Hidden-dependency probes (2) — coupling a static call graph cannot see

An import/call graph only has edges where the extractor could name a
*concrete* target at parse time. Dependency injection defeats that: the
callee arrives as a parameter, not a module-level reference, so there is
nothing for a static extractor to point at. Both probes below are real
production code, not contrived examples. The graph-only answer must come back
**absent or hedged, never confidently wrong** — a stale or incomplete graph
fails by being plausible, and these two questions exist to catch exactly that
failure mode.

9. **hidden-dependency** [knowledge-retrieval] In `knowledge-retrieval`, what does `push_one` (`pipeline/zotero/push.py`) actually call on its `client` argument?
   - _Grader note:_ `client` is an untyped, injected parameter with no
     import tying it to a concrete class, so no static call-graph edge from
     `push_one` to a concrete method can exist. A good answer is absent or
     hedged ("the graph has no edge here because `client`'s type is never
     declared statically"). A specific, confident claim about what `client`
     concretely is — from either arm — is a failure on this question,
     because it is exactly the kind of plausible-looking wrong edge a stale
     or DI-blind graph produces.
10. **hidden-dependency** [knowledge-retrieval] In `knowledge-retrieval`, when `resolve_concepts` (`pipeline/resolution/resolver.py`) resolves an ambiguous concept match and calls `adjudicate`, what does that actually invoke, and does it touch the network?
    - _Grader note:_ `adjudicate` (like `lookup_by_key`, `nearest`,
      `similarity_to`) arrives as an injected callable parameter — the
      function body only ever calls the parameter, never a module-level
      name, so no static edge exists from `resolver.py` alone. The one real
      caller, `pipeline/assets/resolved_entities.py`, binds `adjudicate` to
      a same-named module function via a lambda closure that wraps a live
      LLM call — but that binding lives in the caller, not in
      `resolve_concepts`/`_resolve_one` itself. A good answer from the graph
      alone is absent or hedged; only reading the caller reveals the LLM
      call. A specific, confident wrong claim here is a failure for
      whichever arm makes it.

## Blind-spot (2) — the graph should say "I cannot see this," not guess

11. **blind-spot** [diffusion-bridge] What does the kdb+/q code in any of the five repos do?
    - _Grader note:_ verified by direct filesystem search while writing
      this file: there are zero `.q`/`.k` files anywhere in
      knowledge-retrieval, graphify, neural_particle_method,
      diffusion-bridge, or claude, and `graphify` has no `.q` extractor
      regardless. A good answer has two parts: in fact there is no kdb+/q
      code here, **and** a graph with zero matching nodes does not by
      itself prove that — it only proves the extractor doesn't look. Grep
      against the real filesystem does prove it. An answer that treats
      "zero matching nodes" as settling the question, without naming that
      limitation, is a failure for whichever arm gives it.
12. **blind-spot** [claude] Does the TypeScript `claude` repo's MCP client / tool-calling code share any functions, types, or call edges with `graphify`'s Python MCP server (`graphify/serve.py`) — the one this very POC wires together via `.mcp.json`?
    - _Grader note:_ the two languages are connected only by the MCP
      JSON-RPC protocol over stdio at runtime; no import or call edge
      between a TypeScript file and a Python file can exist in a
      per-language AST-based graph. A good answer says there is no shared
      code and that the coupling is a wire protocol this kind of graph
      cannot represent — not silence, and not a fabricated edge.

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
