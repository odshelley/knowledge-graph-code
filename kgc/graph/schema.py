"""Constraints and indexes the server depends on.

Idempotent, so push runs it every time. The only non-obvious part is the
legacy index: the first POC created an unnamed range index on CodeNode(id),
and Neo4j refuses to create a uniqueness constraint while an equivalent index
exists, so that index has to go first. A constraint's own backing index also
appears in SHOW INDEXES - with owningConstraint set - and must never be dropped.
"""
from __future__ import annotations

import re

FULLTEXT_INDEX = "kgc_text"

SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT codenode_id IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT repo_tag IF NOT EXISTS FOR (r:Repo) REQUIRE r.tag IS UNIQUE",
    "CREATE INDEX codenode_repo IF NOT EXISTS FOR (n:CodeNode) ON (n.repo)",
    "CREATE INDEX codenode_label IF NOT EXISTS FOR (n:CodeNode) ON (n.label)",
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    "FOR (n:CodeNode) ON EACH [n.label, n.source_file]",
)

LEGACY_ID_INDEXES = """
SHOW INDEXES YIELD name, type, labelsOrTypes, properties, owningConstraint
WHERE type = 'RANGE' AND labelsOrTypes = ['CodeNode'] AND properties = ['id']
  AND owningConstraint IS NULL
RETURN name
"""

_INDEX_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def drop_legacy_id_indexes(session) -> list[str]:
    names = [record["name"] for record in session.run(LEGACY_ID_INDEXES)]
    dropped: list[str] = []
    for name in names:
        if not _INDEX_NAME_RE.match(name):
            raise ValueError(f"refusing to interpolate index name {name!r}")
        session.run(f"DROP INDEX {name} IF EXISTS").consume()
        dropped.append(name)
    return dropped


def ensure_schema(session) -> list[str]:
    """Drop the legacy id index, then create every constraint and index.

    Returns a human-readable log, one line per action taken.
    """
    log = [f"dropped legacy index {name}" for name in drop_legacy_id_indexes(session)]
    for statement in SCHEMA_STATEMENTS:
        session.run(statement).consume()
    log.append(f"ensured {len(SCHEMA_STATEMENTS)} constraints and indexes")
    return log
