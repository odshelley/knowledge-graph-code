from pathlib import Path

import networkx as nx

from kgc.cli import main
from kgc.config import Repo, Settings


def test_build_reports_cleanly_when_no_global_graph_exists(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "kgc.cli.load_repos",
        lambda path: [Repo(path=tmp_path, tag="x", enabled=True)],
    )

    def fake_extract_repo(repo):
        raise RuntimeError("boom")

    monkeypatch.setattr("kgc.cli.extract_repo", fake_extract_repo)

    def fake_load_global_graph():
        raise FileNotFoundError("no global graph")

    monkeypatch.setattr("kgc.cli.load_global_graph", fake_load_global_graph)

    result = main(["build"])

    assert result == 1
    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    assert "no global graph" in captured.err


def test_build_normalises_global_graph_once(monkeypatch, capsys):
    monkeypatch.setattr("kgc.cli.load_repos", lambda path: [])

    calls = []

    def fake_normalize_direction(path):
        calls.append(path)
        return 3, 2

    monkeypatch.setattr("kgc.cli.normalize_direction", fake_normalize_direction)
    monkeypatch.setattr("kgc.cli.load_global_graph", lambda: nx.Graph())

    result = main(["build"])

    assert result == 0
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert "normalised direction on 3 edges (2 skipped: stale markers)" in captured.out


def test_push_returns_1_when_no_global_graph_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "kgc.cli.load_settings",
        lambda path: Settings(
            neo4j_uri="bolt://example",
            neo4j_username="neo4j",
            neo4j_password="secret",
            neo4j_database="neo4j",
        ),
    )
    monkeypatch.setattr("kgc.cli.load_repos", lambda path: [])

    def fake_load_global_graph():
        raise FileNotFoundError("no global graph")

    monkeypatch.setattr("kgc.cli.load_global_graph", fake_load_global_graph)

    result = main(["push"])

    assert result == 1


def test_push_reports_failure_cleanly(monkeypatch, capsys):
    tag = "x"
    monkeypatch.setattr(
        "kgc.cli.load_settings",
        lambda path: Settings(
            neo4j_uri="bolt://example",
            neo4j_username="neo4j",
            neo4j_password="secret",
            neo4j_database="neo4j",
        ),
    )
    monkeypatch.setattr(
        "kgc.cli.load_repos",
        lambda path: [Repo(path=Path("."), tag=tag, enabled=True)],
    )

    graph = nx.Graph()
    graph.add_node("x::n", repo=tag)
    monkeypatch.setattr("kgc.cli.load_global_graph", lambda: graph)

    # _cmd_push imports push_global_graph from kgc.neo4j_io locally at call
    # time, so the patch target is the function's home module, not kgc.cli.
    def fake_push_global_graph(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("kgc.neo4j_io.push_global_graph", fake_push_global_graph)

    result = main(["push"])

    assert result == 1
    captured = capsys.readouterr()
    assert "push failed: boom" in captured.err
