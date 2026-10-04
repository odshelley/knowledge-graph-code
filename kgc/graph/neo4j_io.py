"""Neo4j access: connection, diagnostics, scoped reap, batched push.

Every error path here scrubs the password before it can reach a log, because
driver exceptions routinely include the connection string.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from neo4j import GraphDatabase

from kgc.config import Settings
from kgc.graph.schema import ensure_schema

COUNT_NODES = "MATCH (n) RETURN count(n) AS nodes"
COUNT_RELS = "MATCH ()-[r]->() RETURN count(r) AS rels"
SERVER_VERSION = (
    "CALL dbms.components() YIELD versions "
    "RETURN head(versions) AS version LIMIT 1"
)


@dataclass(frozen=True)
class DoctorReport:
    reachable: bool
    database: str
    server_version: str
    node_count: int
    relationship_count: int
    message: str


def _scrub(text: str, password: str) -> str:
    """Remove the password from any string before it is shown or logged."""
    if password and password in text:
        return text.replace(password, "***")
    return text


def connect(settings: Settings):
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
        connection_timeout=10.0,
    )


def resolve_database(driver, configured: str) -> str:
    """Return the database name that actually accepts a query.

    Aura supplies NEO4J_DATABASE set to the instance id, but the real database
    is often named 'neo4j'. Try the configured name, then 'neo4j'.
    """
    candidates = [c for c in (configured, "neo4j") if c]
    errors: list[str] = []
    for name in candidates:
        try:
            with driver.session(database=name) as session:
                session.run(COUNT_NODES).single()
            return name
        except Exception as exc:  # noqa: BLE001 - probing on purpose
            errors.append(f"{name}: {type(exc).__name__}")
    raise RuntimeError(
        f"neither database name worked ({'; '.join(errors)})"
    )


def doctor(settings: Settings, driver=None) -> DoctorReport:
    own_driver = driver is None
    try:
        if driver is None:
            driver = connect(settings)
        database = resolve_database(driver, settings.neo4j_database)
        with driver.session(database=database) as session:
            nodes = session.run(COUNT_NODES).single()["nodes"]
            rels = session.run(COUNT_RELS).single()["rels"]
            try:
                version = session.run(SERVER_VERSION).single()["version"]
            except Exception:  # noqa: BLE001 - Aura may restrict dbms procs
                version = "unknown"
        return DoctorReport(
            reachable=True,
            database=database,
            server_version=str(version),
            node_count=int(nodes),
            relationship_count=int(rels),
            message=f"connected to {database!r} ({nodes} nodes, {rels} rels)",
        )
    except Exception as exc:  # noqa: BLE001 - doctor never raises
        return DoctorReport(
            reachable=False,
            database="",
            server_version="",
            node_count=0,
            relationship_count=0,
            message=_scrub(f"{type(exc).__name__}: {exc}", settings.neo4j_password),
        )
    finally:
        if own_driver and driver is not None:
            driver.close()


# --- scoped reap and batched push (Task 5) ---

# AuraDB Free: Neo4j support docs say 200k nodes / 400k relationships; the
# pricing page says 50k / 175k. Use the lower pair so we fail early rather
# than half-pushing.
AURA_FREE_NODE_LIMIT = 50_000
AURA_FREE_REL_LIMIT = 175_000

# The only destructive queries in the project. Both are deliberately narrow.
# REAP_REPO is scoped on the 'repo' property that graphify's
# prefix_graph_for_global sets on every repo-local node and on no external
# node, so a tag matching nothing deletes nothing. Both match on :CodeNode so
# the :CodeNode(repo) index below is usable; an unlabelled MATCH (n) is not.
REAP_REPO = """
MATCH (n:CodeNode) WHERE n.repo = $tag
DETACH DELETE n
"""

# External stubs (stdlib / third-party) are shared across repos and carry no
# 'repo' property, so REAP_REPO never touches them. They orphan when the last
# repo referencing them goes away; this sweeps those and only those.
REAP_ORPHAN_EXTERNALS = """
MATCH (n:CodeNode) WHERE n.external = true AND NOT (n)--()
DELETE n
"""

# One Repo node per pushed repo, written LAST so its presence proves the push
# completed. The reap deletes it first for the same reason: a push that dies
# half-way leaves tagged nodes with no Repo node, which `stats` reports as
# "last push incomplete".
REAP_REPO_META = "MATCH (r:Repo {tag: $tag}) DELETE r"

REPO_UPSERT = """
MERGE (r:Repo {tag: $tag})
SET r.path = $path,
    r.pushed_at = $pushed_at,
    r.built_at_commit = $built_at_commit,
    r.node_count = $node_count,
    r.edge_count = $edge_count,
    r.skipped_extensions = $skipped_extensions
