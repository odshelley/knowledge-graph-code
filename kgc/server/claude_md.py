"""The CLAUDE.md block that nudges Claude Code to reach for the graph.

One source of truth: `kgc init` prints or appends it, the project's own
CLAUDE.md is exactly this text, and the harness writes it into the kgc arm's
working directory.
"""
from __future__ import annotations

MARKER = "# kgc code graph"

_TEMPLATE = """{marker}

One MCP server, `kgc`, exposes a code graph of these repos: {repos}.

For any question about how code is structured, what calls or imports what, what depends on what, or where something lives, use the kgc tools BEFORE reading files or grepping:

- `search` — find a symbol, file or concept by words
- `describe` — one symbol: file, docstring, community, edge counts
- `callers` / `callees` — who uses it / what it uses, across repos
- `impact` — transitive dependents of symbols or changed files, grouped by repo
- `externals` — third-party packages per repo; `min_repos=2` for shared ones
- `cross_repo` — real coupling between two repos' own code
- `stats` — what is indexed, when, and which file types were NOT parsed
- `cypher` — read-only Cypher when nothing above fits (schema is in its description)

The graph is static code structure. It cannot see dependency injection, registries or config-file wiring; when it says a symbol has no callers, confirm with grep. The server is read-only; `kgc build` then `kgc push` refresh the graph.
"""


def render_claude_md(tags: list[str]) -> str:
    repos = ", ".join(f"`{t}`" for t in tags) if tags else "(none pushed yet)"
    return _TEMPLATE.format(marker=MARKER, repos=repos)
