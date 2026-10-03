import json
from pathlib import Path
from kgc.extract import ExtractResult
from kgc.globalgraph import (
    merge_repo,
    MergeResult,
    global_graph_path,
    normalize_direction,
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

    changed, skipped = normalize_direction(graph_path)

    assert (changed, skipped) == (1, 0)
    data = json.loads(graph_path.read_text())
    reversed_link, correct_link = data["links"]
    assert reversed_link["source"] == "a"
    assert reversed_link["target"] == "b"
    assert reversed_link["_src"] == "a"
    assert reversed_link["_tgt"] == "b"
    assert correct_link["source"] == "c"
    assert correct_link["target"] == "d"

    assert normalize_direction(graph_path) == (0, 0)
