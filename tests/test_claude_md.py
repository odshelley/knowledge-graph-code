from kgc.server.claude_md import MARKER, render_claude_md
from kgc.server.tools import TOOL_NAMES


def test_rendered_block_starts_with_the_marker_and_lists_repos_and_tools():
    text = render_claude_md(["alpha", "beta"])
    assert text.startswith(MARKER + "\n")
    assert "`alpha`, `beta`" in text
    for name in TOOL_NAMES:
        assert f"`{name}`" in text
    assert "BEFORE reading files or grepping" in text
    assert "dependency injection" in text          # the blind-spot sentence
    assert text.endswith("\n")


def test_rendered_block_with_no_repos_still_reads_sensibly():
    text = render_claude_md([])
    assert "(none pushed yet)" in text


from pathlib import Path

from kgc.config import load_repos

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_project_claude_md_is_exactly_the_rendered_block():
    tags = [r.tag for r in load_repos(PROJECT_ROOT / "repos.yaml")]
    assert (PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8") == render_claude_md(tags)


def test_treatment_arm_keeps_the_pre_kgc_instructions_verbatim():
    text = (PROJECT_ROOT / "verify" / "arms" / "treatment" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "read_neo4j_cypher" in text and "query_graph" in text
    assert MARKER not in text
