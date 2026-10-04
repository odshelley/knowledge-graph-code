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
next load, so a link is handled by how many of its markers name real nodes:

- both valid: source/target are rewritten from the markers;
- exactly one valid, and it names one of the link's own endpoints: that
  endpoint keeps the marker's role (source for _src, target for _tgt), the
  other endpoint takes the other role, and the stale marker is REPAIRED to
  that other endpoint. This is the dedup-survivor case - 1,343 links in the
  five-repo graph, every one with its raw order contradicting the surviving
  marker - and it leaves the markers self-consistent, so the Neo4j push,
  which reads them, stores the right direction;
- otherwise (one valid marker that is not an endpoint, or neither valid):
  the link is left exactly as graphify wrote it and counted skipped.

Only ids already present as endpoints are ever written into
source/target/_src/_tgt, so no pass here can introduce a node id that is
not in the graph.

Imports nothing from kgc, so both kgc/extract.py and kgc/globalgraph.py can
import this without creating a cycle between them.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reorientation:
    """Counts from one reorient_links pass.

    changed:  links whose source/target were rewritten.
    skipped:  links left untouched because their markers could not be trusted.
    repaired: links whose stale marker was rewritten to a real endpoint. Such a
              link is also counted in `changed` if its raw order had to move.
    """
    changed: int = 0
    skipped: int = 0
    repaired: int = 0


def reorient_links(links: list[dict], node_ids: set[str]) -> Reorientation:
    """Rewrite source/target (and repair a lone stale marker) in place.

    Keeps the markers (the Neo4j push reads them). Idempotent - a second call
    changes nothing.
    """
    changed = skipped = repaired = 0
    for link in links:
        src, tgt = link.get("_src"), link.get("_tgt")
        if src is None or tgt is None:
            continue
        src_ok, tgt_ok = src in node_ids, tgt in node_ids
        if src_ok and tgt_ok:
            want_src, want_tgt = src, tgt
        else:
            endpoints = (link.get("source"), link.get("target"))
            if src_ok and src in endpoints:
                other = endpoints[1] if endpoints[0] == src else endpoints[0]
                want_src, want_tgt = src, other
                link["_tgt"] = other
            elif tgt_ok and tgt in endpoints:
                other = endpoints[1] if endpoints[0] == tgt else endpoints[0]
                want_src, want_tgt = other, tgt
                link["_src"] = other
            else:
                skipped += 1
                continue
            repaired += 1
        if (link.get("source"), link.get("target")) != (want_src, want_tgt):
            link["source"] = want_src
            link["target"] = want_tgt
            changed += 1
    return Reorientation(changed=changed, skipped=skipped, repaired=repaired)
