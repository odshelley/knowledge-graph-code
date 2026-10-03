"""Neo4j access: connection, diagnostics, scoped reap, batched push.

Every error path here scrubs the password before it can reach a log, because
driver exceptions routinely include the connection string.
"""
from __future__ import annotations

from dataclasses import dataclass

from neo4j import GraphDatabase

from kgc.config import Settings

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
