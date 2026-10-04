import networkx as nx
import pytest
from kgc.config import Settings
from kgc.graph.neo4j_io import (
    REAP_REPO,
    REAP_REPO_META,
    REAP_ORPHAN_EXTERNALS,
    REPO_UPSERT,
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
    from kgc.graph.neo4j_io import _rel_type
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

            def __iter__(self_inner):
                return iter([])
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


def test_reaps_match_on_the_codenode_label():
    # An unlabelled MATCH (n) cannot use the :CodeNode(repo) index the push
    # creates; both reaps must be label-scoped.
    assert "MATCH (n:CodeNode)" in REAP_REPO
    assert "MATCH (n:CodeNode)" in REAP_ORPHAN_EXTERNALS


def _node_rows(driver):
    return [row for q, p in driver.session_obj.calls
            if "MERGE (n:CodeNode" in q for row in p["rows"]]


def test_push_strips_private_node_props_but_keeps_public_ones():
    # Edge props already drop _-prefixed keys; node props must do the same
    # (_origin/_callable are graphify internals, ~65k values across the real
    # graph) while keeping everything Cypher users and the reaper rely on.
    g = _graph()
    g.nodes["alpha::mod_f"].update(_origin="ast", _callable=True,
                                   local_id="mod_f", source_file="mod.py")
    driver = _RecordingDriver()
    push_global_graph(g, SETTINGS, {"alpha"}, driver=driver)
    (row,) = [r for r in _node_rows(driver) if r["id"] == "alpha::mod_f"]
    assert not any(k.startswith("_") for k in row["props"])
    assert row["props"]["repo"] == "alpha"
    assert row["props"]["local_id"] == "mod_f"
    assert row["props"]["source_file"] == "mod.py"
    assert row["props"]["label"] == "f"
    assert row["props"]["file_type"] == "code"
    (ext,) = [r for r in _node_rows(driver) if r["id"] == "numpy"]
    assert ext["props"]["external"] is True


def test_partial_push_includes_edges_with_one_endpoint_outside_the_pushed_set():
    # Pushing only alpha must still write the beta->alpha call. If beta's node
    # is absent the MATCH is a harmless no-op; when beta is pushed later the
    # same row lands, so repeated partial pushes converge on the full graph.
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    calls_rows = [row for q, p in _edge_statements(driver)
                  if "calls" in q.lower() for row in p["rows"]]
    (row,) = calls_rows
    assert (row["source"], row["target"]) == ("beta::mod_g", "alpha::mod_f")


def test_push_falls_back_to_raw_order_when_markers_do_not_name_the_endpoints():
    # The fallback is atomic: a half-stale marker pair never yields a
    # self-loop or an endpoint that is not on the edge.
    g = _graph()
    g.edges["alpha::mod_f", "beta::mod_g"].update(_src="ghost", _tgt="alpha::mod_f")
    driver = _RecordingDriver()
    push_global_graph(g, SETTINGS, {"alpha", "beta"}, driver=driver)
    calls_rows = [row for q, p in _edge_statements(driver)
                  if "calls" in q.lower() for row in p["rows"]]
    (row,) = calls_rows
    assert {row["source"], row["target"]} == {"alpha::mod_f", "beta::mod_g"}


def _queries(driver):
    return [" ".join(q.split()) for q, _ in driver.session_obj.calls]


def test_push_ensures_schema_before_any_write():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    queries = _queries(driver)
    # resolve_database's COUNT probe is recorded first (same fake session), so
    # look for the schema statements anywhere before the first destructive query.
    first_write = next(i for i, q in enumerate(queries) if "DELETE" in q)
    assert any(q.startswith("SHOW INDEXES") for q in queries[:first_write])
    assert any("CREATE CONSTRAINT codenode_id" in q for q in queries[:first_write])
    assert any("CREATE FULLTEXT INDEX kgc_text" in q for q in queries[:first_write])


def test_reap_removes_the_repo_node_so_an_incomplete_push_is_visible():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    queries = _queries(driver)
    meta_reap = next(i for i, q in enumerate(queries) if "MATCH (r:Repo {tag: $tag}) DELETE r" in q)
    first_node_write = next(i for i, q in enumerate(queries) if "MERGE (n:CodeNode" in q)
    assert meta_reap < first_node_write
    assert "$tag" in REAP_REPO_META


def test_push_writes_the_repo_node_last_per_push():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha", "beta"}, driver=driver,
                      repo_paths={"alpha": "/src/alpha", "beta": "/src/beta"})
    queries = _queries(driver)
    repo_writes = [i for i, q in enumerate(queries) if "MERGE (r:Repo" in q]
    assert len(repo_writes) == 2
    last_data_write = max(
        i for i, q in enumerate(queries)
        if "MERGE (n:CodeNode" in q or "[r:" in q or "NOT (n)--()" in q
    )
    assert last_data_write < repo_writes[0]


def test_repo_node_carries_counts_path_and_pushed_at():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver,
                      repo_paths={"alpha": "/src/alpha"})
    (params,) = [p for q, p in driver.session_obj.calls if "MERGE (r:Repo" in q]
    assert params["tag"] == "alpha"
    assert params["path"] == "/src/alpha"
    assert params["node_count"] == 1          # alpha::mod_f
    assert params["edge_count"] == 2          # alpha->numpy and beta->alpha touch alpha
    assert params["built_at_commit"] is None  # Plan 2 supplies it
    assert params["skipped_extensions"] == "null"
    assert params["pushed_at"].endswith("+00:00")
    assert "MERGE (r:Repo {tag: $tag})" in " ".join(REPO_UPSERT.split())


def test_repo_node_path_defaults_to_empty_when_not_supplied():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    (params,) = [p for q, p in driver.session_obj.calls if "MERGE (r:Repo" in q]
    assert params["path"] == ""
