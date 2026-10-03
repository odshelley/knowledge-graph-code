from pathlib import Path
from kgc.config import Settings
from setup_mcp import build_config

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


def test_neo4j_password_is_in_env_never_argv():
    # argv is readable by `ps`; the password goes only through the environment.
    cfg = build_config(SETTINGS, Path("/tmp/g.json"), Path("/usr/bin/python3"))
    neo = cfg["mcpServers"]["neo4j"]
    assert "secret" not in " ".join(neo["args"])
    assert neo["env"]["NEO4J_PASSWORD"] == "secret"
