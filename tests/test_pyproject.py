import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_graphify_mcp_extra_is_declared():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(dep.startswith("graphifyy[mcp]==") for dep in deps), (
        "graphify.serve needs the mcp SDK, which graphifyy ships only as its "
        "[mcp] extra - a bare 'graphifyy==' dependency leaves a fresh venv "
        "unable to launch the MCP server (ModuleNotFoundError: No module "
        f"named 'mcp'). Got: {deps}"
    )
