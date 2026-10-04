"""Turn what the agent typed into one node, or say precisely why not.

Tiers, first non-empty wins (adapted from graphify's _find_node_tiers):
  path-scoped `file.py::name`  or  bare path -> file node
  exact label (with or without the trailing `()` callables carry)
  case-insensitive label
  full-text, accepted only with a clear winner (top score >= 2x the runner-up)
More than one hit across different files is ambiguity, reported as candidates
- one per file - never a guess. Several hits in one file are ordinary
precedence (a file node plus its members): the first wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from kgc.server import queries as q
from kgc.server.format import lucene_query

_PATHLIKE = re.compile(r"/|\.[A-Za-z0-9]{1,5}$")


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    repo: str | None
    source_file: str | None
    source_location: str | None
    community: int | None
    external: bool

    @classmethod
    def from_row(cls, row: dict) -> "Node":
        return cls(
            id=row["id"], label=row.get("label") or "", repo=row.get("repo"),
            source_file=row.get("source_file"), source_location=row.get("source_location"),
            community=row.get("community"), external=bool(row.get("external")),
        )

    def as_row(self) -> dict:
        return {"id": self.id, "label": self.label, "repo": self.repo,
                "source_file": self.source_file, "source_location": self.source_location,
                "community": self.community, "external": self.external}


@dataclass(frozen=True)
class Resolution:
    node: Node | None = None
    candidates: tuple[Node, ...] = ()
    nearest: tuple[Node, ...] = ()

    @property
    def ok(self) -> bool:
        return self.node is not None


def _decide(rows: list[dict]) -> Resolution:
    nodes = [Node.from_row(r) for r in rows]
    per_file: dict[str, Node] = {}
    for node in nodes:
        per_file.setdefault(node.source_file or "", node)
    if len(per_file) > 1:
        return Resolution(candidates=tuple(per_file.values()))
    return Resolution(node=nodes[0])


def resolve(db, symbol: str, repo: str | None = None) -> Resolution:
    symbol = symbol.strip()
    tiers: list[tuple[str, dict]] = []
    if "::" in symbol:
        path, _, name = symbol.partition("::")
        tiers.append((q.PATH_SCOPED, {"path": path.strip(), "label": name.strip()}))
    elif _PATHLIKE.search(symbol):
        tiers.append((q.FILE_NODE, {"path": symbol}))
    tiers.append((q.EXACT_LABEL, {"label": symbol}))
    tiers.append((q.CI_LABEL, {"label": symbol}))

    for query, params in tiers:
        rows = db.read(query, repo=repo, limit=q.DEFAULT_LIMIT, **params)
        if rows:
            return _decide(rows)

    lucene = lucene_query(symbol)
    if not lucene:
        return Resolution()
    hits = db.read(q.SEARCH, q=lucene, repo=repo, limit=5)
    if not hits:
        return Resolution()
    if len(hits) == 1 or hits[0]["score"] >= 2 * hits[1]["score"]:
        return Resolution(node=Node.from_row(hits[0]))
    return Resolution(nearest=tuple(Node.from_row(h) for h in hits[:3]))
