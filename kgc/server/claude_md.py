"""The CLAUDE.md blocks that tell Claude Code about the graph.

Two blocks, deliberately separate:

`render_claude_md` is the machine-agnostic one. `kgc init` prints or appends
it, the project's own CLAUDE.md is exactly this text, and the harness writes
it into the kgc arm's working directory. It must never contain an absolute
path, or those three uses diverge per machine.

`render_local_checkouts` is the machine-specific one: where this box has each
repo cloned, so the agent can open the file the graph points at. It goes in
CLAUDE.local.md, which is gitignored, because the graph is shared and the
paths are not. At work, CI builds the graph on a path nobody queries from;
this file is how a laptop says where its own copies live.
"""
from __future__ import annotations

MARKER = "# kgc code graph"
LOCAL_MARKER = "# kgc local checkouts"

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


_LOCAL_TEMPLATE = """{marker}

The graph answers in repo-relative paths. These repos are checked out on this machine:

{rows}

To open a hit, join the repo's path above to the repo-relative path the graph gave you: a hit of `some/dir/file.py:L61` in `{example_tag}` is `{example_path}/some/dir/file.py` at line 61.

Use the graph to find WHERE something is and WHAT connects to it, then read that file for what the code actually does: the graph stores names, locations and truncated docstrings, never function bodies. A repo the graph names that is absent from the list above is indexed but not cloned here; `repos.yaml` says where it was built from.
"""


def render_local_checkouts(paths: dict[str, str]) -> str:
    """The CLAUDE.local.md block mapping each repo tag to its path on THIS machine.

    `paths` is {tag: absolute path}, already filtered to checkouts that exist.
    """
    if not paths:
        return (
            f"{LOCAL_MARKER}\n\n"
            "No repo in `repos.yaml` is checked out at its recorded path on this "
            "machine, so graph results cannot be opened locally. Fix the paths in "
            "`repos.yaml` and re-run `kgc init`.\n"
        )
    rows = "\n".join(f"- `{tag}` — `{paths[tag]}`" for tag in sorted(paths))
    example_tag = sorted(paths)[0]
    return _LOCAL_TEMPLATE.format(
        marker=LOCAL_MARKER, rows=rows,
        example_tag=example_tag, example_path=paths[example_tag],
    )
