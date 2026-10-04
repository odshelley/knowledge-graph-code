import json
from pathlib import Path
from kgc.direction import Reorientation
from kgc.extract import ExtractResult
from kgc.globalgraph import (
    merge_repo,
    MergeResult,
    global_graph_path,
    normalize_direction,
    retag_shared_stubs,
)


def test_global_graph_path_is_under_home_graphify():
    p = global_graph_path()
    assert p.name == "global-graph.json"


def test_merge_repo_maps_global_add_summary(monkeypatch, tmp_path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text('{"nodes": [], "links": []}')

    captured = {}

    def fake_global_add(source_path, repo_tag):
        captured["source_path"] = source_path
        captured["repo_tag"] = repo_tag
        return {
            "repo_tag": repo_tag,
            "nodes_added": 7,
            "nodes_removed": 2,
            "skipped": False,
            "cross_repo_calls": 3,
        }

    monkeypatch.setattr("kgc.globalgraph.global_add", fake_global_add)

    res = merge_repo(
        ExtractResult(tag="alpha", nodes=7, edges=9, files=3,
                      seconds=0.1, graph_path=graph_path)
    )

    assert isinstance(res, MergeResult)
    assert captured["repo_tag"] == "alpha"
    assert captured["source_path"] == graph_path
    assert res.nodes_added == 7
    assert res.nodes_removed == 2
    assert res.cross_repo_calls == 3
    assert res.skipped is False


def test_merge_repo_propagates_skipped(monkeypatch, tmp_path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text('{"nodes": [], "links": []}')
    monkeypatch.setattr(
        "kgc.globalgraph.global_add",
        lambda source_path, repo_tag: {
            "repo_tag": repo_tag, "nodes_added": 0, "nodes_removed": 0,
            "skipped": True, "cross_repo_calls": 0,
        },
    )
    res = merge_repo(
        ExtractResult(tag="beta", nodes=0, edges=0, files=0,
                      seconds=0.0, graph_path=graph_path)
    )
    assert res.skipped is True


def test_normalize_direction_rewrites_reversed_links_and_is_idempotent(tmp_path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps({
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
        "links": [
            {"source": "b", "target": "a", "_src": "a", "_tgt": "b"},
            {"source": "c", "target": "d", "_src": "c", "_tgt": "d"},
        ],
    }))

    result = normalize_direction(graph_path)

    assert result == Reorientation(changed=1, skipped=0, repaired=0)
    data = json.loads(graph_path.read_text())
    reversed_link, correct_link = data["links"]
    assert reversed_link["source"] == "a"
    assert reversed_link["target"] == "b"
    assert reversed_link["_src"] == "a"
    assert reversed_link["_tgt"] == "b"
    assert correct_link["source"] == "c"
    assert correct_link["target"] == "d"

    assert normalize_direction(graph_path) == Reorientation(0, 0, 0)


def test_normalize_direction_persists_a_marker_repair_even_without_a_swap(tmp_path):
    # A repaired marker whose source/target were already right must still be
    # written back, otherwise the Neo4j push (which reads the markers) sees
    # the stale one.
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(json.dumps({
        "nodes": [{"id": "a"}, {"id": "d"}],
        "links": [{"source": "a", "target": "d", "_src": "ghost", "_tgt": "d"}],
    }))

    result = normalize_direction(graph_path)

    assert result == Reorientation(changed=0, skipped=0, repaired=1)
    (link,) = json.loads(graph_path.read_text())["links"]
    assert (link["_src"], link["_tgt"]) == ("a", "d")


def _shared_stub_graph():
    return {
        "nodes": [
            {"id": "alpha::f", "label": "f", "repo": "alpha", "local_id": "f",
             "source_file": "a.py"},
            {"id": "beta::g", "label": "g", "repo": "beta", "local_id": "g",
             "source_file": "b.py"},
            # unresolved builtin reference: global_add kept the first-merged
            # repo's copy, tagged alpha, although beta references it too
            {"id": "alpha::exc", "label": "Exception", "repo": "alpha",
             "local_id": "exc", "source_file": ""},
            # same shape, but only alpha ever references it -> stays alpha's
            {"id": "alpha::thing", "label": "Thing", "repo": "alpha",
             "local_id": "thing", "source_file": ""},
            {"id": "numpy", "label": "numpy", "external": True, "source_file": ""},
        ],
        "links": [
            {"source": "alpha::f", "target": "alpha::exc", "relation": "inherits",
             "_src": "alpha::f", "_tgt": "alpha::exc"},
            {"source": "beta::g", "target": "alpha::exc", "relation": "inherits",
             "_src": "beta::g", "_tgt": "alpha::exc"},
            {"source": "alpha::f", "target": "alpha::thing", "relation": "calls",
             "_src": "alpha::f", "_tgt": "alpha::thing"},
            {"source": "alpha::f", "target": "numpy", "relation": "imports",
             "_src": "alpha::f", "_tgt": "numpy"},
        ],
    }


def test_retag_shared_stubs_marks_multi_repo_survivors_external(tmp_path):
    graph_path = tmp_path / "global-graph.json"
    graph_path.write_text(json.dumps(_shared_stub_graph()))

    assert retag_shared_stubs(graph_path) == 1

    nodes = {n["id"]: n for n in json.loads(graph_path.read_text())["nodes"]}
    assert nodes["alpha::exc"]["external"] is True
    assert "repo" not in nodes["alpha::exc"]
    assert nodes["alpha::exc"]["local_id"] == "exc"
    # the single-repo survivor, the real nodes and the real stub are untouched
    assert nodes["alpha::thing"]["repo"] == "alpha"
    assert "external" not in nodes["alpha::thing"]
    assert nodes["alpha::f"]["repo"] == "alpha"
    assert nodes["beta::g"]["repo"] == "beta"
    assert nodes["numpy"]["external"] is True and "repo" not in nodes["numpy"]


def test_retag_shared_stubs_is_idempotent_and_does_not_rewrite_an_untouched_file(tmp_path):
    graph_path = tmp_path / "global-graph.json"
    graph_path.write_text(json.dumps(_shared_stub_graph()))
    retag_shared_stubs(graph_path)
    before = graph_path.read_text()

    assert retag_shared_stubs(graph_path) == 0
    assert graph_path.read_text() == before
