# kgc/globalgraph.py
"""Merge per-repo graphs into graphify's global graph.

graphify owns the hard parts: global_add prefixes node IDs to "{tag}::{id}",
sets a 'repo' attribute on every repo-local node, leaves external stubs
unprefixed so stdlib/third-party modules unify across repos, prunes the repo's
stale nodes before merging, skips entirely when the source hash is unchanged,
and runs the cross-repo linking passes. This module adapts its summary and
adds the two post-merge passes graphify does not do: direction normalisation
(normalize_direction) and shared-stub re-tagging (retag_shared_stubs).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from graphify.global_graph import global_add, global_list, global_path

from kgc.direction import Reorientation, reorient_links
from kgc.extract import ExtractResult


@dataclass(frozen=True)
class MergeResult:
    tag: str
    nodes_added: int
    nodes_removed: int
    skipped: bool
    cross_repo_calls: int


def global_graph_path() -> Path:
    return global_path()


def merge_repo(result: ExtractResult) -> MergeResult:
    summary = global_add(result.graph_path, result.tag)
    return MergeResult(
        tag=summary.get("repo_tag", result.tag),
        nodes_added=int(summary.get("nodes_added", 0)),
        nodes_removed=int(summary.get("nodes_removed", 0)),
        skipped=bool(summary.get("skipped", False)),
        cross_repo_calls=int(summary.get("cross_repo_calls", 0)),
    )


def _load_json(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    links = data["links"] if "links" in data else data.get("edges", [])
    return data, links


def normalize_direction(path: Path) -> Reorientation:
    """Rewrite each link's source/target from its _src/_tgt markers in place.

    graphify's to_json does this (#563) but its global-graph writer and our
    extract writer do not, and graphify's `affected`/forced-directed loader
    trust the raw order. See kgc.direction for how a link whose marker went
    stale (a dedup survivor) is repaired or skipped. Writes back whenever a
    link or a marker changed. Idempotent.
    """
    data, links = _load_json(path)
    node_ids = {n["id"] for n in data.get("nodes", [])}
    result = reorient_links(links, node_ids)
    if result.changed or result.repaired:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return result


def retag_shared_stubs(path: Path) -> int:
    """Turn dedup survivors referenced from >= 2 repos into shared stubs.

    global_add dedups ANY sourceless node by label - its predicate is
    `not source_file`, not `external` - so a repo-local unresolved reference
    such as Exception, Path or parametrize collapses onto the first-merged
    repo's copy, which keeps that repo's tag. Semantically that node is a
    shared stub: it has no source file and other repos' edges point at it.
    Leaving it tagged costs twice: `kgc push --repo <first>` reaps it (and with
    it every other repo's edges onto it), and a Cypher query for cross-repo
    coupling counts those edges as real coupling between repos (1,239 of the
    1,343 such edges in the five-repo graph ran knowledge-retrieval -> graphify
    for this reason alone).

    Any node with no source_file and a repo whose incident edges come from two
    or more distinct repos gets external=True and loses its repo. Returns how
    many nodes were re-tagged; writes only when that is non-zero. Idempotent.
    """
    data, links = _load_json(path)
    nodes = data.get("nodes", [])
    repo_of = {n["id"]: n.get("repo") for n in nodes}

    neighbour_repos: dict[str, set[str]] = {}
    for link in links:
        u, v = link.get("source"), link.get("target")
        for node, other in ((u, v), (v, u)):
            repo = repo_of.get(other)
            if repo:
                neighbour_repos.setdefault(node, set()).add(repo)

    retagged = 0
    for node in nodes:
        if node.get("source_file") or not node.get("repo"):
            continue
        if len(neighbour_repos.get(node["id"], ())) >= 2:
            node["external"] = True
            del node["repo"]
            retagged += 1
    if retagged:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return retagged


def load_global_graph() -> nx.Graph:
    path = global_graph_path()
    if not path.exists():
        raise FileNotFoundError(f"no global graph at {path} - run 'kgc build' first")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "links" not in data and "edges" in data:
        data = dict(data, links=data["edges"])
    return json_graph.node_link_graph(data, edges="links")


def registered_repos() -> dict:
    return global_list()
