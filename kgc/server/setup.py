"""What `kgc init` writes: a .mcp.json with no secrets in it, and the CLAUDE.md block.

The server reads credentials from .env itself, so .mcp.json only needs the
command and the absolute .env path. It is still written 0600 out of habit -
a config file that names where the credentials live is worth keeping private.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from kgc.server.claude_md import MARKER


def build_mcp_config(env_path: Path, python: Path | None = None) -> dict:
    python = python or Path(sys.executable)
    return {
        "mcpServers": {
            "kgc": {
                "command": str(python),
                "args": ["-m", "kgc.cli", "serve", "--env", str(Path(env_path).resolve())],
            }
        }
    }


def write_private(path: Path, text: str) -> None:
    """Create or replace `path` so it is never readable by anyone else, even
    briefly: created 0600, and an existing wider file is truncated and
    tightened on the same descriptor before anything is written."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def append_claude_md(path: Path, block: str) -> bool:
    """Append the kgc block unless its marker heading is already present.
    Returns True when the file was written."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if MARKER in existing:
        return False
    if not existing:
        separator = ""
    elif existing.endswith("\n\n"):
        separator = ""
    elif existing.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    path.write_text(existing + separator + block, encoding="utf-8")
    return True
