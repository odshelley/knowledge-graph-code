# kgc/extract.py
"""Deterministic, zero-token code extraction for one repository.

graphify's default pipeline dispatches LLM subagents over documents, papers and
images. This module guarantees that never happens by emptying every non-code
category from the detect() result before extraction, which is graphify's own
documented code-only fast path: with no non-code files present the semantic
stage has nothing to do and only the AST pass runs.

Nothing is written into the analysed repository. Each repo's graph.json and
graphify's extraction cache land under ~/.graphify/repos/<tag>/graphify-out/,
beside graphify's own global graph - see repo_output_dir for why.
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
from graphify.global_graph import global_path
from networkx.readwrite import json_graph

from kgc.config import Repo
from kgc.direction import reorient_links

NON_CODE_CATEGORIES = ("document", "paper", "image", "video")


@dataclass(frozen=True)
class ExtractResult:
    tag: str
    nodes: int
    edges: int
    files: int
    seconds: float
    graph_path: Path


def repo_output_dir(tag: str) -> Path:
    """Where one repo's extraction lands: ~/.graphify/repos/<tag>/graphify-out.

    Beside graphify's global graph (global_path()), never inside the analysed
    repo. Writing <repo>/graphify-out/ there dirtied five target checkouts
    and, worse for the verification harness, handed the control arm a
    ready-made graph in its cwd - the user's global graphify skill tells
    Claude to treat any question as a graphify query when graphify-out/
    exists, which would contaminate the control arm.
    """
    return global_path().parent / "repos" / tag / "graphify-out"


def code_only_detect(root: Path, cache_root: Path | None = None) -> dict:
    """detect() with every non-code category emptied.

    cache_root is where graphify keeps detect()'s word-count stat index and
    converted/ sidecars - under <cache_root>/graphify-out/ - so passing the
    per-repo output dir's parent keeps that cache out of the analysed tree
    and reuses it across runs instead of re-sniffing file content every time
    (mirrors the cache_root passed to extract() in extract_repo).
    extra_excludes=["*.pdf"] stops detect() from attempting PDF text
    extraction on files we're about to discard anyway - without it, a repo
    with PDFs under a missing 'pypdf' install prints a stray WARNING line
    for work whose result is thrown away one line later. The category-
    zeroing below remains the actual correctness mechanism; this exclude is
    only a noise/waste fix.
    """
    result = detect(root, cache_root=cache_root, extra_excludes=["*.pdf"])
    files = result.setdefault("files", {})
    for category in NON_CODE_CATEGORIES:
        files[category] = []
    result["total_files"] = len(files.get("code", []))
    return result


def extract_repo(repo: Repo) -> ExtractResult:
    """Extract one repo to ~/.graphify/repos/<tag>/graphify-out/graph.json.

    No LLM, no tokens, and nothing written under repo.path.
    """
    if not repo.path.exists():
        raise FileNotFoundError(f"repo path does not exist: {repo.path}")

    started = time.monotonic()
    out_dir = repo_output_dir(repo.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    # graphify appends graphify-out/ to cache_root itself, so hand it the
    # parent and its cache lands beside graph.json.
    cache_root = out_dir.parent
    graph_path = out_dir / "graph.json"

    detected = code_only_detect(repo.path, cache_root=cache_root)

    code_paths: list[Path] = []
    for entry in detected["files"].get("code", []):
        p = Path(entry)
        code_paths.extend(collect_files(p) if p.is_dir() else [p])

    if not code_paths:
        extraction = {"nodes": [], "edges": [], "hyperedges": [],
                      "input_tokens": 0, "output_tokens": 0}
    else:
        # root=repo.path anchors node ids and source_file relativisation;
        # cache_root only decides where the cache is written (#1774).
        extraction = extract(code_paths, cache_root=cache_root, root=repo.path)
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
    reorient_links(payload["links"], set(graph.nodes))
    graph_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return ExtractResult(
        tag=repo.tag,
        nodes=graph.number_of_nodes(),
        edges=graph.number_of_edges(),
        files=len(code_paths),
        seconds=round(time.monotonic() - started, 2),
        graph_path=graph_path,
    )
