# kgc/direction.py
"""Leaf module: reorient graph.json links from their _src/_tgt markers.

graphify stores true edge direction in _src/_tgt but several of its own
consumers (serve._load_graph, affected.py) trust the raw source/target order
instead. graphify's export.to_json compensates (#563); the global graph
writer and our extract writer do not.

Markers can go stale: global_add's node dedup (e.g. unifying every repo's
reference to the builtin Exception) rewires an incident edge's source/target
to the surviving node but does not update that edge's _src/_tgt. Reorienting
onto a stale marker would materialise a phantom, attribute-less node on the
next load - so a link is only reoriented when BOTH markers name a real node
id; otherwise it is left exactly as graphify wrote it and counted skipped.

Imports nothing from kgc, so both kgc/extract.py and kgc/globalgraph.py can
import this without creating a cycle between them.
"""
from __future__ import annotations


def reorient_links(links: list[dict], node_ids: set[str]) -> tuple[int, int]:
    """Rewrite source/target from _src/_tgt wherever both markers are valid.

    Keeps the markers (the Neo4j push reads them). Returns (changed, skipped):
    changed is the number of links whose source/target were rewritten,
    skipped is the number left untouched because a marker was stale.
    Idempotent - a second call changes nothing.
    """
    changed = skipped = 0
    for link in links:
        src, tgt = link.get("_src"), link.get("_tgt")
        if src is None or tgt is None:
            continue
        if src not in node_ids or tgt not in node_ids:
            skipped += 1
            continue
        if (link.get("source"), link.get("target")) != (src, tgt):
            link["source"] = src
            link["target"] = tgt
            changed += 1
    return changed, skipped
