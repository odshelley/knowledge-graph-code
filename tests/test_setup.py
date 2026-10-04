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


# --- upsert_block: the regenerable local block ------------------------------

from kgc.server.setup import upsert_block


def test_upsert_block_creates_then_updates_in_place(tmp_path):
    path = tmp_path / "CLAUDE.local.md"
    assert upsert_block(path, "# m\n\nold rows\n") == "created"
    assert path.read_text() == "# m\n\nold rows\n"
    # regenerating must REPLACE, not append a second copy
    assert upsert_block(path, "# m\n\nnew rows\n") == "updated"
    text = path.read_text()
    assert text.count("# m") == 1 and "new rows" in text and "old rows" not in text


def test_upsert_block_preserves_the_users_own_sections(tmp_path):
    path = tmp_path / "CLAUDE.local.md"
    path.write_text("# mine\n\nkeep me\n\n# block\n\nstale\n\n# after\n\nkeep too\n")
    assert upsert_block(path, "# block\n\nfresh\n") == "updated"
    text = path.read_text()
    assert "keep me" in text and "keep too" in text
    assert "fresh" in text and "stale" not in text
    assert text.index("# mine") < text.index("# block") < text.index("# after")


def test_upsert_block_appends_when_the_marker_is_absent(tmp_path):
    path = tmp_path / "CLAUDE.local.md"
    path.write_text("# mine\n\nkeep me\n")
    assert upsert_block(path, "# m\n\nrows\n") == "appended"
    assert path.read_text() == "# mine\n\nkeep me\n\n# m\n\nrows\n"
