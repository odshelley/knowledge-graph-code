import json
import stat
from pathlib import Path
from kgc.config import Settings
import setup_mcp
from setup_mcp import NEO4J_MCP_VERSION, build_config

SETTINGS = Settings(
    neo4j_uri="neo4j+s://abc.databases.neo4j.io",
    neo4j_username="abc",
    neo4j_password="secret",
    neo4j_database="neo4j",
)


def test_config_declares_both_servers():
    cfg = build_config(SETTINGS, Path("/tmp/global-graph.json"), Path("/usr/bin/python3"))
    assert set(cfg["mcpServers"]) == {"graphify", "neo4j"}


def test_graphify_server_points_at_the_global_graph():
    cfg = build_config(SETTINGS, Path("/tmp/global-graph.json"), Path("/usr/bin/python3"))
    args = cfg["mcpServers"]["graphify"]["args"]
    assert "/tmp/global-graph.json" in " ".join(args)


def test_neo4j_server_is_read_only_and_token_capped():
    cfg = build_config(SETTINGS, Path("/tmp/g.json"), Path("/usr/bin/python3"))
    neo = cfg["mcpServers"]["neo4j"]
    assert "--read-only" in neo["args"]
    assert "--token-limit" in neo["args"]
    joined = " ".join(neo.get("args", []))
    assert "write" not in joined.lower()


def test_neo4j_server_version_is_pinned():
    # CLAUDE.md's tool names (read_neo4j_cypher / get_neo4j_schema) and the
    # flags below were verified against 0.6.0; @latest could rename either.
    cfg = build_config(SETTINGS, Path("/tmp/g.json"), Path("/usr/bin/python3"))
    assert NEO4J_MCP_VERSION == "0.6.0"
    assert cfg["mcpServers"]["neo4j"]["args"][0] == "mcp-neo4j-cypher@0.6.0"
    assert "@latest" not in " ".join(cfg["mcpServers"]["neo4j"]["args"])


def test_neo4j_password_is_in_env_never_argv():
    # argv is readable by `ps`; the password goes only through the environment.
    cfg = build_config(SETTINGS, Path("/tmp/g.json"), Path("/usr/bin/python3"))
    neo = cfg["mcpServers"]["neo4j"]
    assert "secret" not in " ".join(neo["args"])
    assert neo["env"]["NEO4J_PASSWORD"] == "secret"


def test_main_writes_mcp_json_as_0600_even_over_a_wider_existing_file(tmp_path, monkeypatch):
    # write_text-then-chmod leaves a default-umask window in which the password
    # is world-readable; the file must be created 0600 and an existing wider
    # file tightened, with the old content fully replaced.
    out = tmp_path / ".mcp.json"
    out.write_text("stale, world-readable, and longer than the new content" * 4,
                   encoding="utf-8")
    out.chmod(0o644)
    monkeypatch.setattr("setup_mcp.OUT_PATH", out)
    monkeypatch.setattr("setup_mcp.load_settings", lambda path: SETTINGS)
    monkeypatch.setattr("setup_mcp.global_graph_path", lambda: Path("/tmp/g.json"))

    assert setup_mcp.main() == 0

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data["mcpServers"]) == {"graphify", "neo4j"}


def test_main_creates_a_fresh_mcp_json_as_0600(tmp_path, monkeypatch):
    out = tmp_path / ".mcp.json"
    monkeypatch.setattr("setup_mcp.OUT_PATH", out)
    monkeypatch.setattr("setup_mcp.load_settings", lambda path: SETTINGS)
    monkeypatch.setattr("setup_mcp.global_graph_path", lambda: Path("/tmp/g.json"))

    assert setup_mcp.main() == 0

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
