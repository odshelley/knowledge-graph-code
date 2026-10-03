from kgc.direction import reorient_links


def _sample_links():
    return [
        # reversed, both markers are real node ids -> must be reoriented
        {"source": "b", "target": "a", "_src": "a", "_tgt": "b"},
        # already correct -> no change
        {"source": "c", "target": "d", "_src": "c", "_tgt": "d"},
        # dedup survivor: source/target already correctly rewired by
        # global_add, but _src is stale (points at a removed node) -> must be
        # left exactly as-is, not reoriented onto the stale marker
        {"source": "a", "target": "d", "_src": "ghost", "_tgt": "d"},
    ]


def test_reorient_skips_links_whose_markers_are_not_nodes():
    node_ids = {"a", "b", "c", "d"}
    links = _sample_links()

    changed, skipped = reorient_links(links, node_ids)

    assert (changed, skipped) == (1, 1)
    stale = links[2]
    assert stale["source"] == "a"
    assert stale["target"] == "d"


def test_reorient_never_introduces_new_endpoints():
    node_ids = {"a", "b", "c", "d"}
    links = _sample_links()

    reorient_links(links, node_ids)

    for link in links:
        assert link["source"] in node_ids
        assert link["target"] in node_ids
