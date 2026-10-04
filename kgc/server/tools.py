"""The nine tools as plain functions over a Backend.

No MCP types in here, so every tool is testable with a fake backend and a
plain assert on text. Each tool: resolve what the agent named (or not, and
say why), run one query, shape lines, cut to budget. KgcError from db.read
propagates; app.py turns it into the one-line text.
"""
from __future__ import annotations

import json
from collections import defaultdict

from kgc.server import queries as q
from kgc.server.format import (
    DEFAULT_MAX_TOKENS,
    cut_to_budget,
    edge_line,
    lucene_query,
    node_line,
)
from kgc.server.guard import REFUSED, is_write
from kgc.server.resolve import Resolution, resolve

TOOL_NAMES = ("search", "describe", "callers", "callees", "impact",
              "externals", "cross_repo", "stats", "cypher")

SCHEMA_NOTES = """Code graph schema (read-only).
Nodes: (:CodeNode {id, label, repo, external, source_file, source_location, file_type, community}).
  Sourced nodes have repo set and id "repo::local". Externals (stdlib / third-party /
  unresolved names) have external = true, no repo, source_file = '' and merge across repos.
  file_type is code | rationale (a docstring or comment) | concept (external stubs and
  unresolved names). Identify externals by external = true, never by file_type.
(:Repo {tag, path, pushed_at, built_at_commit, node_count, edge_count, skipped_extensions}) - one per repo.
Relationships (native types): calls, indirect_call, imports, imports_from, contains, method,
  inherits, references, uses, rationale_for.
Direction: caller -> callee, importer -> imported, container -> contained, class -> method,
  subclass -> superclass, docstring -[:rationale_for]-> code.
Real cross-repo coupling: edges where both ends are sourced -
  WHERE a.repo <> b.repo AND a.source_file <> '' AND b.source_file <> ''.
Full-text index kgc_text on (label, source_file): CALL db.index.fulltext.queryNodes('kgc_text', $q).
The graph is static structure: it cannot see dependency injection, registries or config-file wiring."""

DESCRIPTIONS = {
    "search": ("Find code by words: symbol names, file paths, or docstring text, across every "
               "indexed repo. Returns ranked nodes as 'repo  label  file:line  cCommunity'. "
               "Start here when you do not know the exact name."),
    "describe": ("One symbol in detail: its repo, file and line, docstring, community, and how many "
                 "edges of each type point in and out. Accepts a name or 'path/to/file.py::name'."),
    "callers": ("Who uses this symbol: incoming calls, imports, references and subclassing, across "
                "all repos, grouped by repo. depth > 1 follows the chain (max 4). Use for "
                "'what would break if I change X'."),
    "callees": ("What this symbol uses: outgoing calls, imports and references. depth > 1 follows "
                "the chain (max 4). Use for 'what does X actually invoke'."),
    "impact": ("Transitive dependents of one or more symbols and/or changed files, grouped by repo "
               "with counts and samples. Feed a git diff's file list straight in. depth max 4."),
    "externals": ("Third-party packages each repo imports. min_repos=2 or more returns only packages "
                  "shared by that many repos, with the repo list."),
    "cross_repo": ("Real coupling between two repos' own code: edges whose both ends are sourced "
                   "nodes in different repos, grouped by relationship type with samples. "
                   "Returns 'none' when there is none."),
    "stats": ("What is indexed: per repo, node and relationship counts, when it was pushed, the "
              "commit, and which file extensions were NOT parsed (the graph's blind spots). "
              "Flags a repo whose last push did not complete."),
    "cypher": ("Read-only Cypher escape hatch for questions the other tools do not cover; prefer "
               "the specific tools first. Writes are refused. Rows come back as a tab table. "
               + SCHEMA_NOTES),
}


# --- helpers ---------------------------------------------------------------------

