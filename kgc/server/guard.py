"""Belt for the `cypher` tool's braces.

The read transaction every query runs in is the real guard: Neo4j rejects a
write inside it server-side. This file refuses the obvious cases before they
are sent, so the agent gets `refused: read-only server` instead of a driver
error, and so a future change to the transaction mode cannot silently open a
write path. String literals are blanked first so `WHERE n.label = 'set'` is
not refused; backslash-escaped quotes inside a literal are handled.
"""
from __future__ import annotations

import re

REFUSED = "refused: read-only server"

_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b"
    r"|\bLOAD\s+CSV\b"
    r"|\bCALL\s*\{",
    re.IGNORECASE,
)


def strip_literals(query: str) -> str:
    return _LITERAL_RE.sub("''", query)


def is_write(query: str) -> bool:
    return _WRITE_RE.search(strip_literals(query)) is not None
