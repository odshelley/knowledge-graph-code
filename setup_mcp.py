#!/usr/bin/env python3
"""Generate .mcp.json for this project: graphify stdio + Neo4j read-only.

This is the TREATMENT-ARM config for the verification harness (graphify +
mcp-neo4j-cypher). Daily use is `kgc init`. Plan 2 deletes this file with
graphify.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from kgc.config import Settings, load_settings
from kgc.globalgraph import global_graph_path

OUT_PATH = Path(".mcp.json")

# Pinned: CLAUDE.md names this server's tools (read_neo4j_cypher,
# get_neo4j_schema) and the flags below were verified against this exact
# release; @latest could rename either without warning.
NEO4J_MCP_VERSION = "0.6.0"


def build_config(settings: Settings, graph_path: Path, python_path: Path) -> dict:
    return {
        "mcpServers": {
            # Cheap path: IDF-scored, trigram-indexed, token-budgeted retrieval
            # over the merged multi-repo graph.
            "graphify": {
                "command": str(python_path),
                "args": ["-m", "graphify.serve", str(graph_path)],
            },
            # Precise path: read-only Cypher, with the result size capped so a
            # careless MATCH (n) RETURN n cannot blow the token budget. Only
            # kgc writes to this database. Flags verified against
            # `uvx mcp-neo4j-cypher@0.6.0 --help` on 2026-10-03.
            "neo4j": {
                "command": "uvx",
                "args": [
                    f"mcp-neo4j-cypher@{NEO4J_MCP_VERSION}",
                    "--read-only",
                    "--token-limit", "4000",
                    "--db-url", settings.neo4j_uri,
                    "--username", settings.neo4j_username,
                    "--database", settings.neo4j_database,
                ],
                # The password goes through the environment only - never argv,
                # which any local process can read via `ps`.
                "env": {"NEO4J_PASSWORD": settings.neo4j_password},
            },
        }
    }


def _write_private(path: Path, text: str) -> None:
    """Create or replace `path` so it is never readable by anyone else, even
    briefly. write_text-then-chmod leaves a window at the default umask; here
    the file is created 0600, and an existing (possibly wider) file is
    truncated and tightened on the same descriptor before anything is written.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None,
                        help="where to write (default: ./.mcp.json). The verification "
                             "harness's treatment arm reads verify/treatment-mcp.json.")
    args = parser.parse_args(argv)
    out_path = Path(args.out) if args.out else OUT_PATH

    settings = load_settings(Path(".env"))
    config = build_config(settings, global_graph_path(), Path(sys.executable))
    _write_private(out_path, json.dumps(config, indent=2))
    print(f"wrote {out_path} (gitignored, mode 600)")
    print(f"servers: graphify (stdio), neo4j (read-only, mcp-neo4j-cypher@{NEO4J_MCP_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
