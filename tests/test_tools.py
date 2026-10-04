from kgc.server import queries as q
from kgc.server import tools
from kgc.server.guard import REFUSED
from tests.fakes import FakeBackend, node_row

KR = "knowledge-retrieval"
PUSH_ONE = node_row("kr::push_one", "push_one()", repo=KR,
                    source_file="pipeline/zotero/push.py", source_location="L61", community=7)


def _exact(row, label="push_one"):
    # key on the label too, or resolving *any* symbol would hit this row
    return (lambda qq, p: qq == q.EXACT_LABEL and p.get("label") == label, [row])


# --- names and descriptions ----------------------------------------------------

def test_the_nine_tool_names_in_spec_order():
    assert tools.TOOL_NAMES == ("search", "describe", "callers", "callees", "impact",
                                "externals", "cross_repo", "stats", "cypher")
    assert set(tools.DESCRIPTIONS) == set(tools.TOOL_NAMES)
    assert all(len(d) > 40 for d in tools.DESCRIPTIONS.values())


def test_schema_notes_state_the_direction_convention_and_the_labels():
    for needle in ("CodeNode", "Repo", "caller", "callee", "source_file <> ''",
                   "rationale_for", "imports_from", "external"):
        assert needle in tools.SCHEMA_NOTES
    assert "prefer the specific tools" in tools.DESCRIPTIONS["cypher"]


# --- search ---------------------------------------------------------------------

def test_search_lists_ranked_nodes_one_per_line():
    rows = [dict(PUSH_ONE, score=3.2), dict(node_row("numpy", "numpy", repo=None, external=True), score=1.0)]
    db = FakeBackend([(q.SEARCH, rows)])
    out = tools.search(db, "push one", limit=5)
    assert out.splitlines() == [
        "knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c7",
        "external  numpy",
    ]
    assert db.calls[0][1] == {"q": '"push one"^2 OR push OR one', "repo": None, "limit": 5}


def test_search_reports_no_matches_and_names_the_repo_filter():
    assert tools.search(FakeBackend(), "zzz", repo="graphify") == "no matches for 'zzz' in repo graphify"
    assert tools.search(FakeBackend(), "zzz") == "no matches for 'zzz'"


def test_search_with_nothing_searchable_does_not_hit_the_database():
    db = FakeBackend()
    assert tools.search(db, "***") == "no searchable words in '***'"
    assert db.calls == []


def test_search_honours_max_tokens():
    rows = [dict(node_row(f"a::{i}", f"fn{i}()" + "x" * 40), score=1.0) for i in range(40)]
    out = tools.search(FakeBackend([(q.SEARCH, rows)]), "fn", limit=40, max_tokens=50)
    assert out.startswith("[truncated: showing ")
    assert out.endswith("… truncated; narrow by repo or reduce depth")


# --- describe -------------------------------------------------------------------

def test_describe_shows_node_docs_and_edge_counts():
    db = FakeBackend([
        _exact(PUSH_ONE),
        (q.DESCRIBE_NODE, [dict(PUSH_ONE, file_type="code",
                                docs=["File one paper or book into Zotero."])]),
        (q.EDGE_COUNTS, [{"direction": "out", "rel": "calls", "n": 6},
                         {"direction": "in", "rel": "calls", "n": 2},
                         {"direction": "in", "rel": "imports_from", "n": 1}]),
    ])
    assert tools.describe(db, "push_one").splitlines() == [
        "knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c7",
        "type: code",
        "doc: File one paper or book into Zotero.",
        "in:  calls 2, imports_from 1",
        "out: calls 6",
    ]


def test_describe_unresolved_lists_nearest_hits():
    hits = [dict(node_row("a::1", "pushy()"), score=1.0), dict(node_row("a::2", "pusher()"), score=0.9)]
    out = tools.describe(FakeBackend([(q.SEARCH, hits)]), "push_oen")
    assert out.splitlines()[0] == "no node matches 'push_oen'"
    assert out.splitlines()[1] == "nearest:"
    assert "pushy()" in out and "pusher()" in out


