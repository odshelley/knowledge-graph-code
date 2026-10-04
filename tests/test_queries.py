import pytest

from kgc.server import queries as q


def test_whitelists_and_caps():
    assert q.DEPENDENCY_RELS == ("calls", "indirect_call", "imports", "imports_from",
                                 "references", "uses", "inherits", "extends", "implements",
                                 "re_exports", "dynamic_import")
    assert q.CALLEE_RELS == ("calls", "indirect_call", "imports", "imports_from", "references",
                             "uses", "re_exports", "dynamic_import")
    assert q.MAX_DEPTH == 4 and q.DEFAULT_LIMIT == 50
    assert q.clamp_depth(9) == 4 and q.clamp_depth(0) == 1 and q.clamp_depth(2) == 2


def test_rel_pattern_only_interpolates_whitelisted_types():
    assert q.rel_pattern(("calls", "imports")) == "calls|imports"
    with pytest.raises(ValueError, match="unknown relationship type"):
        q.rel_pattern(("calls", "DETACH DELETE"))


def test_neighbours_in_points_at_the_target_and_clamps_depth():
    text = q.neighbours("in", q.DEPENDENCY_RELS, 9)
    assert ("(s:CodeNode)-[:calls|indirect_call|imports|imports_from|references|uses|inherits"
           "|extends|implements|re_exports|dynamic_import*1..4]->(t:CodeNode {id: $id})") in text
    assert "s.id <> $id" in text
    assert "$repo IS NULL OR s.repo = $repo" in text
    assert "type(relationships(p)[0]) AS via" in text
    assert "LIMIT $limit" in text


def test_neighbours_out_starts_at_the_target_and_uses_callee_rels():
    text = q.neighbours("out", q.CALLEE_RELS, 1)
    assert ("(t:CodeNode {id: $id})-[:calls|indirect_call|imports|imports_from|references|uses"
           "|re_exports|dynamic_import*1..1]->(s:CodeNode)") in text
    assert "inherits" not in text and "extends" not in text and "implements" not in text


def test_neighbours_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        q.neighbours("sideways", q.CALLEE_RELS, 1)


def test_impact_takes_ids_and_files_and_groups_by_repo():
    text = q.impact(7)
    assert "t.id IN $ids" in text
    assert "any(f IN $files WHERE t.source_file = f OR t.source_file ENDS WITH '/' + f)" in text
    assert "*1..4]->(t)" in text
    assert "s.repo IS NOT NULL" in text
    assert "[0..20] AS sample" in text


def test_search_uses_the_fulltext_index_and_follows_rationale_edges():
    assert "db.index.fulltext.queryNodes('kgc_text', $q)" in q.SEARCH
    assert "CASE WHEN node.external = true THEN 0.5 ELSE 1.0 END" in q.SEARCH
    assert "(node)-[:rationale_for]->(code:CodeNode)" in q.SEARCH
    assert "coalesce(code, node)" in q.SEARCH


def test_every_repo_scoped_query_guards_with_repo_is_null():
    for text in (q.SEARCH, q.EXACT_LABEL, q.PATH_SCOPED, q.FILE_NODE, q.CI_LABEL,
                 q.EXTERNALS, q.NODE_COUNTS, q.REL_COUNTS):
        assert "$repo IS NULL OR" in text, text


def test_cross_repo_filters_both_ends_to_sourced_nodes_in_different_repos():
    assert "a.source_file <> '' AND b.source_file <> ''" in q.CROSS_REPO
    assert "a.repo <> b.repo" in q.CROSS_REPO
    assert "$a IN [a.repo, b.repo]" in q.CROSS_REPO
    assert "$b IS NULL OR $b IN [a.repo, b.repo]" in q.CROSS_REPO


def test_externals_groups_by_top_level_package_and_min_repos():
    assert "split(e.label, '.')[0] AS package" in q.EXTERNALS
    assert "size(repos) >= $min_repos" in q.EXTERNALS
    assert "[:imports|imports_from]->(e:CodeNode)" in q.EXTERNALS


def test_node_return_carries_every_field_node_line_needs():
    for field in ("id", "label", "repo", "source_file", "source_location", "community", "external"):
        assert f"AS {field}" in q.NODE_RETURN
