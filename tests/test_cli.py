from pathlib import Path

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
