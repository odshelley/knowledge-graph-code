from pathlib import Path

import networkx as nx

from kgc.cli import main
from kgc.config import Repo, Settings
from kgc.direction import Reorientation


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


def test_build_normalises_then_retags_the_global_graph_once(monkeypatch, capsys):
    monkeypatch.setattr("kgc.cli.load_repos", lambda path: [])

    calls = []

    def fake_normalize_direction(path):
        calls.append(("normalize", path))
        return Reorientation(changed=3, skipped=2, repaired=4)

    def fake_retag_shared_stubs(path):
        calls.append(("retag", path))
        return 5

    monkeypatch.setattr("kgc.cli.normalize_direction", fake_normalize_direction)
    monkeypatch.setattr("kgc.cli.retag_shared_stubs", fake_retag_shared_stubs)
    monkeypatch.setattr("kgc.cli.load_global_graph", lambda: nx.Graph())

    result = main(["build"])

    assert result == 0
    # direction first (it only touches links), then the stub re-tag (nodes)
    assert [name for name, _ in calls] == ["normalize", "retag"]
    assert calls[0][1] == calls[1][1]
    out = capsys.readouterr().out
    assert "normalised direction on 3 edges (4 stale markers repaired, 2 skipped)" in out
    assert "re-tagged 5 shared stubs as external" in out


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

    # _cmd_push imports push_global_graph from kgc.graph.neo4j_io locally at
    # call time, so the patch target is the function's home module, not kgc.cli.
    def fake_push_global_graph(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("kgc.graph.neo4j_io.push_global_graph", fake_push_global_graph)

    result = main(["push"])

    assert result == 1
    captured = capsys.readouterr()
    assert "push failed: boom" in captured.err


import json
import stat


def _env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=neo4j+s://h.example\nNEO4J_USERNAME=u\n"
                   "NEO4J_PASSWORD=s3cret\nNEO4J_DATABASE=d\n", encoding="utf-8")
    return env


def _repos(tmp_path, monkeypatch):
    repos = tmp_path / "repos.yaml"
    repos.write_text("repos:\n  - path: /tmp/a\n    tag: alpha\n  - path: /tmp/b\n    tag: beta\n",
                     encoding="utf-8")
    monkeypatch.setattr("kgc.cli.REPOS_PATH", repos)
    return repos


def test_init_writes_a_secret_free_mcp_json_and_prints_the_claude_md_block(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    _repos(tmp_path, monkeypatch)
    out = tmp_path / ".mcp.json"

    assert main(["init", "--env", str(env), "--out", str(out)]) == 0

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    data = json.loads(out.read_text(encoding="utf-8"))
    assert list(data["mcpServers"]) == ["kgc"]
    assert data["mcpServers"]["kgc"]["args"][-2:] == ["--env", str(env.resolve())]
    raw = out.read_text(encoding="utf-8")
    for forbidden in ("s3cret", "NEO4J", "h.example", '"u"'):
        assert forbidden not in raw
    printed = capsys.readouterr().out
    assert "Add this to your CLAUDE.md" in printed
    assert "# kgc code graph" in printed and "`alpha`, `beta`" in printed


def test_init_can_append_the_block_to_a_claude_md_instead_of_printing(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    _repos(tmp_path, monkeypatch)
    target = tmp_path / "CLAUDE.md"
    target.write_text("# mine\n", encoding="utf-8")

    assert main(["init", "--env", str(env), "--out", str(tmp_path / ".mcp.json"),
                 "--claude-md", str(target)]) == 0

    text = target.read_text(encoding="utf-8")
    assert text.startswith("# mine\n\n# kgc code graph")
    printed = capsys.readouterr().out
    assert "appended kgc block to" in printed
    assert "Add this to your CLAUDE.md" not in printed


def test_init_refuses_a_broken_env_without_writing_anything(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=neo4j+s://h\n", encoding="utf-8")   # three keys missing
    _repos(tmp_path, monkeypatch)
    out = tmp_path / ".mcp.json"

    assert main(["init", "--env", str(env), "--out", str(out)]) == 1

    assert not out.exists()
    err = capsys.readouterr().err
    assert "missing required key" in err
    assert "s3cret" not in err


def test_init_reports_an_unwritable_out_path_without_a_traceback(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    _repos(tmp_path, monkeypatch)
    out = tmp_path / "missing-dir" / ".mcp.json"   # parent does not exist

    assert main(["init", "--env", str(env), "--out", str(out)]) == 1

    assert not out.exists()
    err = capsys.readouterr().err
    assert "init failed:" in err
    assert "s3cret" not in err


def test_init_reports_a_bad_claude_md_path_without_a_traceback(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    _repos(tmp_path, monkeypatch)
    out = tmp_path / ".mcp.json"
    bad_claude_md = tmp_path / "missing-dir" / "CLAUDE.md"   # parent does not exist

    assert main(["init", "--env", str(env), "--out", str(out),
                 "--claude-md", str(bad_claude_md)]) == 1

    # init writes the .mcp.json before appending to CLAUDE.md, so a bad
    # --claude-md path still leaves a valid .mcp.json behind.
    assert out.exists()
    err = capsys.readouterr().err
    assert "init failed:" in err
    assert "s3cret" not in err


def test_serve_loads_settings_and_hands_off_to_the_server(tmp_path, monkeypatch):
    env = _env(tmp_path)
    recorded = {}

    def fake_serve(settings, *, http_port=None):
        recorded["uri"] = settings.neo4j_uri
        recorded["http_port"] = http_port

    monkeypatch.setattr("kgc.server.app.serve", fake_serve)

    assert main(["serve", "--env", str(env)]) == 0
    assert recorded == {"uri": "neo4j+s://h.example", "http_port": None}

    assert main(["serve", "--env", str(env), "--http", "8765"]) == 0
    assert recorded["http_port"] == 8765
