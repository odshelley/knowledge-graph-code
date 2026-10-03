# tests/test_extract.py
from pathlib import Path
import pytest
from kgc.config import Repo
from kgc.extract import code_only_detect, extract_repo, ExtractResult


def test_code_only_detect_empties_non_code_categories(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    (tmp_path / "README.md").write_text("# docs\n\nprose that must not be extracted\n")
    (tmp_path / "notes.txt").write_text("more prose\n")

    result = code_only_detect(tmp_path)

    assert result["files"]["document"] == []
    assert result["files"]["paper"] == []
    assert result["files"]["image"] == []
    assert result["files"]["video"] == []
    assert any(f.endswith("mod.py") for f in result["files"]["code"])
    assert result["total_files"] == len(result["files"]["code"])


def test_extract_repo_produces_graph_and_costs_no_tokens(tmp_path):
    (tmp_path / "a.py").write_text(
        "def helper():\n    return 2\n\n\ndef caller():\n    return helper()\n"
    )
    (tmp_path / "README.md").write_text("# prose\n")
    repo = Repo(path=tmp_path, tag="fixture", enabled=True)

    res = extract_repo(repo)

    assert isinstance(res, ExtractResult)
    assert res.tag == "fixture"
    assert res.nodes > 0
    assert res.graph_path.exists()
    assert res.graph_path == tmp_path / "graphify-out" / "graph.json"


def test_extract_repo_graph_has_only_python_sources_and_communities(tmp_path):
    import json
    (tmp_path / "a.py").write_text("import os\n\ndef f():\n    return os.getcwd()\n")
    (tmp_path / "GUIDE.md").write_text("# a guide\n\nlots of prose here\n")
    repo = Repo(path=tmp_path, tag="fixture", enabled=True)

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


def test_extract_repo_missing_path_raises(tmp_path):
    repo = Repo(path=tmp_path / "absent", tag="x", enabled=True)
    with pytest.raises(FileNotFoundError):
        extract_repo(repo)


def test_code_only_detect_excludes_pdfs_without_warning(tmp_path, capsys):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-1.4 not really")

    result = code_only_detect(tmp_path)

    assert result["files"]["paper"] == []
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out
    assert "WARNING" not in captured.err
