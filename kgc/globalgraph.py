# kgc/globalgraph.py
"""Merge per-repo graphs into graphify's global graph.

graphify owns the hard parts: global_add prefixes node IDs to "{tag}::{id}",
sets a 'repo' attribute on every repo-local node, leaves external stubs
unprefixed so stdlib/third-party modules unify across repos, prunes the repo's
stale nodes before merging, skips entirely when the source hash is unchanged,
and runs the cross-repo linking passes. This module only adapts its summary.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
from networkx.readwrite import json_graph

from graphify.global_graph import global_add, global_list, global_path

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