def _unresolved(symbol: str, res: Resolution) -> str:
    if res.candidates:
        lines = [f"ambiguous: {symbol!r} matches {len(res.candidates)} nodes in different files; "
                 "retry as path::symbol or pass repo="]
        lines += [node_line(c.as_row()) for c in res.candidates]
        return "\n".join(lines)
    lines = [f"no node matches {symbol!r}"]
    if res.nearest:
        lines.append("nearest:")
        lines += [node_line(n.as_row()) for n in res.nearest]
    return "\n".join(lines)


def _in_repo(repo: str | None) -> str:
    return f" in repo {repo}" if repo else ""


def _loc(source_file, source_location) -> str:
    return f"{source_file or ''}:{source_location or ''}".rstrip(":")


# --- tools -----------------------------------------------------------------------

def search(db, query: str, repo: str | None = None, limit: int = 10,
           max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    lucene = lucene_query(query)
    if not lucene:
        return f"no searchable words in {query!r}"
    rows = db.read(q.SEARCH, q=lucene, repo=repo, limit=int(limit))
    if not rows:
        return f"no matches for {query!r}{_in_repo(repo)}"
    return cut_to_budget([node_line(r) for r in rows], max_tokens)


def describe(db, symbol: str, repo: str | None = None,
             max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    res = resolve(db, symbol, repo)
    if not res.ok:
        return _unresolved(symbol, res)
    rows = db.read(q.DESCRIBE_NODE, id=res.node.id)
    row = rows[0] if rows else dict(res.node.as_row(), file_type="code", docs=[])
    counts = db.read(q.EDGE_COUNTS, id=res.node.id)
    lines = [node_line(row), f"type: {row.get('file_type') or 'code'}"]
    lines += [f"doc: {d}" for d in (row.get("docs") or []) if d]
    for direction, prefix in (("in", "in:  "), ("out", "out: ")):
        parts = [f"{c['rel']} {c['n']}" for c in counts if c.get("direction") == direction]
        lines.append(prefix + (", ".join(parts) or "none"))
    return cut_to_budget(lines, max_tokens)


def _neighbours(db, direction: str, rels, word: str, symbol: str, repo: str | None,
                depth: int, max_tokens: int) -> str:
    # The symbol resolves within `repo` first, so passing repo= is a genuine
    # retry (not the same candidate list again); if nothing matches in that
    # repo, fall back to a global resolution rather than reporting candidates
    # a repo filter could never have narrowed. `repo` still narrows the
    # neighbour query's *results* either way.
    res = resolve(db, symbol, repo)
    if repo is not None and not res.ok and not res.candidates:
        res = resolve(db, symbol, None)
    if not res.ok:
        return _unresolved(symbol, res)
    rows = db.read(q.neighbours(direction, rels, depth), id=res.node.id, repo=repo,
                   limit=q.DEFAULT_LIMIT)
    if not rows:
        return f"no {word} of {res.node.label} within depth {q.clamp_depth(depth)}{_in_repo(repo)}"
    target = res.node.as_row()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups["external" if r.get("external") else (r.get("repo") or "-")].append(r)
    lines = [f"{word} of {node_line(target)}:"]
    for name in sorted(groups):
        lines.append(f"[{name}] {len(groups[name])}")
        for r in groups[name]:
            src, tgt = (r, target) if direction == "in" else (target, r)
            line = edge_line(src, r.get("via") or "?", tgt, r.get("via_location"))
            if (r.get("hops") or 1) > 1:
                line += f"  hops={r['hops']}"
            lines.append(line)
    return cut_to_budget(lines, max_tokens)


def callers(db, symbol: str, repo: str | None = None, depth: int = 1,
            max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    return _neighbours(db, "in", q.DEPENDENCY_RELS, "callers", symbol, repo, depth, max_tokens)


def callees(db, symbol: str, repo: str | None = None, depth: int = 1,
            max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    return _neighbours(db, "out", q.CALLEE_RELS, "callees", symbol, repo, depth, max_tokens)


def impact(db, symbols: list[str] | None = None, files: list[str] | None = None,
           depth: int = 2, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    ids: list[str] = []
    problems: list[str] = []
    for symbol in symbols or []:
        res = resolve(db, symbol)
        if res.ok:
            ids.append(res.node.id)
        else:
            problems.append(_unresolved(symbol, res))
    files = [f.strip() for f in (files or []) if f and f.strip()]
    if not ids and not files:
        return cut_to_budget(problems, max_tokens) if problems else "give at least one symbol or file"
    rows = db.read(q.impact(depth), ids=ids, files=files)
    lines = list(problems)
    if not rows:
        named = ", ".join([*(symbols or []), *files])
        lines.append(f"nothing depends on {named} within depth {q.clamp_depth(depth)}")
    for r in rows:
        lines.append(f"[{r['repo']}] {r['n']} dependents")
        for s in r.get("sample") or []:
            lines.append(f"  {s.get('label')}  {_loc(s.get('source_file'), s.get('source_location'))}"
                         f"  hops={s.get('hops')}")
    return cut_to_budget(lines, max_tokens)


def externals(db, repo: str | None = None, min_repos: int = 1,
              max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    rows = db.read(q.EXTERNALS, repo=repo, min_repos=max(1, int(min_repos)),
                   limit=q.DEFAULT_LIMIT * 4)
    if not rows:
        return "none"
    lines = [f"{r['package']}  repos: {', '.join(sorted(r['repos']))}" for r in rows]
    return cut_to_budget(lines, max_tokens)


def cross_repo(db, repo_a: str, repo_b: str | None = None,
               max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    rows = db.read(q.CROSS_REPO, a=repo_a, b=repo_b)
    if not rows:
        return "none"
    lines: list[str] = []
    for r in rows:
        lines.append(f"{r['from_repo']} -{r['rel']}-> {r['to_repo']}: {r['n']}")
        for s in r.get("sample") or []:
            lines.append(f"  {s.get('from')} -> {s.get('to')}"
                         f"  ({_loc(s.get('source_file'), s.get('source_location'))})")
    return cut_to_budget(lines, max_tokens)


def _skipped(raw) -> str:
    if raw in (None, "", "null"):
        return "not recorded"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "not recorded"
    if not data:
        return "none"
    return ", ".join(f"{ext} ({n})" for ext, n in sorted(data.items(), key=lambda kv: (-kv[1], kv[0])))


def stats(db, repo: str | None = None) -> str:
    meta = {m["tag"]: m for m in db.read(q.REPO_META)}
    nodes = db.read(q.NODE_COUNTS, repo=repo)
    if repo and not nodes:
        return f"no repo {repo!r} indexed"
    rels = db.read(q.REL_COUNTS, repo=repo)
    lines: list[str] = []
    for n in nodes:
        tag = n["repo"]
        m = meta.get(tag)
        if m is None:
            lines.append(f"[{tag}] {n['n']} nodes  last push incomplete")
        else:
            lines.append(f"[{tag}] {n['n']} nodes, {m.get('edge_count')} edges, "
                         f"pushed {m.get('pushed_at')}, commit {m.get('built_at_commit') or 'unknown'}")
            lines.append(f"  skipped extensions: {_skipped(m.get('skipped_extensions'))}")
        parts = [f"{r['rel']} {r['n']}" for r in rels if r.get("repo") == tag]
        lines.append("  relationships: " + (", ".join(parts) or "none"))
    if not repo:
        ext = db.read(q.EXTERNAL_COUNT)
        lines.append(f"externals: {ext[0]['n'] if ext else 0}")
    return "\n".join(lines)


def _cell(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return "" if value is None else str(value)


def cypher(db, query: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    if is_write(query):
        return REFUSED
    rows = db.read(query)[:200]
    if not rows:
        return "no rows"
    keys = list(rows[0].keys())
    lines = ["\t".join(keys)] + ["\t".join(_cell(r.get(k)) for k in keys) for r in rows]
    return cut_to_budget(lines, max_tokens, hint="add LIMIT or RETURN fewer columns")
