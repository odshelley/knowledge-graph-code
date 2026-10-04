"""The MCP server object: nine tools wired to a Backend, plus startup and serve.

This is the only module that imports `mcp`. Tool bodies live in tools.py; here
each one is wrapped so a KgcError becomes its one-line text and anything else
becomes `internal error: <Type>` - the SDK would otherwise hide our message
behind a generic ToolError.
"""
from __future__ import annotations

import threading

from mcp.server.mcpserver import MCPServer

from kgc.config import Settings
from kgc.server import tools
from kgc.server.db import Backend, KgcError
from kgc.server.queries import REPO_TAGS

SERVER_NAME = "kgc"
STARTUP_TIMEOUT_SECONDS = 3.0


def startup_repo_tags(backend, timeout: float = STARTUP_TIMEOUT_SECONDS) -> list[str]:
    """Best effort: the tags currently pushed, for the search description.
    Any failure or a slow (paused) instance yields [] and the server starts anyway."""
    result = []

    def fetch_tags():
        try:
            result.clear()
            result.extend([r["tag"] for r in backend.read(REPO_TAGS)])
        except Exception:  # noqa: BLE001 - timeout, KgcError, anything: the fallback is generic text
            pass

    thread = threading.Thread(target=fetch_tags, name="kgc-startup", daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    return list(result)


def _run(fn, **kwargs) -> str:
    try:
        return fn(**kwargs)
    except KgcError as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - never a traceback, never a message that might carry a URI
        return f"internal error: {type(exc).__name__}"


def build_server(backend, *, repo_tags: list[str] | None = None) -> MCPServer:
    server = MCPServer(name=SERVER_NAME, instructions=tools.SCHEMA_NOTES)
    tags = list(repo_tags or [])
    search_description = tools.DESCRIPTIONS["search"] + (
        f" Repos indexed: {', '.join(tags)}." if tags else ""
    )

    @server.tool(name="search", description=search_description)
    def search(query: str, repo: str | None = None, limit: int = 10, max_tokens: int = 1500) -> str:
        return _run(tools.search, db=backend, query=query, repo=repo, limit=limit, max_tokens=max_tokens)

    @server.tool(name="describe", description=tools.DESCRIPTIONS["describe"])
    def describe(symbol: str, repo: str | None = None, max_tokens: int = 1500) -> str:
        return _run(tools.describe, db=backend, symbol=symbol, repo=repo, max_tokens=max_tokens)

    @server.tool(name="callers", description=tools.DESCRIPTIONS["callers"])
    def callers(symbol: str, repo: str | None = None, depth: int = 1, max_tokens: int = 1500) -> str:
        return _run(tools.callers, db=backend, symbol=symbol, repo=repo, depth=depth, max_tokens=max_tokens)

    @server.tool(name="callees", description=tools.DESCRIPTIONS["callees"])
    def callees(symbol: str, repo: str | None = None, depth: int = 1, max_tokens: int = 1500) -> str:
        return _run(tools.callees, db=backend, symbol=symbol, repo=repo, depth=depth, max_tokens=max_tokens)

    @server.tool(name="impact", description=tools.DESCRIPTIONS["impact"])
    def impact(symbols: list[str] | None = None, files: list[str] | None = None,
               depth: int = 2, max_tokens: int = 1500) -> str:
        return _run(tools.impact, db=backend, symbols=symbols, files=files, depth=depth, max_tokens=max_tokens)

    @server.tool(name="externals", description=tools.DESCRIPTIONS["externals"])
    def externals(repo: str | None = None, min_repos: int = 1, max_tokens: int = 1500) -> str:
        return _run(tools.externals, db=backend, repo=repo, min_repos=min_repos, max_tokens=max_tokens)

    @server.tool(name="cross_repo", description=tools.DESCRIPTIONS["cross_repo"])
    def cross_repo(repo_a: str, repo_b: str | None = None, max_tokens: int = 1500) -> str:
        return _run(tools.cross_repo, db=backend, repo_a=repo_a, repo_b=repo_b, max_tokens=max_tokens)

    @server.tool(name="stats", description=tools.DESCRIPTIONS["stats"])
    def stats(repo: str | None = None) -> str:
        return _run(tools.stats, db=backend, repo=repo)

    @server.tool(name="cypher", description=tools.DESCRIPTIONS["cypher"])
    def cypher(query: str, max_tokens: int = 1500) -> str:
        return _run(tools.cypher, db=backend, query=query, max_tokens=max_tokens)

    return server


def serve(settings: Settings, *, http_port: int | None = None) -> None:
    backend = Backend(settings)
    server = build_server(backend, repo_tags=startup_repo_tags(backend))
    try:
        if http_port is None:
            server.run("stdio")
        else:
            server.run("streamable-http", host="127.0.0.1", port=int(http_port))
    finally:
        backend.close()