def test_describe_ambiguous_lists_candidates_and_the_retry_hint():
    rows = [node_row("a::f", "f()", source_file="a.py"), node_row("b::f", "f()", repo="beta", source_file="b.py")]
    out = tools.describe(FakeBackend([(q.EXACT_LABEL, rows)]), "f")
    lines = out.splitlines()
    assert lines[0] == "ambiguous: 'f' matches 2 nodes in different files; retry as path::symbol or pass repo="
    assert lines[1:] == ["alpha  f()  a.py:L1  c0", "beta  f()  b.py:L1  c0"]


def test_describe_with_no_edges_says_none():
    db = FakeBackend([_exact(PUSH_ONE), (q.DESCRIBE_NODE, [dict(PUSH_ONE, file_type="code", docs=[])])])
    out = tools.describe(db, "push_one")
    assert "in:  none" in out and "out: none" in out


# --- callers / callees ----------------------------------------------------------

def _neighbour(id, label, repo, hops=1, via="calls", via_location="x.py:L5", **kw):
    return dict(node_row(id, label, repo=repo, **kw), hops=hops, via=via, via_location=via_location)


def test_callers_groups_by_repo_and_marks_multi_hop():
    rows = [
        _neighbour("kr::asset", "zotero_push_asset()", KR, source_file="pipeline/assets/z.py"),
        _neighbour("kr::backfill", "main()", KR, hops=2, via="imports_from", source_file="scripts/backfill.py"),
        _neighbour("g::x", "x()", "graphify", source_file="g.py"),
    ]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "*1..2]->(t:CodeNode {id: $id})" in qq, rows)])
    out = tools.callers(db, "push_one", depth=2)
    assert out.splitlines() == [
        "callers of knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c7:",
        "[graphify] 1",
        "x() [graphify]  -calls->  push_one() [knowledge-retrieval]  (x.py:L5)",
        "[knowledge-retrieval] 2",
        "zotero_push_asset() [knowledge-retrieval]  -calls->  push_one() [knowledge-retrieval]  (x.py:L5)",
        "main() [knowledge-retrieval]  -imports_from->  push_one() [knowledge-retrieval]  (x.py:L5)  hops=2",
    ]
    _, params = db.calls[-1]
    assert params == {"id": "kr::push_one", "repo": None, "limit": q.DEFAULT_LIMIT}


def test_callees_points_the_arrow_away_from_the_target_and_omits_inherits():
    rows = [_neighbour("kr::create_items", "create_items()", KR, source_file="pipeline/zotero/client.py")]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "(t:CodeNode {id: $id})-[:" in qq, rows)])
    out = tools.callees(db, "push_one")
    assert out.splitlines()[-1] == (
        "push_one() [knowledge-retrieval]  -calls->  create_items() [knowledge-retrieval]  (x.py:L5)"
    )
    assert "inherits" not in db.queries()[-1]


def test_callers_with_none_says_so_with_the_clamped_depth():
    db = FakeBackend([_exact(PUSH_ONE)])
    assert tools.callers(db, "push_one", depth=9) == "no callers of push_one() within depth 4"
    assert tools.callers(db, "push_one", repo="graphify") == "no callers of push_one() within depth 1 in repo graphify"


def test_callers_propagates_resolution_failures():
    assert tools.callers(FakeBackend(), "ghost") == "no node matches 'ghost'"


def test_callers_with_repo_resolves_within_that_repo_first():
    alpha_row = node_row("a::f", "f()", repo="alpha", source_file="a.py")
    beta_row = node_row("b::f", "f()", repo="beta", source_file="b.py")

    def matcher(qq, p):
        return qq == q.EXACT_LABEL and p.get("label") == "f"

    def rows(p):
        if p.get("repo") == "alpha":
            return [alpha_row]
        if p.get("repo") is None:
            return [alpha_row, beta_row]
        return []

    db = FakeBackend([(matcher, rows)])
    out = tools.callers(db, "f", repo="alpha")
    assert "ambiguous" not in out and "no node matches" not in out
    assert db.calls[-1][1]["repo"] == "alpha"


