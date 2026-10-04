"""Read-only checks against the real instance. Opt in with KGC_INTEGRATION=1.

Asserts shape, not content: the graph changes with every push. Nothing here
writes; the Backend only ever opens read transactions.
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KGC_INTEGRATION"),
    reason="set KGC_INTEGRATION=1 to run read-only against the real Neo4j instance",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOS = {"knowledge-retrieval", "graphify", "neural-particle-method", "diffusion-bridge", "claude"}


@pytest.fixture(scope="module")
def db():
    from kgc.config import load_settings
    from kgc.server.db import Backend

    backend = Backend(load_settings(PROJECT_ROOT / ".env"))
    yield backend
    backend.close()


def test_stats_lists_the_pushed_repos(db):
    from kgc.server import tools
    out = tools.stats(db)
    assert "[knowledge-retrieval]" in out
    assert "externals:" in out


def test_search_finds_push_one_by_words(db):
    from kgc.server import tools
    assert "push_one()" in tools.search(db, "push one zotero", repo="knowledge-retrieval")


def test_describe_resolves_a_path_scoped_symbol(db):
    from kgc.server import tools
    out = tools.describe(db, "pipeline/zotero/push.py::push_one")
    assert out.startswith("knowledge-retrieval  push_one()  pipeline/zotero/push.py:")
    assert "out: " in out


def test_callees_of_push_one_are_edges(db):
    from kgc.server import tools
    out = tools.callees(db, "pipeline/zotero/push.py::push_one")
    assert out.startswith("callees of ") and "-calls->" in out


def test_callers_of_build_from_json_report_or_say_none(db):
    from kgc.server import tools
    out = tools.callers(db, "graphify/build.py::build_from_json", depth=1)
    assert out.startswith("callers of ") or out.startswith("no callers of ")


def test_externals_shared_by_three_repos(db):
    from kgc.server import tools
    out = tools.externals(db, min_repos=3)
    assert out == "none" or all("repos:" in line for line in out.splitlines())


def test_cross_repo_claude_graphify_is_none(db):
    from kgc.server import tools
    assert tools.cross_repo(db, "claude", "graphify") == "none"


def test_impact_by_file(db):
    from kgc.server import tools
    out = tools.impact(db, files=["pipeline/zotero/push.py"])
    assert "dependents" in out or out.startswith("nothing depends on")


def test_cypher_reads_and_refuses_writes(db):
    from kgc.server import tools
    out = tools.cypher(db, "MATCH (n:CodeNode) RETURN n.repo AS repo, count(*) AS n ORDER BY n DESC LIMIT 3")
    assert out.splitlines()[0] == "repo\tn"
    assert tools.cypher(db, "MATCH (n) SET n.x = 1") == "refused: read-only server"


def test_server_builds_with_the_live_tags(db):
    from kgc.server.app import build_server, startup_repo_tags
    tags = startup_repo_tags(db)
    assert set(tags) <= REPOS
    build_server(db, repo_tags=tags)
