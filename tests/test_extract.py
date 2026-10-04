# tests/test_extract.py
import json
from pathlib import Path
import pytest
from kgc.config import Repo
from kgc.extract import code_only_detect, extract_repo, repo_output_dir, ExtractResult


@pytest.fixture
def graphify_home(tmp_path, monkeypatch):
    """Redirect per-repo outputs away from the real ~/.graphify for the test."""
    home = tmp_path / "graphify-home"
    monkeypatch.setattr("kgc.extract.global_path", lambda: home / "global-graph.json")
    return home


@pytest.fixture
def repo_dir(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    return d


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


def test_code_only_detect_empties_non_code_categories(repo_dir, graphify_home):
    (repo_dir / "mod.py").write_text("def f():\n    return 1\n")
    (repo_dir / "README.md").write_text("# docs\n\nprose that must not be extracted\n")
    (repo_dir / "notes.txt").write_text("more prose\n")

    result = code_only_detect(repo_dir, cache_root=graphify_home / "repos" / "x")

    assert result["files"]["document"] == []
    assert result["files"]["paper"] == []
    assert result["files"]["image"] == []
    assert result["files"]["video"] == []
    assert any(f.endswith("mod.py") for f in result["files"]["code"])
    assert result["total_files"] == len(result["files"]["code"])


def test_repo_output_dir_lives_beside_the_global_graph(graphify_home):
    assert repo_output_dir("fixture") == graphify_home / "repos" / "fixture" / "graphify-out"


def test_extract_repo_produces_graph_and_costs_no_tokens(repo_dir, graphify_home):
    (repo_dir / "a.py").write_text(
        "def helper():\n    return 2\n\n\ndef caller():\n    return helper()\n"
    )
    (repo_dir / "README.md").write_text("# prose\n")
    repo = Repo(path=repo_dir, tag="fixture", enabled=True)

    res = extract_repo(repo)

    assert isinstance(res, ExtractResult)
    assert res.tag == "fixture"
    assert res.nodes > 0
    assert res.graph_path.exists()
    assert res.graph_path == graphify_home / "repos" / "fixture" / "graphify-out" / "graph.json"


def test_extract_repo_writes_nothing_into_the_repo(repo_dir, graphify_home):
    # The analysed checkout must stay clean: no graphify-out/, no cache, no
    # marker files. Everything lands under the per-repo dir beside the global
    # graph instead (the control arm of the verification harness runs in the
    # repo and must not find a ready-made graph there).
    (repo_dir / "a.py").write_text("import os\n\ndef f():\n    return os.getcwd()\n")
    repo = Repo(path=repo_dir, tag="fixture", enabled=True)
    before = _snapshot(repo_dir)

    extract_repo(repo)

    assert _snapshot(repo_dir) == before
    assert not (repo_dir / "graphify-out").exists()
    # graphify's own extraction cache goes beside graph.json, not in the repo
    assert (graphify_home / "repos" / "fixture" / "graphify-out" / "cache").is_dir()


def test_extract_repo_graph_has_only_python_sources_and_communities(repo_dir, graphify_home):
    (repo_dir / "a.py").write_text("import os\n\ndef f():\n    return os.getcwd()\n")
    (repo_dir / "GUIDE.md").write_text("# a guide\n\nlots of prose here\n")
    repo = Repo(path=repo_dir, tag="fixture", enabled=True)

    res = extract_repo(repo)
    data = json.loads(res.graph_path.read_text())

    # The real invariant is provenance, not file_type: the AST pass legitimately
    # emits docstring-derived 'rationale'/'concept' nodes FROM .py files. Every
    # sourced node must come from a .py; the only unsourced nodes are external
    # stubs (here: 'os'). Nothing may originate from GUIDE.md.
    for n in data["nodes"]:
        src = n.get("source_file")
        if src:
            assert src.endswith(".py"), n
        else:
            assert n.get("external") is True, n

    # graphify's MCP server reconstructs communities from this attribute.
    assert all("community" in n for n in data["nodes"])


def test_extract_repo_writes_normalised_direction(repo_dir, graphify_home):
    (repo_dir / "a.py").write_text(
        "def helper():\n    return 2\n\n\ndef caller():\n    return helper()\n"
    )
    repo = Repo(path=repo_dir, tag="fixture", enabled=True)

    res = extract_repo(repo)
    data = json.loads(res.graph_path.read_text())

    links = data.get("links", data.get("edges", []))
    marked = [link for link in links if "_src" in link and "_tgt" in link]
    assert marked, "expected at least one link with _src/_tgt markers"
    for link in marked:
        assert link["source"] == link["_src"]
        assert link["target"] == link["_tgt"]


def test_extract_repo_missing_path_raises(tmp_path, graphify_home):
    repo = Repo(path=tmp_path / "absent", tag="x", enabled=True)
    with pytest.raises(FileNotFoundError):
        extract_repo(repo)


def test_code_only_detect_excludes_pdfs_without_warning(repo_dir, graphify_home, capsys):
    (repo_dir / "mod.py").write_text("def f():\n    return 1\n")
    (repo_dir / "paper.pdf").write_bytes(b"%PDF-1.4 not really")

    result = code_only_detect(repo_dir, cache_root=graphify_home / "repos" / "x")

    assert result["files"]["paper"] == []
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out
    assert "WARNING" not in captured.err
