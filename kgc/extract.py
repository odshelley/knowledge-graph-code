# kgc/extract.py
"""Deterministic, zero-token code extraction for one repository.

graphify's default pipeline dispatches LLM subagents over documents, papers and
images. This module guarantees that never happens by emptying every non-code
category from the detect() result before extraction, which is graphify's own
documented code-only fast path: with no non-code files present the semantic
stage has nothing to do and only the AST pass runs.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from graphify.build import build_from_json
from graphify.cluster import cluster
from graphify.detect import detect
from graphify.extract import collect_files, extract
from networkx.readwrite import json_graph

from kgc.config import Repo

NON_CODE_CATEGORIES = ("document", "paper", "image", "video")


@dataclass(frozen=True)
class ExtractResult:
    tag: str
    nodes: int
    edges: int
    files: int
    seconds: float
    graph_path: Path


def code_only_detect(root: Path) -> dict:
    """detect() with every non-code category emptied.

    cache_root=root lets detect() reuse graphify's on-disk cache under
    <repo>/graphify-out/ across runs instead of re-sniffing file content
    every time (mirrors the cache_root already passed to extract() below).
    extra_excludes=["*.pdf"] stops detect() from attempting PDF text
    extraction on files we're about to discard anyway - without it, a repo
    with PDFs under a missing 'pypdf' install prints a stray WARNING line
    for work whose result is thrown away one line later. The category-
    zeroing below remains the actual correctness mechanism; this exclude is
    only a noise/waste fix.
    """
    result = detect(root, cache_root=root, extra_excludes=["*.pdf"])
    files = result.setdefault("files", {})
    for category in NON_CODE_CATEGORIES:
        files[category] = []
    result["total_files"] = len(files.get("code", []))
    return result


def extract_repo(repo: Repo) -> ExtractResult:
    """Extract one repo to <repo>/graphify-out/graph.json. No LLM, no tokens."""
    if not repo.path.exists():
        raise FileNotFoundError(f"repo path does not exist: {repo.path}")

    started = time.monotonic()
    detected = code_only_detect(repo.path)

    code_paths: list[Path] = []
    for entry in detected["files"].get("code", []):
        p = Path(entry)
        code_paths.extend(collect_files(p) if p.is_dir() else [p])

    out_dir = repo.path / "graphify-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    graph_path = out_dir / "graph.json"

    if not code_paths:
        extraction = {"nodes": [], "edges": [], "hyperedges": [],
                      "input_tokens": 0, "output_tokens": 0}
    else:
        extraction = extract(code_paths, cache_root=repo.path, root=repo.path)
        extraction.setdefault("hyperedges", [])

    # Assert the zero-token guarantee rather than trusting it.
    if extraction.get("input_tokens") or extraction.get("output_tokens"):
        raise AssertionError(
            f"{repo.tag}: extraction reported token spend "
            f"({extraction.get('input_tokens')} in / "
            f"{extraction.get('output_tokens')} out) - the semantic stage ran, "
            "which this pipeline forbids"
        )

    graph = build_from_json(extraction, root=str(repo.path))

    # Cluster and stamp 'community' on every node. graphify's MCP server
    # reconstructs communities from this attribute (get_community, god_nodes by
    # community) and global_add offsets it so repos' community ids stay apart.
    # Skipping this degrades both silently. Deliberately NOT graphify.export.to_json:
    # that carries the #479 shrink-guard, which would refuse to write a graph that
    # legitimately got smaller after code was deleted.
    communities = cluster(graph)
    for cid, members in communities.items():
        for nid in members:
            if nid in graph:
                graph.nodes[nid]["community"] = int(cid)

    # Write the graph.json shape global_add consumes (node-link with 'links').
    payload = json_graph.node_link_data(graph, edges="links")
    graph_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return ExtractResult(
        tag=repo.tag,
        nodes=graph.number_of_nodes(),
        edges=graph.number_of_edges(),
        files=len(code_paths),
        seconds=round(time.monotonic() - started, 2),
        graph_path=graph_path,
    )
