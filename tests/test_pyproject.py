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


def test_mcp_sdk_v2_is_declared_explicitly():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(dep.startswith("mcp>=2.3,<3") for dep in deps), (
        "kgc.server.app imports mcp.server.mcpserver.MCPServer, a 2.x API; the floor "
        f"must be explicit rather than inherited from graphifyy[mcp]'s 'mcp<3,>=1'. Got: {deps}"
    )
