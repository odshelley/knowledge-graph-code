import threading
import time

import anyio

from kgc.server import queries as q
from kgc.server.app import SERVER_NAME, STARTUP_TIMEOUT_SECONDS, build_server, startup_repo_tags
from kgc.server.db import KgcError
from kgc.server.tools import TOOL_NAMES
from tests.fakes import FakeBackend, node_row


def _text(result) -> str:
    return result.content[0].text


def _call(server, name, args):
    return anyio.run(lambda: server.call_tool(name, args))


def test_server_registers_exactly_the_nine_tools_in_order():
    server = build_server(FakeBackend())
    assert server.name == SERVER_NAME == "kgc"
    listed = anyio.run(server.list_tools)
    assert [t.name for t in listed] == list(TOOL_NAMES)
    assert all(t.description for t in listed)
    cypher_description = next(t for t in listed if t.name == "cypher").description
    assert "caller -> callee" in cypher_description
    assert "caller -> callee" not in server.instructions


def test_search_description_names_the_indexed_repos_when_known():
    with_tags = anyio.run(build_server(FakeBackend(), repo_tags=["alpha", "beta"]).list_tools)
    without = anyio.run(build_server(FakeBackend()).list_tools)
    search_with = next(t for t in with_tags if t.name == "search").description
    search_without = next(t for t in without if t.name == "search").description
    assert search_with.endswith(" Repos indexed: alpha, beta.")
    assert "Repos indexed" not in search_without


def test_tool_schemas_expose_the_spec_defaults():
    listed = {t.name: t for t in anyio.run(build_server(FakeBackend()).list_tools)}
    props = listed["callers"].input_schema["properties"]
    assert props["depth"]["default"] == 1
    assert props["max_tokens"]["default"] == 1500
    assert listed["impact"].input_schema["properties"]["depth"]["default"] == 2
    assert "max_tokens" not in listed["stats"].input_schema["properties"]
    assert set(listed["stats"].input_schema["properties"]) == {"repo"}


def test_calling_a_tool_returns_its_text():
    row = node_row("a::f", "f()", repo="alpha", source_file="a.py", community=1)
    server = build_server(FakeBackend([(q.SEARCH, [dict(row, score=1.0)])]))
    result = _call(server, "search", {"query": "f"})
    assert result.is_error is False
    assert _text(result) == "alpha  f()  a.py:L1  c1"


def test_backend_errors_come_back_as_plain_text_not_as_tool_errors():
    class _Down(FakeBackend):
        def read(self, query, **params):
            raise KgcError("cannot reach Neo4j at host.example; is the instance paused?")

    result = _call(build_server(_Down()), "stats", {})
    assert result.is_error is False
    assert _text(result) == "cannot reach Neo4j at host.example; is the instance paused?"


def test_unexpected_exceptions_become_internal_error_with_only_the_type_name():
    class _Buggy(FakeBackend):
        def read(self, query, **params):
            raise ValueError("neo4j+s://user:s3cret@host blew up")

    result = _call(build_server(_Buggy()), "externals", {})
    assert _text(result) == "internal error: ValueError"


def test_cypher_tool_refuses_writes_through_the_server_too():
    db = FakeBackend()
    result = _call(build_server(db), "cypher", {"query": "MATCH (n) SET n.x = 1"})
    assert _text(result) == "refused: read-only server"
    assert db.calls == []


def test_startup_repo_tags_reads_tags_or_falls_back_quietly():
    assert startup_repo_tags(FakeBackend([(q.REPO_TAGS, [{"tag": "b"}, {"tag": "a"}])])) == ["b", "a"]

    class _Down(FakeBackend):
        def read(self, query, **params):
            raise KgcError("down")

    assert startup_repo_tags(_Down()) == []


def test_startup_repo_tags_returns_a_copy_the_background_thread_cannot_later_mutate():
    class _Slow(FakeBackend):
        def read(self, query, **params):
            time.sleep(0.1)
            return [{"tag": "late"}]

    returned = startup_repo_tags(_Slow(), timeout=0.02)
    assert returned == []
    time.sleep(0.2)                      # the background thread has now finished writing
    assert returned == []                # but the list we were handed is unaffected


def test_startup_repo_tags_gives_up_after_the_timeout():
    class _Slow(FakeBackend):
        def read(self, query, **params):
            time.sleep(0.5)
            return [{"tag": "late"}]

    assert STARTUP_TIMEOUT_SECONDS == 3.0
    started = time.monotonic()
    assert startup_repo_tags(_Slow(), timeout=0.05) == []
    assert time.monotonic() - started < 0.4
    leftover = [t for t in threading.enumerate() if t.name.startswith("kgc-startup")]
    assert leftover and all(t.daemon for t in leftover)
