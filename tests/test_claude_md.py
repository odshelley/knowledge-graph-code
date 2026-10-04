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


# --- local checkouts block (machine-specific, CLAUDE.local.md) ---------------

from kgc.server.claude_md import LOCAL_MARKER, render_local_checkouts


def test_local_block_lists_every_tag_with_its_absolute_path():
    text = render_local_checkouts({"beta": "/src/beta", "alpha": "/src/alpha"})
    assert text.startswith(LOCAL_MARKER + "\n")
    assert "- `alpha` — `/src/alpha`" in text
    assert "- `beta` — `/src/beta`" in text
    # sorted, so the file is stable across runs
    assert text.index("`alpha`") < text.index("`beta`")
    assert text.endswith("\n")


def test_local_block_shows_how_to_turn_a_graph_hit_into_a_real_file():
    text = render_local_checkouts({"alpha": "/src/alpha"})
    assert "/src/alpha/some/dir/file.py" in text
    assert "never function bodies" in text


def test_local_block_example_never_claims_a_file_lives_in_the_wrong_repo():
    # The example names one tag; every concrete path in it must be that tag's.
    text = render_local_checkouts({"alpha": "/src/alpha", "beta": "/src/beta"})
    example = [ln for ln in text.splitlines() if ln.startswith("To open a hit")][0]
    assert "/src/alpha" in example and "/src/beta" not in example


def test_local_block_with_no_checkouts_says_so_and_names_the_fix():
    text = render_local_checkouts({})
    assert text.startswith(LOCAL_MARKER + "\n")
    assert "repos.yaml" in text and "kgc init" in text


def test_the_shared_block_never_carries_an_absolute_path():
    # The shared block goes into the tracked CLAUDE.md and into the harness's
    # kgc arm; a machine-specific path there would make those diverge.
    text = render_claude_md(["alpha", "beta"])
    assert "/" not in text.replace("callers` / `callees", "") or "Users" not in text
    assert LOCAL_MARKER not in text
