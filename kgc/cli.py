# kgc/cli.py
"""Command line entry point: doctor / build / push / status.

These subcommands take no knowledge of what invoked them, so a manual run and a
CI runner are interchangeable. That is what makes this POC transferable to the
bank, where CI on merge to master is the correct trigger.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kgc.config import load_repos, load_settings
from kgc.extract import extract_repo
from kgc.globalgraph import (
    global_graph_path,
    load_global_graph,
    merge_repo,
    normalize_direction,
    registered_repos,
    retag_shared_stubs,
)

ENV_PATH = Path(".env")
REPOS_PATH = Path("repos.yaml")


def _cmd_doctor(_args: argparse.Namespace) -> int:
    from kgc.graph.neo4j_io import doctor

    settings = load_settings(ENV_PATH)
    print(f"neo4j target: {settings.redacted()}")
    report = doctor(settings)
    print(f"reachable:    {report.reachable}")
    print(f"database:     {report.database or '-'}")
    print(f"server:       {report.server_version or '-'}")
    print(f"contents:     {report.node_count} nodes, "
          f"{report.relationship_count} relationships")
    print(f"message:      {report.message}")
    return 0 if report.reachable else 1


def _cmd_build(args: argparse.Namespace) -> int:
    repos = load_repos(REPOS_PATH)
    if args.repo:
        repos = [r for r in repos if r.tag in set(args.repo)]
        if not repos:
            print(f"no enabled repo matching {args.repo}", file=sys.stderr)
            return 1

    failures: list[str] = []
    for repo in repos:
        try:
            extracted = extract_repo(repo)
            merged = merge_repo(extracted)
        except Exception as exc:  # noqa: BLE001 - one bad repo must not abort
            failures.append(repo.tag)
            print(f"  {repo.tag:26s} FAILED  {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            continue
        state = "skipped (unchanged)" if merged.skipped else (
            f"+{merged.nodes_added} -{merged.nodes_removed} nodes, "
            f"{merged.cross_repo_calls} cross-repo"
        )
        print(f"  {repo.tag:26s} {extracted.nodes:6d}n {extracted.edges:6d}e "
              f"{extracted.seconds:6.2f}s  {state}")

    # Two post-merge passes graphify does not do (see kgc.globalgraph):
    # direction first (links only), then re-tag dedup survivors that several
    # repos reference as shared external stubs (nodes only).
    try:
        oriented = normalize_direction(global_graph_path())
    except FileNotFoundError as exc:
        print(f"no global graph to normalise: {exc}", file=sys.stderr)
    else:
        print(f"normalised direction on {oriented.changed} edges "
              f"({oriented.repaired} stale markers repaired, "
              f"{oriented.skipped} skipped)")
        retagged = retag_shared_stubs(global_graph_path())
        print(f"re-tagged {retagged} shared stubs as external")

    try:
        graph = load_global_graph()
    except FileNotFoundError as exc:
        print(f"\nno global graph to summarise: {exc}", file=sys.stderr)
    else:
        print(f"\nglobal graph: {graph.number_of_nodes()} nodes, "
              f"{graph.number_of_edges()} edges")
    if failures:
        print(f"failed repos: {', '.join(failures)}", file=sys.stderr)
    return 1 if failures else 0


def _cmd_push(args: argparse.Namespace) -> int:
    # load_global_graph() is checked before importing push_global_graph (Task
    # 5's not-yet-written function) so a missing global graph is reported
    # cleanly even before Task 5 exists, instead of an unrelated ImportError.
    try:
        graph = load_global_graph()
    except FileNotFoundError as exc:
        print(f"no global graph to push: {exc}", file=sys.stderr)
        return 1

    from kgc.graph.neo4j_io import push_global_graph

    settings = load_settings(ENV_PATH)
    repos = load_repos(REPOS_PATH)
    tags = set(args.repo) if args.repo else {r.tag for r in repos}
    try:
        summary = push_global_graph(graph, settings, tags, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1
    for line in summary:
        print(line)
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    # global_list() returns {tag: metadata}, not {"repos": {tag: metadata}}
    # (verified against the installed graphifyy==0.9.74) - index it directly.
    for tag, meta in sorted(registered_repos().items()):
        print(f"  {tag:26s} {meta.get('source_path', '-')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kgc")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check the Neo4j connection").set_defaults(
        func=_cmd_doctor)

    p_build = sub.add_parser("build", help="extract repos and merge into the global graph")
    p_build.add_argument("--repo", action="append", help="limit to this tag (repeatable)")
    p_build.set_defaults(func=_cmd_build)

    p_push = sub.add_parser("push", help="reap and push repo slices to Neo4j")
    p_push.add_argument("--repo", action="append", help="limit to this tag (repeatable)")
    p_push.add_argument("--dry-run", action="store_true", help="plan only, write nothing")
    p_push.set_defaults(func=_cmd_push)

    sub.add_parser("status", help="list repos in the global graph").set_defaults(
        func=_cmd_status)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