"""


def _repo_meta(graph, tag: str, path: str) -> dict:
    tag_nodes = {nid for nid, d in graph.nodes(data=True) if d.get("repo") == tag}
    return {
        "tag": tag,
        "path": path,
        "pushed_at": datetime.now(timezone.utc).isoformat(),
        # The merged global graph carries no per-repo commit; Plan 2's
        # per-repo artefact does.
        "built_at_commit": None,
        "node_count": len(tag_nodes),
        "edge_count": sum(1 for u, v in graph.edges() if u in tag_nodes or v in tag_nodes),
        "skipped_extensions": "null",
    }


UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:CodeNode {id: row.id})
SET n += row.props
"""

# One statement per relationship type, so Cypher like MATCH ()-[:imports]->()
# works in the Neo4j MCP server. The type is interpolated - Cypher cannot
# parameterise relationship types - so it is always passed through _rel_type
# first, and that whitelist regex is the injection guard. Doubled braces are
# literal braces for str.format.
UPSERT_EDGES_TEMPLATE = """
UNWIND $rows AS row
MATCH (a:CodeNode {{id: row.source}})
MATCH (b:CodeNode {{id: row.target}})
MERGE (a)-[r:{rel}]->(b)
SET r += row.props
"""
_REL_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

BATCH = 1000


def _scalar(value):
    """Neo4j properties must be primitives or arrays of primitives.

    dict/list/tuple are JSON-encoded (not str()'d) so the stored value stays
    parseable instead of becoming a Python repr.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return str(value)


def _rel_type(relation: str) -> str:
    """Sanitise a relation name into a legal Cypher relationship type."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", relation.strip()) or "RELATED"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"_{cleaned}"
    if not _REL_TYPE_RE.match(cleaned):
        raise ValueError(f"cannot make a safe relationship type from {relation!r}")
    return cleaned


def preflight_capacity(graph, node_limit: int = AURA_FREE_NODE_LIMIT,
                       rel_limit: int = AURA_FREE_REL_LIMIT):
    """Return (ok, message). Refuse rather than half-push.

    Aura Free caps nodes and relationships separately, so check both.
    """
    n, m = graph.number_of_nodes(), graph.number_of_edges()
    problems = []
    if n > node_limit:
        problems.append(f"{n} nodes exceeds the node limit of {node_limit}")
    if m > rel_limit:
        problems.append(f"{m} relationships exceeds the relationship limit of {rel_limit}")
    if problems:
        return False, "; ".join(problems) + "; reduce repos.yaml or upgrade the instance"
    return True, (
        f"capacity ok: {n} nodes / {m} relationships against limits of "
        f"{node_limit} / {rel_limit}"
    )


