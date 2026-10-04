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
