import networkx as nx
import pytest
from kgc.config import Settings
from kgc.neo4j_io import (
    REAP_REPO,
    REAP_ORPHAN_EXTERNALS,
    preflight_capacity,
    push_global_graph,
)

SETTINGS = Settings(
    neo4j_uri="neo4j+s://abc.databases.neo4j.io",
    neo4j_username="abc",
    neo4j_password="secret",
    neo4j_database="neo4j",
)


def _graph():
    # Mirrors what global_add produces: repo-local ids prefixed "{tag}::",
    # a shared unprefixed external stub, and true direction kept in _src/_tgt
    # (the Graph itself is undirected, so (u, v) order is arbitrary).
    g = nx.Graph()
    g.add_node("alpha::mod_f", label="f", file_type="code", repo="alpha")
    g.add_node("beta::mod_g", label="g", file_type="code", repo="beta")
    g.add_node("numpy", label="numpy", file_type="code", external=True)
    g.add_edge("alpha::mod_f", "numpy", relation="imports", confidence="EXTRACTED",
               _src="alpha::mod_f", _tgt="numpy")
    g.add_edge("beta::mod_g", "numpy", relation="imports", confidence="EXTRACTED",
               _src="beta::mod_g", _tgt="numpy")
    g.add_edge("alpha::mod_f", "beta::mod_g", relation="calls", confidence="EXTRACTED",
               _src="beta::mod_g", _tgt="alpha::mod_f")  # beta calls alpha
    return g


def test_reap_repo_is_scoped_to_a_repo_property():
    # The single most dangerous query in the project. It must never be able to
    # match the whole database.
    assert "$tag" in REAP_REPO
    assert "n.repo = $tag" in REAP_REPO
    assert "DETACH DELETE" in REAP_REPO


def test_reap_orphan_externals_only_targets_unreferenced_externals():
    assert "external" in REAP_ORPHAN_EXTERNALS
    assert "NOT (n)--()" in REAP_ORPHAN_EXTERNALS
    assert "$tag" not in REAP_ORPHAN_EXTERNALS


def test_preflight_capacity_passes_under_limits():
    ok, msg = preflight_capacity(_graph(), node_limit=1000, rel_limit=1000)
    assert ok is True
    assert "3 nodes" in msg


def test_preflight_capacity_fails_over_node_limit():
    ok, msg = preflight_capacity(_graph(), node_limit=2, rel_limit=1000)
    assert ok is False
    assert "node limit" in msg


def test_preflight_capacity_fails_over_relationship_limit():
    # Aura Free's relationship cap is a separate ceiling from the node cap.
    ok, msg = preflight_capacity(_graph(), node_limit=1000, rel_limit=1)
    assert ok is False
    assert "relationship limit" in msg


def test_rel_type_sanitises_an_injection_attempt():
    # Relationship types cannot be parameterised in Cypher, so this string is
    # interpolated. The sanitiser is the injection guard.
    from kgc.neo4j_io import _rel_type
    out = _rel_type("imports]->(x) DETACH DELETE x //")
    assert all(ch.isalnum() or ch == "_" for ch in out)
    assert out[0].isalpha() or out[0] == "_"
    assert _rel_type("imports") == "imports"


class _Recorder:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))

        class _R:
            def single(self_inner):
                return {"nodes": 0, "rels": 0}

            def consume(self_inner):
                return None
        return _R()


class _RecordingDriver:
    def __init__(self):
        self.session_obj = _Recorder()

    def session(self, database=None):
        return self.session_obj

    def close(self):
        pass


def test_push_reaps_each_tag_before_writing():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha", "beta"}, driver=driver)
    reaps = [p["tag"] for q, p in driver.session_obj.calls
             if "DETACH DELETE" in q and "tag" in p]
    assert set(reaps) == {"alpha", "beta"}


def test_push_dry_run_writes_nothing():
    driver = _RecordingDriver()
    lines = push_global_graph(_graph(), SETTINGS, {"alpha"},
                             dry_run=True, driver=driver)
    assert driver.session_obj.calls == []
    assert any("dry run" in line.lower() for line in lines)


def test_push_refuses_unknown_tag():
    driver = _RecordingDriver()
    with pytest.raises(ValueError, match="not present in the graph"):
        push_global_graph(_graph(), SETTINGS, {"nonexistent"}, driver=driver)


def _edge_statements(driver):
    return [(q, p) for q, p in driver.session_obj.calls if "[r:" in q]


def test_push_emits_one_statement_per_relationship_type():
    # Real relationship types, not a single :RELATES with a 'relation' property,
    # so Cypher like MATCH ()-[:imports]->() works in the Neo4j MCP server.
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha", "beta"}, driver=driver)
    rel_types = {q.split("[r:")[1].split("]")[0] for q, _ in _edge_statements(driver)}
    assert {t.lower() for t in rel_types} == {"imports", "calls"}


def test_push_preserves_true_edge_direction():
    # The undirected Graph yields (u, v) in arbitrary order; the push must use
    # _src/_tgt so "what calls X" is answerable in Cypher.
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha", "beta"}, driver=driver)
    calls_rows = [row for q, p in _edge_statements(driver)
                  if "calls" in q.lower() for row in p["rows"]]
    (row,) = calls_rows
    assert row["source"] == "beta::mod_g"
    assert row["target"] == "alpha::mod_f"
    assert "_src" not in row["props"] and "_tgt" not in row["props"]


def test_push_scrubs_password_from_driver_errors():
    # Driver exceptions routinely include the connection string. The probe
    # session (resolve_database's first call) must succeed so the failure
    # surfaces from the real work session, carrying the password text that
    # the fix is responsible for scrubbing before it reaches a traceback.
    class _AuthFailingDriver:
        def __init__(self):
            self.calls = 0

        def session(self, database=None):
            self.calls += 1
            if self.calls == 1:
                return _Recorder()
            raise RuntimeError(
                "auth failed for neo4j+s://x with password secret"
            )

        def close(self):
            pass

    driver = _AuthFailingDriver()
    with pytest.raises(RuntimeError) as ei:
        push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    assert "secret" not in str(ei.value)
    assert "***" in str(ei.value)
    assert ei.value.__cause__ is None
