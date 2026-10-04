"""A scripted stand-in for kgc.server.db.Backend. Tests pick responses by
matching on the query text, so each tool's tests read as 'given these rows
from these queries, the text is ...'."""
from __future__ import annotations


class FakeBackend:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def read(self, query: str, **params) -> list[dict]:
        self.calls.append((query, params))
        for matcher, rows in self.responses:
            hit = matcher(query, params) if callable(matcher) else (matcher in query)
            if hit:
                return list(rows(params)) if callable(rows) else list(rows)
        return []

    def queries(self) -> list[str]:
        return [q for q, _ in self.calls]


def node_row(id, label, repo="alpha", source_file="a.py", source_location="L1",
             community=0, external=False, **extra):
    row = {"id": id, "label": label, "repo": repo, "source_file": source_file,
           "source_location": source_location, "community": community, "external": external}
    row.update(extra)
    return row