def _tags_in_graph(graph) -> set[str]:
    return {
        d["repo"] for _, d in graph.nodes(data=True) if d.get("repo")
    }


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def push_global_graph(graph, settings: Settings, tags: set[str], *,
                      dry_run: bool = False, driver=None,
                      repo_paths: dict[str, str] | None = None) -> list[str]:
    """Reap then re-push each tag's slice. Externals are pushed once, shared."""
    present = _tags_in_graph(graph)
    unknown = tags - present
    if unknown:
        raise ValueError(
            f"tag(s) {sorted(unknown)} not present in the graph "
            f"(present: {sorted(present)})"
        )

    ok, capacity_msg = preflight_capacity(graph)
    lines = [capacity_msg]
    if not ok:
        return lines

    if dry_run:
        for tag in sorted(tags):
            count = sum(1 for _, d in graph.nodes(data=True) if d.get("repo") == tag)
            lines.append(f"dry run: would reap and push {count} nodes for {tag!r}")
        return lines

    own_driver = driver is None
    try:
        driver = driver or connect(settings)
        database = resolve_database(driver, settings.neo4j_database)
        with driver.session(database=database) as session:
            lines.extend(ensure_schema(session))

            for tag in sorted(tags):
                session.run(REAP_REPO_META, tag=tag).consume()
                session.run(REAP_REPO, tag=tag).consume()
                lines.append(f"reaped {tag!r}")

            # The requested repos' nodes, plus every external stub: externals
            # are shared across repos and MERGE makes re-pushing them idempotent.
            # _-prefixed props (_origin, _callable, ...) are graphify internals
            # and are dropped exactly as they are for edges below.
            node_rows = [
                {
                    "id": nid,
                    "props": {k: _scalar(v) for k, v in data.items()
                              if not k.startswith("_")},
                }
                for nid, data in graph.nodes(data=True)
                if data.get("repo") in tags or data.get("external")
            ]
            ids = {r["id"] for r in node_rows}
            for batch in _chunks(node_rows, BATCH):
                session.run(UPSERT_NODES, rows=batch).consume()
            lines.append(f"upserted {len(node_rows)} nodes")

            # Group edges by relationship type. build_from_json(directed=False)
            # yields (u, v) in arbitrary order; graphify keeps the true direction
            # in _src/_tgt, which is what "what calls X" in Cypher depends on.
            # An edge is written when AT LEAST ONE endpoint is in this push: if
            # the other endpoint is not in the database yet the MATCH is a
            # harmless no-op, and when that repo is pushed later the same row
            # lands, so partial pushes converge on the full edge set.
            by_rel: dict[str, list[dict]] = {}
            for u, v, data in graph.edges(data=True):
                if u not in ids and v not in ids:
                    continue  # neither endpoint belongs to this push
                source, target = data.get("_src"), data.get("_tgt")
                if {source, target} != {u, v}:
                    # Markers absent or stale (not both naming this edge's own
                    # endpoints): fall back atomically to the raw order rather
                    # than trusting either marker on its own.
                    source, target = u, v
                rel = _rel_type(str(data.get("relation") or "RELATED"))
                by_rel.setdefault(rel, []).append({
                    "source": source,
                    "target": target,
                    "props": {k: _scalar(val) for k, val in data.items()
                              if not k.startswith("_")},
                })
            total_edges = 0
            for rel, rows in sorted(by_rel.items()):
                statement = UPSERT_EDGES_TEMPLATE.format(rel=rel)
                for batch in _chunks(rows, BATCH):
                    session.run(statement, rows=batch).consume()
                total_edges += len(rows)
            lines.append(
                f"upserted {total_edges} edges across {len(by_rel)} relationship types"
            )

            session.run(REAP_ORPHAN_EXTERNALS).consume()
            lines.append("swept orphaned external nodes")

            paths = repo_paths or {}
            for tag in sorted(tags):
                session.run(REPO_UPSERT, **_repo_meta(graph, tag, paths.get(tag, ""))).consume()
                lines.append(f"wrote Repo node for {tag!r}")
    except Exception as exc:  # noqa: BLE001 - scrub before it can reach a log
        raise RuntimeError(
            _scrub(f"{type(exc).__name__}: {exc}", settings.neo4j_password)
        ) from None
    finally:
        if own_driver and driver is not None:
            driver.close()
    return lines
