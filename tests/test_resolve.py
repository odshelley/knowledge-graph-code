from kgc.server import queries as q
from kgc.server.resolve import Node, Resolution, resolve
from tests.fakes import FakeBackend, node_row


def test_exact_label_wins_and_returns_the_node():
    db = FakeBackend([(q.EXACT_LABEL, [node_row("a::f", "f()", source_file="a.py")])])
    res = resolve(db, "f")
    assert res.ok and res.node == Node("a::f", "f()", "alpha", "a.py", "L1", 0, False)
    assert db.queries() == [q.EXACT_LABEL]
    assert db.calls[0][1] == {"label": "f", "repo": None, "limit": q.DEFAULT_LIMIT}


def test_repo_filter_is_passed_to_every_tier():
    db = FakeBackend([(q.CI_LABEL, [node_row("b::g", "G", repo="beta")])])
    res = resolve(db, "g", repo="beta")
    assert res.ok and res.node.repo == "beta"
    assert all(params["repo"] == "beta" for _, params in db.calls)


def test_path_scoped_symbol_uses_the_path_tier_first():
    db = FakeBackend([(q.PATH_SCOPED, [node_row("a::f", "f()", source_file="pkg/a.py")])])
    res = resolve(db, "pkg/a.py::f")
    assert res.ok
    assert db.queries()[0] == q.PATH_SCOPED
    assert db.calls[0][1]["path"] == "pkg/a.py" and db.calls[0][1]["label"] == "f"


def test_a_bare_path_resolves_to_the_file_node():
    db = FakeBackend([(q.FILE_NODE, [node_row("a::a", "a.py", source_file="pkg/a.py")])])
    res = resolve(db, "pkg/a.py")
    assert res.ok and res.node.label == "a.py"
    assert db.queries()[0] == q.FILE_NODE


def test_same_label_in_two_files_is_ambiguous_with_one_candidate_per_file():
    rows = [node_row("a::f1", "f()", source_file="a.py"),
            node_row("a::f2", "f()", source_file="a.py", source_location="L9"),
            node_row("b::f", "f()", repo="beta", source_file="b.py")]
    db = FakeBackend([(q.EXACT_LABEL, rows)])
    res = resolve(db, "f")
    assert not res.ok and res.node is None
    assert [c.id for c in res.candidates] == ["a::f1", "b::f"]


def test_same_label_twice_in_one_file_is_not_ambiguous():
    rows = [node_row("a::f1", "f()", source_file="a.py"),
            node_row("a::f2", "f()", source_file="a.py", source_location="L9")]
    db = FakeBackend([(q.EXACT_LABEL, rows)])
    res = resolve(db, "f")
    assert res.ok and res.node.id == "a::f1"


def test_tiers_run_in_order_until_one_hits():
    db = FakeBackend([(q.CI_LABEL, [node_row("a::f", "F()")])])
    res = resolve(db, "f")
    assert res.ok
    assert db.queries() == [q.EXACT_LABEL, q.CI_LABEL]


def test_fulltext_fallback_resolves_a_clear_winner():
    hits = [dict(node_row("a::f", "frobnicate()"), score=4.0),
            dict(node_row("a::g", "frob_helper()"), score=1.5)]
    db = FakeBackend([(q.SEARCH, hits)])
    res = resolve(db, "frobnicate")
    assert res.ok and res.node.id == "a::f"
    assert db.queries() == [q.EXACT_LABEL, q.CI_LABEL, q.SEARCH]
    assert db.calls[-1][1]["q"] == "frobnicate"
    assert db.calls[-1][1]["limit"] == 5


def test_fulltext_fallback_without_a_clear_winner_reports_nearest_three():
    hits = [dict(node_row(f"a::{i}", f"thing{i}()"), score=3.0 - 0.1 * i) for i in range(5)]
    db = FakeBackend([(q.SEARCH, hits)])
    res = resolve(db, "thing")
    assert not res.ok and res.candidates == ()
    assert [n.id for n in res.nearest] == ["a::0", "a::1", "a::2"]


def test_no_hits_anywhere_is_an_empty_resolution():
    res = resolve(FakeBackend(), "nothing_here")
    assert res == Resolution()
    assert not res.ok


def test_unsearchable_symbol_skips_the_fulltext_call():
    db = FakeBackend()
    resolve(db, "*")
    assert q.SEARCH not in db.queries()


def test_node_round_trips_through_as_row():
    row = node_row("a::f", "f()", community=3)
    assert Node.from_row(row).as_row() == row
