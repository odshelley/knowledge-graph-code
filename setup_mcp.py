#!/usr/bin/env python3
"""Generate .mcp.json for this project: graphify stdio + Neo4j read-only.

.mcp.json carries the Neo4j password, so it is gitignored. Regenerate it rather
than editing it by hand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from kgc.config import Settings, load_settings
from kgc.globalgraph import global_graph_path

OUT_PATH = Path(".mcp.json")


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
            # `uvx mcp-neo4j-cypher@latest --help` on 2026-10-03.
            "neo4j": {
                "command": "uvx",
                "args": [
                    "mcp-neo4j-cypher@latest",
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


def main() -> int:
    settings = load_settings(Path(".env"))
    config = build_config(settings, global_graph_path(), Path(sys.executable))
    OUT_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    OUT_PATH.chmod(0o600)
    print(f"wrote {OUT_PATH} (gitignored, mode 600)")
    print("servers: graphify (stdio), neo4j (read-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