def test_callers_with_repo_falls_back_to_global_resolution_when_the_repo_has_no_match():
    row = node_row("a::f", "f()", repo="alpha", source_file="a.py")

    def matcher(qq, p):
        return qq == q.EXACT_LABEL and p.get("label") == "f" and p.get("repo") is None

    db = FakeBackend([(matcher, [row])])
    out = tools.callers(db, "f", repo="beta")
    assert "ambiguous" not in out and "no node matches" not in out
    resolve_calls = [p for qq, p in db.calls if qq == q.EXACT_LABEL]
    assert resolve_calls[0]["repo"] == "beta"
    assert resolve_calls[-1]["repo"] is None
    assert db.calls[-1][1]["repo"] == "beta"


# --- impact ---------------------------------------------------------------------

def test_impact_groups_dependents_by_repo_with_samples():
    rows = [{"repo": "graphify", "n": 2, "sample": [
                {"label": "a()", "source_file": "a.py", "source_location": "L1", "hops": 1},
                {"label": "b()", "source_file": "b.py", "source_location": "L2", "hops": 2}]},
            {"repo": KR, "n": 1, "sample": [
                {"label": "c()", "source_file": "c.py", "source_location": "L3", "hops": 1}]}]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "t.id IN $ids" in qq, rows)])
    out = tools.impact(db, symbols=["push_one"], files=["pipeline/zotero/items.py"], depth=3)
    assert out.splitlines() == [
        "[graphify] 2 dependents",
        "  a()  a.py:L1  hops=1",
        "  b()  b.py:L2  hops=2",
        "[knowledge-retrieval] 1 dependents",
        "  c()  c.py:L3  hops=1",
    ]
    assert db.calls[-1][1] == {"ids": ["kr::push_one"], "files": ["pipeline/zotero/items.py"]}
    assert "*1..3]->(t)" in db.queries()[-1]


def test_impact_with_only_files_skips_resolution():
    db = FakeBackend([(lambda qq, p: "t.id IN $ids" in qq, [])])
    out = tools.impact(db, files=["x.py"])
    assert out == "nothing depends on x.py within depth 2"
    assert db.calls[-1][1] == {"ids": [], "files": ["x.py"]}


def test_impact_reports_unresolved_symbols_and_still_runs_for_the_rest():
    rows = [{"repo": KR, "n": 1, "sample": [{"label": "c()", "source_file": "c.py", "source_location": "L3", "hops": 1}]}]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "t.id IN $ids" in qq, rows)])
    out = tools.impact(db, symbols=["ghost", "push_one"])
    assert out.splitlines()[0] == "no node matches 'ghost'"
    assert "[knowledge-retrieval] 1 dependents" in out


def test_impact_with_nothing_to_look_up():
    assert tools.impact(FakeBackend()) == "give at least one symbol or file"


def test_impact_with_only_unresolved_symbols_honours_max_tokens():
    symbols = [f"ghost{i}" for i in range(40)]
    out = tools.impact(FakeBackend(), symbols=symbols, max_tokens=20)
    assert out.startswith("[truncated: showing ")
    assert out.endswith("… truncated; narrow by repo or reduce depth")


# --- externals / cross_repo ------------------------------------------------------

def test_externals_lists_packages_with_their_repos():
    rows = [{"package": "numpy", "repos": ["graphify", KR, "diffusion-bridge"]},
            {"package": "dagster", "repos": [KR]}]
    db = FakeBackend([(q.EXTERNALS, rows)])
    out = tools.externals(db, min_repos=1)
    assert out.splitlines() == [
        "numpy  repos: diffusion-bridge, graphify, knowledge-retrieval",
        "dagster  repos: knowledge-retrieval",
    ]
    assert db.calls[0][1]["min_repos"] == 1 and db.calls[0][1]["repo"] is None


def test_externals_and_cross_repo_say_none_when_empty():
    assert tools.externals(FakeBackend(), min_repos=3) == "none"
    assert tools.cross_repo(FakeBackend(), "claude", "graphify") == "none"


