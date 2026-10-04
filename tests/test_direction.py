from kgc.direction import Reorientation, reorient_links


def _sample_links():
    return [
        # reversed, both markers are real node ids -> must be reoriented
        {"source": "b", "target": "a", "_src": "a", "_tgt": "b"},
        # already correct -> no change
        {"source": "c", "target": "d", "_src": "c", "_tgt": "d"},
        # dedup survivor: global_add rewired source/target onto the surviving
        # node but left _src pointing at the removed one. The one valid marker
        # (_tgt=d) names an endpoint, so the link is repairable: d stays the
        # target and the stale _src is repaired to the other endpoint, a.
        {"source": "a", "target": "d", "_src": "ghost", "_tgt": "d"},
    ]


def test_reorient_repairs_a_stale_marker_that_names_an_endpoint():
    node_ids = {"a", "b", "c", "d"}
    links = _sample_links()

    result = reorient_links(links, node_ids)

    assert result == Reorientation(changed=1, skipped=0, repaired=1)
    repaired = links[2]
    assert (repaired["source"], repaired["target"]) == ("a", "d")
    assert (repaired["_src"], repaired["_tgt"]) == ("a", "d")


def test_reorient_reorders_endpoints_so_the_valid_marker_keeps_its_role():
    # Raw order contradicts the surviving marker (1,343 of 1,343 real cases):
    # _tgt=d is valid but d sits in 'source'. d must become the target and
    # the stale _src must be repaired to the other endpoint.
    links = [{"source": "d", "target": "a", "_src": "ghost", "_tgt": "d"}]

    result = reorient_links(links, {"a", "d"})

    assert result == Reorientation(changed=1, skipped=0, repaired=1)
    (link,) = links
    assert (link["source"], link["target"]) == ("a", "d")
    assert (link["_src"], link["_tgt"]) == ("a", "d")


def test_reorient_repairs_a_stale_tgt_the_same_way():
    links = [{"source": "d", "target": "a", "_src": "a", "_tgt": "ghost"}]

    result = reorient_links(links, {"a", "d"})

    assert result == Reorientation(changed=1, skipped=0, repaired=1)
    (link,) = links
    assert (link["source"], link["target"]) == ("a", "d")
    assert (link["_src"], link["_tgt"]) == ("a", "d")


def test_reorient_skips_when_the_valid_marker_is_not_an_endpoint():
    # The one valid marker names a real node that is not on this link, so
    # there is no safe way to say which endpoint plays which role.
    links = [{"source": "a", "target": "d", "_src": "c", "_tgt": "ghost"}]

    result = reorient_links(links, {"a", "c", "d"})

    assert result == Reorientation(changed=0, skipped=1, repaired=0)
    assert links == [{"source": "a", "target": "d", "_src": "c", "_tgt": "ghost"}]


def test_reorient_skips_when_both_markers_are_stale():
    links = [{"source": "a", "target": "d", "_src": "ghost1", "_tgt": "ghost2"}]

    result = reorient_links(links, {"a", "d"})

    assert result == Reorientation(changed=0, skipped=1, repaired=0)
    assert (links[0]["_src"], links[0]["_tgt"]) == ("ghost1", "ghost2")


def test_reorient_is_idempotent():
    node_ids = {"a", "b", "c", "d"}
    links = _sample_links()
    reorient_links(links, node_ids)

    assert reorient_links(links, node_ids) == Reorientation(0, 0, 0)


def test_reorient_never_introduces_new_endpoints_or_markers():
    node_ids = {"a", "b", "c", "d"}
    links = _sample_links() + [
        {"source": "d", "target": "a", "_src": "ghost", "_tgt": "d"},
    ]

    reorient_links(links, node_ids)

    for link in links:
        assert link["source"] in node_ids
        assert link["target"] in node_ids
        assert link["_src"] in node_ids
        assert link["_tgt"] in node_ids
