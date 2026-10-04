import threading
import time

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable

from kgc.config import Settings
from kgc.server.db import QUERY_TIMEOUT_SECONDS, Backend, KgcError, _read

SETTINGS = Settings(
    neo4j_uri="neo4j+s://f35a6a04.databases.neo4j.io",
    neo4j_username="user",
    neo4j_password="s3cret",
    neo4j_database="neo4j",
)


class _Record:
    def __init__(self, d):
        self._d = d

    def data(self):
        return dict(self._d)


class _Tx:
    def __init__(self, rows, fail=None):
        self._rows, self._fail = rows, fail
        self.ran = []

    def run(self, query, **params):
        self.ran.append((query, params))
        if self._fail is not None:
            raise self._fail
        return [_Record(r) for r in self._rows]


class _Session:
    def __init__(self, rows, fail=None):
        self.tx = _Tx(rows, fail)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute_read(self, fn, *args, **kwargs):
        return fn(self.tx, *args, **kwargs)


class _Driver:
    def __init__(self, rows=(), fail=None):
        self.rows, self.fail, self.closed, self.sessions = list(rows), fail, False, 0

    def session(self, database=None):
        self.sessions += 1
        self.database = database
        self.last_session = _Session(self.rows, self.fail)
        return self.last_session

    def close(self):
        self.closed = True


def _backend(driver, *, connect_calls=None):
    calls = connect_calls if connect_calls is not None else []

    def connect_fn(settings):
        calls.append(settings)
        if isinstance(driver, Exception):
            raise driver
        return driver

    return Backend(SETTINGS, connect_fn=connect_fn, resolve_fn=lambda d, name: "neo4j")


def test_backend_connects_lazily_and_only_once():
    calls = []
    driver = _Driver(rows=[{"a": 1}])
    backend = _backend(driver, connect_calls=calls)
    assert calls == []                       # constructing does not connect
    assert backend.read("RETURN 1 AS a") == [{"a": 1}]
    assert backend.read("RETURN 1 AS a") == [{"a": 1}]
    assert len(calls) == 1
    assert driver.database == "neo4j"


def test_concurrent_first_reads_connect_exactly_once():
    calls = []
    lock = threading.Lock()

    def connect_fn(settings):
        with lock:
            calls.append(settings)
        time.sleep(0.05)
        return _Driver(rows=[{"a": 1}])

    backend = Backend(SETTINGS, connect_fn=connect_fn, resolve_fn=lambda d, name: "neo4j")
    results: list[list[dict]] = [None] * 8

    def worker(i):
        results[i] = backend.read("RETURN 1 AS a")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1
    assert results == [[{"a": 1}]] * 8


def test_read_passes_query_and_params_into_the_read_transaction():
    driver = _Driver(rows=[])
    backend = _backend(driver)
    backend.read("MATCH (n {id: $id}) RETURN n", id="x", limit=3)
    # the session was created inside read(); grab the tx it ran on
    assert driver.sessions == 1
    assert driver.last_session.tx.ran == [("MATCH (n {id: $id}) RETURN n", {"id": "x", "limit": 3})]


def test_unit_of_work_timeout_is_ten_seconds():
    assert QUERY_TIMEOUT_SECONDS == 10.0
    assert _read.timeout == 10.0              # set by neo4j.unit_of_work


def test_unreachable_database_gives_the_spec_sentence_and_retries_next_time():
    calls = []
    backend = _backend(ServiceUnavailable("Unable to retrieve routing information for neo4j+s://user:s3cret@host"),
                       connect_calls=calls)
    with pytest.raises(KgcError) as ei:
        backend.read("RETURN 1")
    assert str(ei.value) == "cannot reach Neo4j at f35a6a04.databases.neo4j.io; is the instance paused?"
    with pytest.raises(KgcError):
        backend.read("RETURN 1")
    assert len(calls) == 2                   # the driver was reset, so it reconnected


def test_service_unavailable_mid_session_resets_the_driver():
    driver = _Driver(fail=ServiceUnavailable("gone"))
    calls = []
    backend = _backend(driver, connect_calls=calls)
    with pytest.raises(KgcError, match="cannot reach Neo4j"):
        backend.read("RETURN 1")
    assert driver.closed is True
    assert len(calls) == 1


class _Timeout(ClientError):
    code = "Neo.ClientError.Transaction.TransactionTimedOut"


class _Syntax(ClientError):
    code = "Neo.ClientError.Statement.SyntaxError"


def test_transaction_timeout_gives_the_spec_sentence():
    backend = _backend(_Driver(fail=_Timeout("The transaction has been terminated")))
    with pytest.raises(KgcError) as ei:
        backend.read("MATCH p=()-[*1..4]->() RETURN p")
    assert str(ei.value) == "timed out; narrow by repo or reduce depth"


def test_query_errors_are_reported_and_scrubbed():
    backend = _backend(_Driver(fail=_Syntax("Invalid input near 's3cret'")))
    with pytest.raises(KgcError) as ei:
        backend.read("MATCH (n RETURN n")
    assert str(ei.value).startswith("query error: ")
    assert "s3cret" not in str(ei.value)
    assert "***" in str(ei.value)


def test_host_is_the_uri_hostname_only():
    assert _backend(_Driver()).host == "f35a6a04.databases.neo4j.io"


def test_close_is_safe_before_and_after_connecting():
    driver = _Driver(rows=[{"a": 1}])
    backend = _backend(driver)
    backend.close()
    backend.read("RETURN 1 AS a")
    backend.close()
    assert driver.closed is True


def test_resolve_failure_closes_the_fresh_driver_and_reports_unreachable():
    calls = []
    driver = _Driver(rows=[{"a": 1}])

    def connect_fn(settings):
        calls.append(settings)
        return driver

    def resolve_fn(d, name):
        raise RuntimeError("neither database name worked")

    backend = Backend(SETTINGS, connect_fn=connect_fn, resolve_fn=resolve_fn)
    with pytest.raises(KgcError) as ei:
        backend.read("RETURN 1")
    assert str(ei.value) == "cannot reach Neo4j at f35a6a04.databases.neo4j.io; is the instance paused?"
    assert driver.closed is True
    with pytest.raises(KgcError):
        backend.read("RETURN 1")
    assert len(calls) == 2  # the driver was reset, so it reconnected
