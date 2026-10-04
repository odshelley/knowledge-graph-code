"""Read-only Neo4j access for the server.

One lazy driver per process, every query in a read transaction with a 10 s
timeout, and every failure mapped to the one-line sentence the spec names so
a tool can return it as text. This is the only server module that imports
the driver.
"""
from __future__ import annotations

import threading
from urllib.parse import urlparse

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from kgc.config import Settings
from kgc.graph.neo4j_io import _scrub, connect, resolve_database
from kgc.server.format import NARROW_HINT

QUERY_TIMEOUT_SECONDS = 10.0


class KgcError(Exception):
    """A failure the tool reports to the agent as one line of text."""


def _host(uri: str) -> str:
    return urlparse(uri).hostname or uri


@unit_of_work(timeout=QUERY_TIMEOUT_SECONDS)
def _read(tx, query: str, params: dict) -> list[dict]:
    return [record.data() for record in tx.run(query, **params)]


class Backend:
    def __init__(self, settings: Settings, *, connect_fn=connect, resolve_fn=resolve_database):
        self._settings = settings
        self._connect = connect_fn
        self._resolve = resolve_fn
        self._driver = None
        self._database: str | None = None
        self._lock = threading.Lock()

    @property
    def host(self) -> str:
        return _host(self._settings.neo4j_uri)

    def _unreachable(self) -> KgcError:
        return KgcError(f"cannot reach Neo4j at {self.host}; is the instance paused?")

    def _ensure(self):
        with self._lock:
            if self._driver is not None:
                return self._driver, self._database
            try:
                driver = self._connect(self._settings)
            except Exception:  # noqa: BLE001 - every connect failure is "unreachable" to the agent
                raise self._unreachable() from None
            try:
                database = self._resolve(driver, self._settings.neo4j_database)
            except Exception:  # noqa: BLE001 - resolve failure is also "unreachable" to the agent
                try:
                    driver.close()
                except Exception:  # noqa: BLE001 - closing a fresh driver may still fail
                    pass
                raise self._unreachable() from None
            self._driver = driver
            self._database = database
            return driver, database

    def _reset(self) -> None:
        with self._lock:
            driver, self._driver, self._database = self._driver, None, None
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001 - closing a broken driver may itself fail
                pass

    def read(self, query: str, **params) -> list[dict]:
        driver, database = self._ensure()
        try:
            with driver.session(database=database) as session:
                return session.execute_read(_read, query, params)
        except (ServiceUnavailable, SessionExpired):
            self._reset()
            raise self._unreachable() from None
        except Neo4jError as exc:
            code = getattr(exc, "code", "") or ""
            if "TimedOut" in code or "timeout" in str(exc).lower():
                raise KgcError(f"timed out; {NARROW_HINT}") from None
            message = str(exc) or getattr(exc, "message", None) or "unknown error"
            raise KgcError(
                _scrub(f"query error: {message}", self._settings.neo4j_password)
            ) from None

    def close(self) -> None:
        self._reset()