def test_cross_repo_lists_edge_groups_with_samples():
    rows = [{"from_repo": KR, "to_repo": "graphify", "rel": "calls", "n": 3,
             "sample": [{"from": "a()", "to": "b()", "source_file": "a.py", "source_location": "L4"}]}]
    db = FakeBackend([(q.CROSS_REPO, rows)])
    out = tools.cross_repo(db, KR)
    assert out.splitlines() == [
        "knowledge-retrieval -calls-> graphify: 3",
        "  a() -> b()  (a.py:L4)",
    ]
    assert db.calls[0][1] == {"a": KR, "b": None}


# --- stats ----------------------------------------------------------------------

def test_stats_reports_each_repo_with_meta_relationships_and_blind_spots():
    db = FakeBackend([
        (q.REPO_META, [{"tag": "alpha", "path": "/src/alpha", "pushed_at": "2026-10-04T10:00:00+00:00",
                        "built_at_commit": None, "node_count": 10, "edge_count": 20,
                        "skipped_extensions": '{".q": 3, ".sas": 1}'}]),
        (q.NODE_COUNTS, [{"repo": "alpha", "n": 10}, {"repo": "beta", "n": 4}]),
        (q.REL_COUNTS, [{"repo": "alpha", "rel": "calls", "n": 12}, {"repo": "alpha", "rel": "imports", "n": 8}]),
        (q.EXTERNAL_COUNT, [{"n": 99}]),
    ])
    assert tools.stats(db).splitlines() == [
        "[alpha] 10 nodes, 20 edges, pushed 2026-10-04T10:00:00+00:00, commit unknown",
        "  skipped extensions: .q (3), .sas (1)",
        "  relationships: calls 12, imports 8",
        "[beta] 4 nodes  last push incomplete",
        "  relationships: none",
        "externals: 99",
    ]


def test_stats_says_not_recorded_before_plan_2_and_filters_by_repo():
    db = FakeBackend([
        (q.REPO_META, [{"tag": "alpha", "path": "", "pushed_at": "t", "built_at_commit": "abc123",
                        "node_count": 1, "edge_count": 1, "skipped_extensions": "null"}]),
        (q.NODE_COUNTS, [{"repo": "alpha", "n": 1}]),
        (q.REL_COUNTS, []),
        (q.EXTERNAL_COUNT, [{"n": 0}]),
    ])
    out = tools.stats(db, repo="alpha")
    assert "commit abc123" in out
    assert "  skipped extensions: not recorded" in out
    assert "externals:" not in out                      # per-repo view omits the global count
    assert db.calls[1][1] == {"repo": "alpha"}


def test_stats_for_an_unknown_repo():
    db = FakeBackend([(q.REPO_META, []), (q.NODE_COUNTS, [])])
    assert tools.stats(db, repo="nope") == "no repo 'nope' indexed"


# --- cypher ---------------------------------------------------------------------

def test_cypher_refuses_writes_before_touching_the_database():
    db = FakeBackend()
    assert tools.cypher(db, "MATCH (n) DETACH DELETE n") == REFUSED
    assert db.calls == []


def test_cypher_renders_rows_as_a_tab_table_and_json_encodes_nested_values():
    rows = [{"repo": KR, "n": 3, "sample": ["a", "b"]}, {"repo": "graphify", "n": 1, "sample": []}]
    db = FakeBackend([("RETURN", rows)])
    out = tools.cypher(db, "MATCH (n) RETURN n.repo AS repo, count(*) AS n, collect(n.label)[0..2] AS sample")
    assert out.splitlines() == [
        "repo\tn\tsample",
        'knowledge-retrieval\t3\t["a", "b"]',
        "graphify\t1\t[]",
    ]


def test_cypher_says_no_rows_and_uses_a_query_specific_hint_when_truncating():
    assert tools.cypher(FakeBackend(), "MATCH (n) RETURN n LIMIT 0") == "no rows"
    rows = [{"x": "y" * 200} for _ in range(30)]
    out = tools.cypher(FakeBackend([("RETURN", rows)]), "MATCH (n) RETURN n.x AS x", max_tokens=20)
    assert out.endswith("… truncated; add LIMIT or RETURN fewer columns")
