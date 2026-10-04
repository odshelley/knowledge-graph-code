import json
import stat
from pathlib import Path

from kgc.server.claude_md import MARKER, render_claude_md
from kgc.server.setup import append_claude_md, build_mcp_config, write_private


def test_mcp_config_has_one_server_and_no_secrets(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=neo4j+s://h\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=s3cret\nNEO4J_DATABASE=d\n")
    cfg = build_mcp_config(env, Path("/venv/bin/python"))
    assert list(cfg["mcpServers"]) == ["kgc"]
    kgc = cfg["mcpServers"]["kgc"]
    assert kgc["command"] == "/venv/bin/python"
    assert kgc["args"] == ["-m", "kgc.cli", "serve", "--env", str(env.resolve())]
    assert "env" not in kgc
    raw = json.dumps(cfg)
    for forbidden in ("s3cret", "NEO4J", "neo4j+s://h", '"u"', '"d"'):
        assert forbidden not in raw


def test_mcp_config_defaults_to_the_running_interpreter(tmp_path):
    import sys
    cfg = build_mcp_config(tmp_path / ".env")
    assert cfg["mcpServers"]["kgc"]["command"] == sys.executable


def test_write_private_creates_0600_and_tightens_a_wider_existing_file(tmp_path):
    out = tmp_path / "x.json"
    out.write_text("stale and much longer than the replacement text" * 3)
    out.chmod(0o644)
    write_private(out, "{}")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert out.read_text() == "{}"


def test_append_claude_md_creates_appends_and_is_idempotent(tmp_path):
    path = tmp_path / "CLAUDE.md"
    block = render_claude_md(["alpha"])
    assert append_claude_md(path, block) is True
    assert path.read_text() == block

    path.write_text("# Existing notes\n\nkeep me\n")
    assert append_claude_md(path, block) is True
    text = path.read_text()
    assert text.startswith("# Existing notes\n\nkeep me\n\n" + MARKER)
    assert append_claude_md(path, block) is False
    assert path.read_text() == text
