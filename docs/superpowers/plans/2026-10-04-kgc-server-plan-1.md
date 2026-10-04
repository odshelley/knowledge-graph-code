# kgc MCP Server (Plan 1 of 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace graphify's in-memory MCP server and `mcp-neo4j-cypher` with one Neo4j-backed MCP server, `kgc`, exposing nine purpose-built tools, started by `kgc serve`, configured by `kgc init`, and measured as a third arm of the existing verification harness.

**Architecture:** A `kgc/server/` package where every tool is a parameterised Cypher query run in a read transaction plus a text formatter with a token budget. Symbol resolution and output shaping are pure functions tested without a database; the one I/O class (`Backend`) is tested with fake drivers. The write side gains a `kgc/graph/schema.py` (constraints, indexes, full-text index) and a `Repo` metadata node written last by the existing push. Plan 2 (vendoring the extractor) is a separate plan; nothing here depends on it.

**Tech Stack:** Python 3.11+, `mcp` 2.3 (`mcp.server.mcpserver.MCPServer`), `neo4j` driver 6.x (`execute_read`, `unit_of_work(timeout=10)`), pytest under `-W error`, Neo4j full-text index (`db.index.fulltext.queryNodes`).

**Spec:** `docs/specs/2026-10-04-kgc-bespoke-mcp-design.md` — sections "Plan 1 — the MCP server", "Neo4j schema", "Error handling", "Testing", "Verification experiment".

## Global Constraints

Copied from the spec. Every task's requirements include these.

- Server class is `mcp.server.mcpserver.MCPServer` (mcp 2.x; FastMCP does not exist in this SDK). Tool functions are synchronous.
- Exactly nine tools, named `search`, `describe`, `callers`, `callees`, `impact`, `externals`, `cross_repo`, `stats`, `cypher`. Output is compact text, never JSON.
- Every tool except `stats` takes `max_tokens: int = 1500`. Tokens are estimated at 4 characters per token. Over-budget output is cut at a line boundary and ends with `… truncated; narrow by repo or reduce depth`.
- `depth` parameters are capped at 4. Transaction timeout is 10 s on every query.
- Read-only twice over: every query runs via `session.execute_read`; the `cypher` tool refuses, before sending, any query that after string-literal removal matches `\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b`, `\bLOAD\s+CSV\b` or `\bCALL\s*\{` (case-insensitive). Refusal text: `refused: read-only server`.
- Error shapes, verbatim: `cannot reach Neo4j at <host>; is the instance paused?` · `timed out; narrow by repo or reduce depth` · `no node matches <X>` followed by up to three nearest search hits · ambiguous symbol → candidate list, never a guess · `none` is the literal output of `cross_repo` and `externals` when empty.
- The server never raises out of a tool call and never returns a traceback; unexpected exceptions become `internal error: <ExceptionType>`.
- Password never appears in any output: every error string passes through `kgc.graph.neo4j_io._scrub`.
- Schema: label `CodeNode`; properties `id, label, repo, external, source_file, source_location, file_type, community`; relationship types `calls, indirect_call, imports, imports_from, contains, method, inherits, references, uses, rationale_for`; direction caller→callee, importer→imported, container→contained, class→method, subclass→superclass, docstring→code. New label `Repo` with `tag, path, pushed_at, built_at_commit, node_count, edge_count, skipped_extensions` (JSON string; `"null"` until Plan 2). `Repo` is written **last** in a push.
- Constraints/indexes, by name: `codenode_id` (unique), `repo_tag` (unique), `codenode_repo`, `codenode_label`, full-text `kgc_text` on `[label, source_file]`. The first POC's unnamed index on `CodeNode(id)` must be dropped before `codenode_id` can be created, and only when `owningConstraint IS NULL`.
- `.mcp.json` is written mode 0600 and contains no username, password or database name — only the command and the absolute `.env` path.
- One driver per server process, connected lazily on the first tool call. Startup reads repo tags with a 3 s timeout and falls back to a generic `search` description.
- Full-text ranking: Lucene score × 1.0 for sourced nodes, × 0.5 for externals; `rationale` hits are reported as the code node they describe.
- Existing 107 tests keep passing under `-W error` at every commit. Run the suite with `cd /Users/osianshelley/Projects/knowledge-graph-code && .venv/bin/python -m pytest -q -W error`.
- Credentials live only in `.env` (gitignored). Never print, log or commit them. Never run `kgc push` or any Neo4j write in a test; database-touching tests are opt-in behind `KGC_INTEGRATION=1` and read-only.

**Two plan-time decisions not spelled out in the spec** (both recorded here so a reviewer can object):

1. `setup_mcp.py` survives Plan 1 as the *treatment-arm* config generator (it is the baseline the experiment compares against) and gains `--out`; Plan 2 deletes it with graphify. `kgc init` is the daily-use generator and writes `.mcp.json`.
2. The harness's two MCP arms run in a per-run temporary directory holding only that arm's `CLAUDE.md`, instead of in the project root. Reason: Claude Code loads `CLAUDE.md` from the cwd *and every parent*, so once the project's own `CLAUDE.md` describes kgc, the treatment arm could not run anywhere under the project without seeing it. The treatment arm's instructions move verbatim to `verify/arms/treatment/CLAUDE.md`; the kgc arm's are rendered from the same template `kgc init` prints.

---

## File structure

```
kgc/
  graph/
    __init__.py
    neo4j_io.py        MOVED from kgc/neo4j_io.py; push ensures schema, reaps/writes Repo node
    schema.py          NEW  SCHEMA_STATEMENTS, LEGACY_ID_INDEXES, ensure_schema(session)
  server/
    __init__.py        NEW
    format.py          NEW  strip_diacritics, search_tokens, query_terms, lucene_query,
                            node_line, edge_line, cut_to_budget
    guard.py           NEW  strip_literals, is_write, REFUSED
    db.py              NEW  KgcError, Backend(settings).read(query, **params)
    queries.py         NEW  every Cypher string; neighbours(), impact() builders; whitelists
    resolve.py         NEW  Node, Resolution, resolve(db, symbol, repo)
    tools.py           NEW  the nine tool functions over a Backend; DESCRIPTIONS; SCHEMA_NOTES
    claude_md.py       NEW  CLAUDE_MD_TEMPLATE, render_claude_md(tags)
    setup.py           NEW  build_mcp_config, write_private, append_claude_md
    app.py             NEW  build_server(backend, repo_tags), startup_repo_tags, serve
  cli.py               MODIFIED  + serve, + init; push passes repo paths
setup_mcp.py           MODIFIED  --out
CLAUDE.md              REWRITTEN to the kgc block (old text → verify/arms/treatment/CLAUDE.md)
verify/run_arm.py      MODIFIED  third arm, per-arm mcp config, temp cwd, kgc_calls, isolation_ok
verify/arms/treatment/CLAUDE.md   NEW (moved content)
tests/test_schema.py, test_push.py (+), test_format.py, test_guard.py, test_db.py,
tests/test_queries.py, test_resolve.py, test_tools.py, test_app.py, test_cli.py (+),
tests/test_setup.py, test_claude_md.py, test_pyproject.py (+), test_verify_runner.py (±),
tests/integration/test_live_tools.py (opt-in)
```

Responsibilities: `format.py` and `guard.py` are pure text. `queries.py` is strings only. `resolve.py` and `tools.py` know Cypher *names* but do no I/O themselves — they call `db.read`. `db.py` is the only module that imports the driver. `app.py` is the only module that imports `mcp`.

---

### Task 1: `kgc/graph/` package — schema module and the `Repo` node

**Files:**
- Move: `kgc/neo4j_io.py` → `kgc/graph/neo4j_io.py`
- Create: `kgc/graph/__init__.py`, `kgc/graph/schema.py`
- Modify: `kgc/cli.py:30`, `kgc/cli.py:105-111` (import path, `repo_paths`), `tests/test_cli.py:109`, `tests/test_neo4j_io.py:3,102`, `tests/test_push.py:4,72` (import paths), `tests/test_push.py` `_Recorder._R` (iterable)
- Test: `tests/test_schema.py` (new), `tests/test_push.py` (extend)

**Interfaces:**
- Consumes: today's `push_global_graph(graph, settings, tags, *, dry_run=False, driver=None)`, `connect`, `resolve_database`, `_scrub`.
- Produces: `kgc.graph.schema.FULLTEXT_INDEX = "kgc_text"`, `SCHEMA_STATEMENTS: tuple[str, ...]`, `LEGACY_ID_INDEXES: str`, `ensure_schema(session) -> list[str]`; `kgc.graph.neo4j_io.REAP_REPO_META`, `REPO_UPSERT`, `push_global_graph(..., repo_paths: dict[str, str] | None = None)`. Later tasks import `connect`, `resolve_database`, `_scrub` from `kgc.graph.neo4j_io`.

- [ ] **Step 1: Move the module and fix imports**

```bash
cd /Users/osianshelley/Projects/knowledge-graph-code
mkdir -p kgc/graph && printf '"""Neo4j write side: connection, push, schema."""\n' > kgc/graph/__init__.py
git mv kgc/neo4j_io.py kgc/graph/neo4j_io.py
sed -i '' 's/from kgc\.neo4j_io import/from kgc.graph.neo4j_io import/; s/"kgc\.neo4j_io\./"kgc.graph.neo4j_io./' kgc/cli.py tests/test_cli.py tests/test_neo4j_io.py tests/test_push.py
grep -rn "kgc\.neo4j_io" kgc tests verify && echo "STILL REFERENCED" || echo "clean"
```

- [ ] **Step 2: Run the suite — 107 pass**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: `107 passed`

- [ ] **Step 3: Commit the move**

```bash
git add -A kgc/graph kgc/cli.py tests && git commit -q -m "refactor: move neo4j_io under kgc.graph"
```

- [ ] **Step 4: Write the failing schema tests**

Create `tests/test_schema.py`:

```python
import pytest

from kgc.graph.schema import (
    FULLTEXT_INDEX,
    LEGACY_ID_INDEXES,
    SCHEMA_STATEMENTS,
    ensure_schema,
)


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def consume(self):
        return None


class _Session:
    def __init__(self, legacy_names=()):
        self.calls = []
        self._legacy = [{"name": n} for n in legacy_names]

    def run(self, query, **params):
        self.calls.append(" ".join(query.split()))
        if query.strip().startswith("SHOW INDEXES"):
            return _Result(self._legacy)
        return _Result()


def _norm(s):
    return " ".join(s.split())


def test_schema_statements_name_every_object_the_server_reads():
    joined = " ".join(SCHEMA_STATEMENTS)
    for needle in (
        "CONSTRAINT codenode_id IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.id IS UNIQUE",
        "CONSTRAINT repo_tag IF NOT EXISTS FOR (r:Repo) REQUIRE r.tag IS UNIQUE",
        "INDEX codenode_repo IF NOT EXISTS FOR (n:CodeNode) ON (n.repo)",
        "INDEX codenode_label IF NOT EXISTS FOR (n:CodeNode) ON (n.label)",
        f"FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS FOR (n:CodeNode) ON EACH [n.label, n.source_file]",
    ):
        assert needle in joined
    assert FULLTEXT_INDEX == "kgc_text"


def test_ensure_schema_drops_the_legacy_unowned_id_index_before_creating():
    session = _Session(legacy_names=["index_3f2a"])
    log = ensure_schema(session)
    assert session.calls[0].startswith("SHOW INDEXES")
    assert session.calls[1] == "DROP INDEX index_3f2a IF EXISTS"
    assert session.calls[2:] == [_norm(s) for s in SCHEMA_STATEMENTS]
    assert "dropped legacy index index_3f2a" in log


def test_ensure_schema_is_a_no_op_drop_when_nothing_is_legacy():
    session = _Session()
    ensure_schema(session)
    assert not any(c.startswith("DROP") for c in session.calls)
    assert len(session.calls) == 1 + len(SCHEMA_STATEMENTS)


def test_legacy_query_never_matches_a_constraint_backed_index():
    assert "owningConstraint IS NULL" in LEGACY_ID_INDEXES
    assert "properties = ['id']" in LEGACY_ID_INDEXES
    assert "labelsOrTypes = ['CodeNode']" in LEGACY_ID_INDEXES


def test_ensure_schema_refuses_to_interpolate_a_suspicious_index_name():
    # DROP INDEX cannot take a parameter, so the name is interpolated. The
    # whitelist regex is the injection guard.
    session = _Session(legacy_names=["x; DROP DATABASE neo4j"])
    with pytest.raises(ValueError, match="refusing"):
        ensure_schema(session)
```

- [ ] **Step 5: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_schema.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.graph.schema'`

- [ ] **Step 6: Write `kgc/graph/schema.py`**

```python
"""Constraints and indexes the server depends on.

Idempotent, so push runs it every time. The only non-obvious part is the
legacy index: the first POC created an unnamed range index on CodeNode(id),
and Neo4j refuses to create a uniqueness constraint while an equivalent index
exists, so that index has to go first. A constraint's own backing index also
appears in SHOW INDEXES - with owningConstraint set - and must never be dropped.
"""
from __future__ import annotations

import re

FULLTEXT_INDEX = "kgc_text"

SCHEMA_STATEMENTS: tuple[str, ...] = (
    "CREATE CONSTRAINT codenode_id IF NOT EXISTS FOR (n:CodeNode) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT repo_tag IF NOT EXISTS FOR (r:Repo) REQUIRE r.tag IS UNIQUE",
    "CREATE INDEX codenode_repo IF NOT EXISTS FOR (n:CodeNode) ON (n.repo)",
    "CREATE INDEX codenode_label IF NOT EXISTS FOR (n:CodeNode) ON (n.label)",
    f"CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS "
    "FOR (n:CodeNode) ON EACH [n.label, n.source_file]",
)

LEGACY_ID_INDEXES = """
SHOW INDEXES YIELD name, type, labelsOrTypes, properties, owningConstraint
WHERE type = 'RANGE' AND labelsOrTypes = ['CodeNode'] AND properties = ['id']
  AND owningConstraint IS NULL
RETURN name
"""

_INDEX_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def drop_legacy_id_indexes(session) -> list[str]:
    names = [record["name"] for record in session.run(LEGACY_ID_INDEXES)]
    dropped: list[str] = []
    for name in names:
        if not _INDEX_NAME_RE.match(name):
            raise ValueError(f"refusing to interpolate index name {name!r}")
        session.run(f"DROP INDEX {name} IF EXISTS").consume()
        dropped.append(name)
    return dropped


def ensure_schema(session) -> list[str]:
    """Drop the legacy id index, then create every constraint and index.

    Returns a human-readable log, one line per action taken.
    """
    log = [f"dropped legacy index {name}" for name in drop_legacy_id_indexes(session)]
    for statement in SCHEMA_STATEMENTS:
        session.run(statement).consume()
    log.append(f"ensured {len(SCHEMA_STATEMENTS)} constraints and indexes")
    return log
```

- [ ] **Step 7: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_schema.py`
Expected: 5 passed

- [ ] **Step 8: Write the failing push tests**

In `tests/test_push.py`, first make the recorder's result iterable (the schema step iterates `SHOW INDEXES`): inside `_Recorder.run`, add to class `_R`:

```python
            def __iter__(self_inner):
                return iter([])
```

Change the import at the top of the file to:

```python
from kgc.graph.neo4j_io import (
    REAP_REPO,
    REAP_REPO_META,
    REAP_ORPHAN_EXTERNALS,
    REPO_UPSERT,
    preflight_capacity,
    push_global_graph,
)
```

Append these tests:

```python
def _queries(driver):
    return [" ".join(q.split()) for q, _ in driver.session_obj.calls]


def test_push_ensures_schema_before_any_write():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    queries = _queries(driver)
    # resolve_database's COUNT probe is recorded first (same fake session), so
    # look for the schema statements anywhere before the first destructive query.
    first_write = next(i for i, q in enumerate(queries) if "DELETE" in q)
    assert any(q.startswith("SHOW INDEXES") for q in queries[:first_write])
    assert any("CREATE CONSTRAINT codenode_id" in q for q in queries[:first_write])
    assert any("CREATE FULLTEXT INDEX kgc_text" in q for q in queries[:first_write])


def test_reap_removes_the_repo_node_so_an_incomplete_push_is_visible():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    queries = _queries(driver)
    meta_reap = next(i for i, q in enumerate(queries) if "MATCH (r:Repo {tag: $tag}) DELETE r" in q)
    first_node_write = next(i for i, q in enumerate(queries) if "MERGE (n:CodeNode" in q)
    assert meta_reap < first_node_write
    assert "$tag" in REAP_REPO_META


def test_push_writes_the_repo_node_last_per_push():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha", "beta"}, driver=driver,
                      repo_paths={"alpha": "/src/alpha", "beta": "/src/beta"})
    queries = _queries(driver)
    repo_writes = [i for i, q in enumerate(queries) if "MERGE (r:Repo" in q]
    assert len(repo_writes) == 2
    last_data_write = max(
        i for i, q in enumerate(queries)
        if "MERGE (n:CodeNode" in q or "[r:" in q or "NOT (n)--()" in q
    )
    assert last_data_write < repo_writes[0]


def test_repo_node_carries_counts_path_and_pushed_at():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver,
                      repo_paths={"alpha": "/src/alpha"})
    (params,) = [p for q, p in driver.session_obj.calls if "MERGE (r:Repo" in q]
    assert params["tag"] == "alpha"
    assert params["path"] == "/src/alpha"
    assert params["node_count"] == 1          # alpha::mod_f
    assert params["edge_count"] == 2          # alpha->numpy and beta->alpha touch alpha
    assert params["built_at_commit"] is None  # Plan 2 supplies it
    assert params["skipped_extensions"] == "null"
    assert params["pushed_at"].endswith("+00:00")
    assert "MERGE (r:Repo {tag: $tag})" in " ".join(REPO_UPSERT.split())


def test_repo_node_path_defaults_to_empty_when_not_supplied():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha"}, driver=driver)
    (params,) = [p for q, p in driver.session_obj.calls if "MERGE (r:Repo" in q]
    assert params["path"] == ""
```

- [ ] **Step 9: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_push.py`
Expected: FAIL with `ImportError: cannot import name 'REAP_REPO_META'`

- [ ] **Step 10: Modify `kgc/graph/neo4j_io.py`**

Add after the imports:

```python
from datetime import datetime, timezone

from kgc.graph.schema import ensure_schema
```

Delete the two constants `ENSURE_INDEX` and `ENSURE_REPO_INDEX` and add, next to `REAP_ORPHAN_EXTERNALS`:

```python
# One Repo node per pushed repo, written LAST so its presence proves the push
# completed. The reap deletes it first for the same reason: a push that dies
# half-way leaves tagged nodes with no Repo node, which `stats` reports as
# "last push incomplete".
REAP_REPO_META = "MATCH (r:Repo {tag: $tag}) DELETE r"

REPO_UPSERT = """
MERGE (r:Repo {tag: $tag})
SET r.path = $path,
    r.pushed_at = $pushed_at,
    r.built_at_commit = $built_at_commit,
    r.node_count = $node_count,
    r.edge_count = $edge_count,
    r.skipped_extensions = $skipped_extensions
"""


def _repo_meta(graph, tag: str, path: str) -> dict:
    tag_nodes = {nid for nid, d in graph.nodes(data=True) if d.get("repo") == tag}
    return {
        "tag": tag,
        "path": path,
        "pushed_at": datetime.now(timezone.utc).isoformat(),
        # The merged global graph carries no per-repo commit; Plan 2's
        # per-repo artefact does.
        "built_at_commit": None,
        "node_count": len(tag_nodes),
        "edge_count": sum(1 for u, v in graph.edges() if u in tag_nodes or v in tag_nodes),
        "skipped_extensions": "null",
    }
```

Change the signature and body of `push_global_graph`:

```python
def push_global_graph(graph, settings: Settings, tags: set[str], *,
                      dry_run: bool = False, driver=None,
                      repo_paths: dict[str, str] | None = None) -> list[str]:
```

Replace the two `session.run(ENSURE_INDEX...)` lines with:

```python
            lines.extend(ensure_schema(session))
```

Replace the reap loop body with:

```python
            for tag in sorted(tags):
                session.run(REAP_REPO_META, tag=tag).consume()
                session.run(REAP_REPO, tag=tag).consume()
                lines.append(f"reaped {tag!r}")
```

After `lines.append("swept orphaned external nodes")`, add:

```python
            paths = repo_paths or {}
            for tag in sorted(tags):
                session.run(REPO_UPSERT, **_repo_meta(graph, tag, paths.get(tag, ""))).consume()
                lines.append(f"wrote Repo node for {tag!r}")
```

In `kgc/cli.py` `_cmd_push`, pass the paths:

```python
        summary = push_global_graph(
            graph, settings, tags, dry_run=args.dry_run,
            repo_paths={r.tag: str(r.path) for r in repos},
        )
```

- [ ] **Step 11: Run the whole suite**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 117 passed (107 + 5 schema + 5 push)

- [ ] **Step 12: Commit**

```bash
git add kgc/graph tests/test_schema.py tests/test_push.py kgc/cli.py
git commit -q -m "feat(graph): schema module (constraints, kgc_text full-text index) and Repo node written last by push"
```

---

### Task 2: `kgc/server/format.py` — tokenising, Lucene escaping, line shapes, token budget

**Files:**
- Create: `kgc/server/__init__.py`, `kgc/server/format.py`
- Test: `tests/test_format.py`

**Interfaces:**
- Produces: `strip_diacritics(text) -> str`, `search_tokens(text) -> list[str]`, `query_terms(question) -> list[str]`, `lucene_query(question) -> str`, `node_line(row: dict) -> str`, `edge_line(src: dict, rel: str, tgt: dict, location: str | None = None) -> str`, `cut_to_budget(lines: list[str], max_tokens: int = 1500, hint: str = NARROW_HINT) -> str`, constants `CHARS_PER_TOKEN = 4`, `DEFAULT_MAX_TOKENS = 1500`, `NARROW_HINT = "narrow by repo or reduce depth"`.
- Row dicts carry keys `id, label, repo, source_file, source_location, community, external` (what every node query in Task 4 returns).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_format.py`:

```python
from kgc.server.format import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_TOKENS,
    NARROW_HINT,
    cut_to_budget,
    edge_line,
    lucene_query,
    node_line,
    query_terms,
    search_tokens,
    strip_diacritics,
)


def test_search_tokens_split_snake_case_hyphens_and_dots_and_fold_diacritics():
    assert search_tokens("graph_first-guard.py") == ["graph", "first", "guard", "py"]
    assert search_tokens("Résumé_Loader") == ["resume", "loader"]
    assert strip_diacritics("über") == "uber"


def test_query_terms_drop_stopwords_and_one_or_two_letter_words():
    assert query_terms("how does the bridge sampler work") == ["bridge", "sampler"]
    assert query_terms("what is validate_analysis") == ["validate", "analysis"]


def test_query_terms_fall_back_to_everything_when_nothing_survives():
    # all stopwords -> the stopwords themselves
    assert query_terms("how does it work") == ["how", "does", "work"]
    # all too short -> the raw tokens, so a 2-letter acronym is still searchable
    assert query_terms("is it in a DB") == ["is", "it", "in", "a", "db"]


def test_lucene_query_joins_terms_and_boosts_the_phrase_for_multi_word_queries():
    assert lucene_query("zotero") == "zotero"
    assert lucene_query("push_one") == '"push one"^2 OR push OR one'
    assert lucene_query("bridge sampler") == '"bridge sampler"^2 OR bridge OR sampler'
    assert lucene_query("") == ""
    assert lucene_query("the") == "the"
    # tokens are [^\W_]+ so Lucene specials never survive tokenising; a bare
    # '*' or '?' must not reach the index as a wildcard.
    assert lucene_query("c++") == "c"
    assert lucene_query("*") == ""


def test_node_line_shapes_sourced_and_external_rows():
    sourced = {"id": "kr::x", "label": "push_one()", "repo": "knowledge-retrieval",
               "source_file": "pipeline/zotero/push.py", "source_location": "L61",
               "community": 12, "external": False}
    assert node_line(sourced) == "knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c12"
    external = {"id": "numpy", "label": "numpy", "repo": None, "source_file": "",
                "source_location": None, "community": None, "external": True}
    assert node_line(external) == "external  numpy"


def test_node_line_tolerates_missing_location_and_community():
    row = {"id": "a::f", "label": "f", "repo": "a", "source_file": "f.py",
           "source_location": None, "community": None, "external": False}
    assert node_line(row) == "a  f  f.py"


def test_edge_line_names_both_ends_with_repo_and_optional_location():
    src = {"label": "push_one()", "repo": "knowledge-retrieval"}
    tgt = {"label": "create_items()", "repo": "knowledge-retrieval"}
    assert edge_line(src, "calls", tgt, "pipeline/zotero/push.py:L101") == (
        "push_one() [knowledge-retrieval]  -calls->  create_items() [knowledge-retrieval]"
        "  (pipeline/zotero/push.py:L101)"
    )
    ext = {"label": "numpy", "repo": None}
    assert edge_line(src, "imports", ext) == (
        "push_one() [knowledge-retrieval]  -imports->  numpy [external]"
    )


def test_cut_to_budget_returns_input_unchanged_when_under_budget():
    assert cut_to_budget(["one", "two", "three"], max_tokens=100) == "one\ntwo\nthree"


def test_cut_to_budget_cuts_on_a_line_boundary_and_says_so_top_and_bottom():
    lines = [f"line {i:03d} " + "x" * 30 for i in range(50)]   # 39 chars each
    out = cut_to_budget(lines, max_tokens=50)                    # 200-char budget
    head, *body = out.split("\n")
    assert head == f"[truncated: showing 5 of 50 lines; {NARROW_HINT}]"
    assert out.endswith(f"… truncated; {NARROW_HINT}")
    kept = body[:-1]
    assert kept == lines[:5], "cuts only at a line boundary"


def test_cut_to_budget_default_budget_and_custom_hint():
    assert DEFAULT_MAX_TOKENS == 1500 and CHARS_PER_TOKEN == 4
    out = cut_to_budget(["y" * 100] * 100, max_tokens=10, hint="add LIMIT")
    assert out.endswith("… truncated; add LIMIT")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_format.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server'`

- [ ] **Step 3: Write `kgc/server/__init__.py` and `kgc/server/format.py`**

`kgc/server/__init__.py`:

```python
"""The kgc MCP server: nine read-only tools over the Neo4j code graph."""
```

`kgc/server/format.py`:

```python
"""Pure text helpers: tokenising queries, escaping for Lucene, shaping result
lines, and cutting output to a token budget. No I/O and no Neo4j in here.

The tokeniser and the budget cutter are adapted from graphify 0.9.74's
serve.py (Apache-2.0); the line shapes are ours.
"""
from __future__ import annotations

import re
import unicodedata

CHARS_PER_TOKEN = 4
DEFAULT_MAX_TOKENS = 1500
NARROW_HINT = "narrow by repo or reduce depth"

# Question and filler words that would otherwise out-rank content words.
_STOPWORDS = frozenset({
    "how", "what", "why", "when", "where", "which", "who", "whom", "whose",
    "does", "did", "is", "are", "was", "were", "be", "been", "being",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "has", "have", "had", "the", "and", "but", "not", "for", "from", "with",
    "without", "into", "onto", "off", "that", "this", "these", "those", "there",
    "here", "its", "their", "them", "they", "about", "any", "all", "some",
    "work", "works", "working",
})

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def strip_diacritics(text) -> str:
    text = "" if text is None else str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def search_tokens(text) -> list[str]:
    """Word tokens, lower-cased, diacritics folded. `_`, `-` and `.` all split,
    so `graph_first` and `graph-first` tokenise identically."""
    return re.findall(r"[^\W_]+", strip_diacritics(text).lower())


def _is_searchable(token: str) -> bool:
    if all("a" <= ch <= "z" for ch in token):
        return len(token) > 2
    return True


def query_terms(question: str) -> list[str]:
    """Content words of a question. Drops stopwords and 1-2 letter words;
    falls back to every token when nothing survives, so "how does it work"
    still searches for something."""
    tokens = [t for t in search_tokens(question) if _is_searchable(t)]
    content = [t for t in tokens if t not in _STOPWORDS]
    if content:
        return content
    return tokens or search_tokens(question)


def lucene_query(question: str) -> str:
    """OR of the escaped terms, with the exact phrase boosted when there is
    more than one term. Empty string when there is nothing to search for."""
    terms = query_terms(question)
    if not terms:
        return ""
    escaped = [_LUCENE_SPECIAL.sub(r"\\\1", t) for t in terms]
    if len(escaped) == 1:
        return escaped[0]
    return f'"{" ".join(escaped)}"^2 OR ' + " OR ".join(escaped)


def node_line(row: dict) -> str:
    """`repo  label  file:line  cN` for sourced nodes; `external  label` otherwise."""
    if row.get("external"):
        return f"external  {row.get('label') or ''}"
    source_file = row.get("source_file") or ""
    location = row.get("source_location") or ""
    where = f"{source_file}:{location}" if source_file and location else source_file
    community = row.get("community")
    tail = f"  c{community}" if community is not None else ""
    return f"{row.get('repo') or '-'}  {row.get('label') or ''}  {where}{tail}".rstrip()


def _endpoint(row: dict) -> str:
    return f"{row.get('label') or ''} [{row.get('repo') or 'external'}]"


def edge_line(src: dict, rel: str, tgt: dict, location: str | None = None) -> str:
    where = f"  ({location})" if location else ""
    return f"{_endpoint(src)}  -{rel}->  {_endpoint(tgt)}{where}"


def cut_to_budget(lines: list[str], max_tokens: int = DEFAULT_MAX_TOKENS,
                  hint: str = NARROW_HINT) -> str:
    """Join lines; if over budget, cut at a line boundary and say so at both
    ends. A bottom-only marker reads as silence, so the count goes on top too."""
    output = "\n".join(lines)
    budget = max(1, int(max_tokens)) * CHARS_PER_TOKEN
    if len(output) <= budget:
        return output
    cut = output[:budget].rfind("\n")
    kept = output[:cut] if cut > 0 else output[:budget]
    shown = kept.count("\n") + 1 if kept else 0
    return (
        f"[truncated: showing {shown} of {len(lines)} lines; {hint}]\n"
        + kept
        + f"\n… truncated; {hint}"
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_format.py`
Expected: 10 passed. If the line-boundary test fails, the cut is wrong, not the test: `kept` must be a prefix of the joined output that ends exactly at a newline.

- [ ] **Step 5: Commit**

```bash
git add kgc/server/__init__.py kgc/server/format.py tests/test_format.py
git commit -q -m "feat(server): pure text helpers - tokeniser, Lucene query, line shapes, token budget"
```

---

### Task 3: `kgc/server/guard.py` and `kgc/server/db.py` — the write guard and the one I/O class

**Files:**
- Create: `kgc/server/guard.py`, `kgc/server/db.py`
- Modify: `docs/specs/2026-10-04-kgc-bespoke-mcp-design.md` (two sentences about escaped quotes — see Step 9)
- Test: `tests/test_guard.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `kgc.graph.neo4j_io.connect(settings)`, `resolve_database(driver, configured)`, `_scrub(text, password)`; `kgc.server.format.NARROW_HINT`; `kgc.config.Settings`.
- Produces: `guard.REFUSED = "refused: read-only server"`, `guard.strip_literals(query) -> str`, `guard.is_write(query) -> bool`; `db.KgcError(Exception)`, `db.QUERY_TIMEOUT_SECONDS = 10.0`, `db.Backend(settings, *, connect_fn=connect, resolve_fn=resolve_database)` with `.read(query: str, **params) -> list[dict]`, `.host -> str`, `.close()`. Every later module calls only `db.read`.

- [ ] **Step 1: Write the failing guard tests**

Create `tests/test_guard.py`:

```python
import pytest

from kgc.server.guard import REFUSED, is_write, strip_literals


@pytest.mark.parametrize("keyword", [
    "CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "FOREACH",
])
def test_every_write_keyword_is_refused_in_any_case(keyword):
    assert is_write(f"MATCH (n) {keyword} n.x = 1")
    assert is_write(f"match (n) {keyword.lower()} n.x = 1")
    assert is_write(f"MATCH (n) {keyword.title()} n.x = 1")


def test_load_csv_and_call_subqueries_are_refused():
    assert is_write("LOAD CSV FROM 'file:///x' AS row RETURN row")
    assert is_write("load   csv with headers from 'x' as r return r")
    assert is_write("CALL { MATCH (n) RETURN n } RETURN 1")
    assert is_write("CALL{ MATCH (n) RETURN n } RETURN 1")


def test_plain_reads_and_read_procedures_are_allowed():
    assert not is_write("MATCH (n:CodeNode) RETURN n.label LIMIT 5")
    assert not is_write("CALL db.index.fulltext.queryNodes('kgc_text', 'push') YIELD node RETURN node")
    assert not is_write("MATCH (n) WHERE n.settings IS NOT NULL RETURN n")   # 'settings' != SET
    assert not is_write("MATCH (n) RETURN n.dataset, n.reset, n.preset")


def test_keywords_inside_string_literals_do_not_refuse():
    assert not is_write("MATCH (n) WHERE n.label = 'set' RETURN n")
    assert not is_write('MATCH (n) WHERE n.label = "DELETE me" RETURN n')
    assert not is_write("MATCH (n) WHERE n.label = 'it\\'s a set' RETURN n")   # escaped quote inside literal
    assert strip_literals("x = 'a\\'b' AND y = \"c\"") == "x = '' AND y = ''"


def test_refusal_text_is_the_spec_sentence():
    assert REFUSED == "refused: read-only server"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_guard.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.guard'`

- [ ] **Step 3: Write `kgc/server/guard.py`**

```python
"""Belt for the `cypher` tool's braces.

The read transaction every query runs in is the real guard: Neo4j rejects a
write inside it server-side. This file refuses the obvious cases before they
are sent, so the agent gets `refused: read-only server` instead of a driver
error, and so a future change to the transaction mode cannot silently open a
write path. String literals are blanked first so `WHERE n.label = 'set'` is
not refused; backslash-escaped quotes inside a literal are handled.
"""
from __future__ import annotations

import re

REFUSED = "refused: read-only server"

_LITERAL_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_WRITE_RE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b"
    r"|\bLOAD\s+CSV\b"
    r"|\bCALL\s*\{",
    re.IGNORECASE,
)


def strip_literals(query: str) -> str:
    return _LITERAL_RE.sub("''", query)


def is_write(query: str) -> bool:
    return _WRITE_RE.search(strip_literals(query)) is not None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_guard.py`
Expected: 12 passed (8 parametrised + 4)

- [ ] **Step 5: Write the failing Backend tests**

Create `tests/test_db.py`:

```python
import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable

from kgc.config import Settings
from kgc.server.db import QUERY_TIMEOUT_SECONDS, Backend, KgcError, _read

SETTINGS = Settings(
    neo4j_uri="neo4j+s://f35a6a04.databases.neo4j.io",
    neo4j_username="user",
    neo4j_password="s3cret",
    neo4j_database="neo4j",
)


class _Record:
    def __init__(self, d):
        self._d = d

    def data(self):
        return dict(self._d)


class _Tx:
    def __init__(self, rows, fail=None):
        self._rows, self._fail = rows, fail
        self.ran = []

    def run(self, query, **params):
        self.ran.append((query, params))
        if self._fail is not None:
            raise self._fail
        return [_Record(r) for r in self._rows]


class _Session:
    def __init__(self, rows, fail=None):
        self.tx = _Tx(rows, fail)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute_read(self, fn, *args, **kwargs):
        return fn(self.tx, *args, **kwargs)


class _Driver:
    def __init__(self, rows=(), fail=None):
        self.rows, self.fail, self.closed, self.sessions = list(rows), fail, False, 0

    def session(self, database=None):
        self.sessions += 1
        self.database = database
        return _Session(self.rows, self.fail)

    def close(self):
        self.closed = True


def _backend(driver, *, connect_calls=None):
    calls = connect_calls if connect_calls is not None else []

    def connect_fn(settings):
        calls.append(settings)
        if isinstance(driver, Exception):
            raise driver
        return driver

    return Backend(SETTINGS, connect_fn=connect_fn, resolve_fn=lambda d, name: "neo4j")


def test_backend_connects_lazily_and_only_once():
    calls = []
    driver = _Driver(rows=[{"a": 1}])
    backend = _backend(driver, connect_calls=calls)
    assert calls == []                       # constructing does not connect
    assert backend.read("RETURN 1 AS a") == [{"a": 1}]
    assert backend.read("RETURN 1 AS a") == [{"a": 1}]
    assert len(calls) == 1
    assert driver.database == "neo4j"


def test_read_passes_query_and_params_into_the_read_transaction():
    driver = _Driver(rows=[])
    backend = _backend(driver)
    backend.read("MATCH (n {id: $id}) RETURN n", id="x", limit=3)
    # the session was created inside read(); grab the tx it ran on
    assert driver.sessions == 1


def test_unit_of_work_timeout_is_ten_seconds():
    assert QUERY_TIMEOUT_SECONDS == 10.0
    assert _read.timeout == 10.0              # set by neo4j.unit_of_work


def test_unreachable_database_gives_the_spec_sentence_and_retries_next_time():
    calls = []
    backend = _backend(ServiceUnavailable("Unable to retrieve routing information for neo4j+s://user:s3cret@host"),
                       connect_calls=calls)
    with pytest.raises(KgcError) as ei:
        backend.read("RETURN 1")
    assert str(ei.value) == "cannot reach Neo4j at f35a6a04.databases.neo4j.io; is the instance paused?"
    with pytest.raises(KgcError):
        backend.read("RETURN 1")
    assert len(calls) == 2                   # the driver was reset, so it reconnected


def test_service_unavailable_mid_session_resets_the_driver():
    driver = _Driver(fail=ServiceUnavailable("gone"))
    calls = []
    backend = _backend(driver, connect_calls=calls)
    with pytest.raises(KgcError, match="cannot reach Neo4j"):
        backend.read("RETURN 1")
    assert driver.closed is True
    assert len(calls) == 1


class _Timeout(ClientError):
    code = "Neo.ClientError.Transaction.TransactionTimedOut"


class _Syntax(ClientError):
    code = "Neo.ClientError.Statement.SyntaxError"


def test_transaction_timeout_gives_the_spec_sentence():
    backend = _backend(_Driver(fail=_Timeout("The transaction has been terminated")))
    with pytest.raises(KgcError) as ei:
        backend.read("MATCH p=()-[*1..4]->() RETURN p")
    assert str(ei.value) == "timed out; narrow by repo or reduce depth"


def test_query_errors_are_reported_and_scrubbed():
    backend = _backend(_Driver(fail=_Syntax("Invalid input near 's3cret'")))
    with pytest.raises(KgcError) as ei:
        backend.read("MATCH (n RETURN n")
    assert str(ei.value).startswith("query error: ")
    assert "s3cret" not in str(ei.value)
    assert "***" in str(ei.value)


def test_host_is_the_uri_hostname_only():
    assert _backend(_Driver()).host == "f35a6a04.databases.neo4j.io"


def test_close_is_safe_before_and_after_connecting():
    driver = _Driver(rows=[{"a": 1}])
    backend = _backend(driver)
    backend.close()
    backend.read("RETURN 1 AS a")
    backend.close()
    assert driver.closed is True
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_db.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.db'`

- [ ] **Step 7: Write `kgc/server/db.py`**

```python
"""Read-only Neo4j access for the server.

One lazy driver per process, every query in a read transaction with a 10 s
timeout, and every failure mapped to the one-line sentence the spec names so
a tool can return it as text. This is the only server module that imports
the driver.
"""
from __future__ import annotations

from urllib.parse import urlparse

from neo4j import unit_of_work
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

from kgc.config import Settings
from kgc.graph.neo4j_io import _scrub, connect, resolve_database
from kgc.server.format import NARROW_HINT

QUERY_TIMEOUT_SECONDS = 10.0


class KgcError(Exception):
    """A failure the tool reports to the agent as one line of text."""


def _host(uri: str) -> str:
    return urlparse(uri).hostname or uri


@unit_of_work(timeout=QUERY_TIMEOUT_SECONDS)
def _read(tx, query: str, params: dict) -> list[dict]:
    return [record.data() for record in tx.run(query, **params)]


class Backend:
    def __init__(self, settings: Settings, *, connect_fn=connect, resolve_fn=resolve_database):
        self._settings = settings
        self._connect = connect_fn
        self._resolve = resolve_fn
        self._driver = None
        self._database: str | None = None

    @property
    def host(self) -> str:
        return _host(self._settings.neo4j_uri)

    def _unreachable(self) -> KgcError:
        return KgcError(f"cannot reach Neo4j at {self.host}; is the instance paused?")

    def _ensure(self) -> None:
        if self._driver is not None:
            return
        try:
            driver = self._connect(self._settings)
            self._database = self._resolve(driver, self._settings.neo4j_database)
        except Exception:  # noqa: BLE001 - every connect failure is "unreachable" to the agent
            raise self._unreachable() from None
        self._driver = driver

    def _reset(self) -> None:
        driver, self._driver, self._database = self._driver, None, None
        if driver is not None:
            try:
                driver.close()
            except Exception:  # noqa: BLE001 - closing a broken driver may itself fail
                pass

    def read(self, query: str, **params) -> list[dict]:
        self._ensure()
        try:
            with self._driver.session(database=self._database) as session:
                return session.execute_read(_read, query, params)
        except (ServiceUnavailable, SessionExpired):
            self._reset()
            raise self._unreachable() from None
        except Neo4jError as exc:
            code = getattr(exc, "code", "") or ""
            if "TimedOut" in code or "timeout" in str(exc).lower():
                raise KgcError(f"timed out; {NARROW_HINT}") from None
            message = getattr(exc, "message", None) or str(exc)
            raise KgcError(
                _scrub(f"query error: {message}", self._settings.neo4j_password)
            ) from None

    def close(self) -> None:
        self._reset()
```

- [ ] **Step 8: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_db.py tests/test_guard.py`
Expected: 21 passed

- [ ] **Step 9: Bring the spec in line with the guard (it handles escaped quotes)**

The spec says escaped quotes are not handled. They are. Edit `docs/specs/2026-10-04-kgc-bespoke-mcp-design.md` with these exact replacements (run and check the two-line diff):

```bash
cd /Users/osianshelley/Projects/knowledge-graph-code && .venv/bin/python - <<'PY'
from pathlib import Path
p = Path("docs/specs/2026-10-04-kgc-bespoke-mcp-design.md"); s = p.read_text()
old1 = ("not refused. Escaped quotes inside literals are not handled and can cause a\n"
        "false refusal; that is documented, and the agent's recourse is the specific\n"
        "tools. Transaction timeout 10 s on every call.")
new1 = ("not refused, and backslash-escaped quotes inside literals are handled. The one\n"
        "deliberate false positive is a read-only `CALL { }` subquery, refused because\n"
        "a subquery can contain a write; the agent's recourse is the specific tools.\n"
        "Transaction timeout 10 s on every call.")
old2 = ("  **not** refused; a literal containing an escaped quote followed by a keyword\n"
        "  is refused (the documented false positive, asserted so a later fix is\n"
        "  deliberate).")
new2 = ("  **not** refused, including literals with backslash-escaped quotes; a\n"
        "  read-only `CALL { }` subquery is refused (the documented false positive).")
old3 = "| `cypher` guard false positive on escaped quotes inside literals | Documented; agent can use the specific tools |"
new3 = "| `cypher` guard refuses read-only `CALL { }` subqueries | Documented; agent can use the specific tools |"
for o, n in ((old1, new1), (old2, new2), (old3, new3)):
    assert s.count(o) == 1, o[:40]
    s = s.replace(o, n)
p.write_text(s); print("spec amended")
PY
git diff --stat docs/specs/
```

- [ ] **Step 10: Run the whole suite and commit**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 148 passed (117 + 10 format + 12 guard + 9 db)

```bash
git add kgc/server/guard.py kgc/server/db.py tests/test_guard.py tests/test_db.py docs/specs/2026-10-04-kgc-bespoke-mcp-design.md
git commit -q -m "feat(server): read-only Backend with 10s read transactions and spec error shapes; cypher write guard"
```

---

### Task 4: `kgc/server/queries.py` and `kgc/server/resolve.py` — every Cypher string, and symbol resolution

**Files:**
- Create: `kgc/server/queries.py`, `kgc/server/resolve.py`
- Test: `tests/test_queries.py`, `tests/test_resolve.py`, `tests/fakes.py` (shared `FakeBackend`)

**Interfaces:**
- Consumes: `kgc.server.format.lucene_query`, `kgc.graph.schema.FULLTEXT_INDEX`.
- Produces (queries): `NODE_RETURN`, `DEPENDENCY_RELS`, `CALLEE_RELS`, `MAX_DEPTH = 4`, `DEFAULT_LIMIT = 50`, `clamp_depth(depth) -> int`, `rel_pattern(rels) -> str`, string constants `SEARCH`, `EXACT_LABEL`, `PATH_SCOPED`, `FILE_NODE`, `CI_LABEL`, `DESCRIBE_NODE`, `EDGE_COUNTS`, `EXTERNALS`, `CROSS_REPO`, `REPO_META`, `REPO_TAGS`, `NODE_COUNTS`, `REL_COUNTS`, `EXTERNAL_COUNT`; builders `neighbours(direction: "in" | "out", rels, depth) -> str`, `impact(depth) -> str`.
- Produces (resolve): `Node` dataclass (`id, label, repo, source_file, source_location, community, external`; `Node.from_row(row)`, `.as_row()`), `Resolution` dataclass (`node: Node | None`, `candidates: tuple[Node, ...]`, `nearest: tuple[Node, ...]`, property `ok`), `resolve(db, symbol: str, repo: str | None = None) -> Resolution`.
- Produces (tests/fakes.py): `FakeBackend(responses)` where `responses` is a list of `(matcher, rows)`; `matcher` is a substring of the query or a callable `(query, params) -> bool`; `rows` is a list or a callable `(params) -> list`. Records `.calls: list[tuple[str, dict]]`. Returns `[]` when nothing matches.

- [ ] **Step 1: Write the shared fake**

Create `tests/fakes.py`:

```python
"""A scripted stand-in for kgc.server.db.Backend. Tests pick responses by
matching on the query text, so each tool's tests read as 'given these rows
from these queries, the text is ...'."""
from __future__ import annotations


class FakeBackend:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def read(self, query: str, **params) -> list[dict]:
        self.calls.append((query, params))
        for matcher, rows in self.responses:
            hit = matcher(query, params) if callable(matcher) else (matcher in query)
            if hit:
                return list(rows(params)) if callable(rows) else list(rows)
        return []

    def queries(self) -> list[str]:
        return [q for q, _ in self.calls]


def node_row(id, label, repo="alpha", source_file="a.py", source_location="L1",
             community=0, external=False, **extra):
    row = {"id": id, "label": label, "repo": repo, "source_file": source_file,
           "source_location": source_location, "community": community, "external": external}
    row.update(extra)
    return row
```

- [ ] **Step 2: Write the failing query tests**

Create `tests/test_queries.py`:

```python
import pytest

from kgc.server import queries as q


def test_whitelists_and_caps():
    assert q.DEPENDENCY_RELS == ("calls", "indirect_call", "imports", "imports_from",
                                 "references", "uses", "inherits")
    assert q.CALLEE_RELS == ("calls", "indirect_call", "imports", "imports_from", "references", "uses")
    assert q.MAX_DEPTH == 4 and q.DEFAULT_LIMIT == 50
    assert q.clamp_depth(9) == 4 and q.clamp_depth(0) == 1 and q.clamp_depth(2) == 2


def test_rel_pattern_only_interpolates_whitelisted_types():
    assert q.rel_pattern(("calls", "imports")) == "calls|imports"
    with pytest.raises(ValueError, match="unknown relationship type"):
        q.rel_pattern(("calls", "DETACH DELETE"))


def test_neighbours_in_points_at_the_target_and_clamps_depth():
    text = q.neighbours("in", q.DEPENDENCY_RELS, 9)
    assert "(s:CodeNode)-[:calls|indirect_call|imports|imports_from|references|uses|inherits*1..4]->(t:CodeNode {id: $id})" in text
    assert "s.id <> $id" in text
    assert "$repo IS NULL OR s.repo = $repo" in text
    assert "type(relationships(p)[0]) AS via" in text
    assert "LIMIT $limit" in text


def test_neighbours_out_starts_at_the_target_and_uses_callee_rels():
    text = q.neighbours("out", q.CALLEE_RELS, 1)
    assert "(t:CodeNode {id: $id})-[:calls|indirect_call|imports|imports_from|references|uses*1..1]->(s:CodeNode)" in text
    assert "inherits" not in text


def test_neighbours_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        q.neighbours("sideways", q.CALLEE_RELS, 1)


def test_impact_takes_ids_and_files_and_groups_by_repo():
    text = q.impact(7)
    assert "t.id IN $ids" in text
    assert "any(f IN $files WHERE t.source_file = f OR t.source_file ENDS WITH '/' + f)" in text
    assert "*1..4]->(t)" in text
    assert "s.repo IS NOT NULL" in text
    assert "[0..20] AS sample" in text


def test_search_uses_the_fulltext_index_and_follows_rationale_edges():
    assert "db.index.fulltext.queryNodes('kgc_text', $q)" in q.SEARCH
    assert "CASE WHEN node.external = true THEN 0.5 ELSE 1.0 END" in q.SEARCH
    assert "(node)-[:rationale_for]->(code:CodeNode)" in q.SEARCH
    assert "coalesce(code, node)" in q.SEARCH


def test_every_repo_scoped_query_guards_with_repo_is_null():
    for text in (q.SEARCH, q.EXACT_LABEL, q.PATH_SCOPED, q.FILE_NODE, q.CI_LABEL,
                 q.EXTERNALS, q.NODE_COUNTS, q.REL_COUNTS):
        assert "$repo IS NULL OR" in text, text


def test_cross_repo_filters_both_ends_to_sourced_nodes_in_different_repos():
    assert "a.source_file <> '' AND b.source_file <> ''" in q.CROSS_REPO
    assert "a.repo <> b.repo" in q.CROSS_REPO
    assert "$a IN [a.repo, b.repo]" in q.CROSS_REPO
    assert "$b IS NULL OR $b IN [a.repo, b.repo]" in q.CROSS_REPO


def test_externals_groups_by_top_level_package_and_min_repos():
    assert "split(e.label, '.')[0] AS package" in q.EXTERNALS
    assert "size(repos) >= $min_repos" in q.EXTERNALS
    assert "[:imports|imports_from]->(e:CodeNode)" in q.EXTERNALS


def test_node_return_carries_every_field_node_line_needs():
    for field in ("id", "label", "repo", "source_file", "source_location", "community", "external"):
        assert f"AS {field}" in q.NODE_RETURN
```

- [ ] **Step 3: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_queries.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.queries'`

- [ ] **Step 4: Write `kgc/server/queries.py`**

```python
"""Every Cypher string the server sends.

Parameters everywhere except the two things Cypher cannot parameterise:
relationship-type lists and variable-length path depth. Those are
interpolated only from the whitelists here, and depth is clamped to MAX_DEPTH.
"""
from __future__ import annotations

from kgc.graph.schema import FULLTEXT_INDEX

NODE_RETURN = (
    "n.id AS id, n.label AS label, n.repo AS repo, n.source_file AS source_file, "
    "n.source_location AS source_location, n.community AS community, "
    "coalesce(n.external, false) AS external"
)

DEPENDENCY_RELS = ("calls", "indirect_call", "imports", "imports_from",
                   "references", "uses", "inherits")
CALLEE_RELS = ("calls", "indirect_call", "imports", "imports_from", "references", "uses")
MAX_DEPTH = 4
DEFAULT_LIMIT = 50


def clamp_depth(depth: int) -> int:
    return max(1, min(int(depth), MAX_DEPTH))


def rel_pattern(rels) -> str:
    for rel in rels:
        if rel not in DEPENDENCY_RELS:
            raise ValueError(f"unknown relationship type {rel!r}")
    return "|".join(rels)


SEARCH = f"""
CALL db.index.fulltext.queryNodes('{FULLTEXT_INDEX}', $q) YIELD node, score
WHERE $repo IS NULL OR node.repo = $repo
WITH node, score * (CASE WHEN node.external = true THEN 0.5 ELSE 1.0 END) AS s
OPTIONAL MATCH (node)-[:rationale_for]->(code:CodeNode)
WITH coalesce(code, node) AS n, max(s) AS score
RETURN {NODE_RETURN}, score
ORDER BY score DESC, id
LIMIT $limit
"""

EXACT_LABEL = f"""
MATCH (n:CodeNode)
WHERE (n.label = $label OR n.label = $label + '()')
  AND ($repo IS NULL OR n.repo = $repo)
RETURN {NODE_RETURN}
ORDER BY id LIMIT $limit
"""

PATH_SCOPED = f"""
MATCH (n:CodeNode)
WHERE (n.source_file = $path OR n.source_file ENDS WITH '/' + $path)
  AND (n.label = $label OR n.label = $label + '()')
  AND ($repo IS NULL OR n.repo = $repo)
RETURN {NODE_RETURN}
ORDER BY id LIMIT $limit
"""

FILE_NODE = f"""
MATCH (n:CodeNode)
WHERE (n.source_file = $path OR n.source_file ENDS WITH '/' + $path)
  AND n.source_location = 'L1' AND n.file_type = 'code'
  AND ($repo IS NULL OR n.repo = $repo)
RETURN {NODE_RETURN}
ORDER BY id LIMIT $limit
"""

CI_LABEL = f"""
MATCH (n:CodeNode)
WHERE toLower(n.label) IN [toLower($label), toLower($label) + '()']
  AND ($repo IS NULL OR n.repo = $repo)
RETURN {NODE_RETURN}
ORDER BY id LIMIT $limit
"""

DESCRIBE_NODE = f"""
MATCH (n:CodeNode {{id: $id}})
OPTIONAL MATCH (doc:CodeNode {{file_type: 'rationale'}})-[:rationale_for]->(n)
WITH n, collect(doc.label)[0..3] AS docs
RETURN {NODE_RETURN}, n.file_type AS file_type, docs
"""

EDGE_COUNTS = """
MATCH (n:CodeNode {id: $id})-[r]->()
RETURN 'out' AS direction, type(r) AS rel, count(*) AS n
UNION ALL
MATCH (n:CodeNode {id: $id})<-[r]-()
RETURN 'in' AS direction, type(r) AS rel, count(*) AS n
"""


def neighbours(direction: str, rels, depth: int) -> str:
    """'in': nodes that reach $id (callers). 'out': nodes $id reaches (callees).
    relationships(p)[0] is always the hop adjacent to s for 'in' and adjacent
    to t for 'out' - either way, the edge whose source_location says where the
    reference is made."""
    pattern = rel_pattern(rels)
    d = clamp_depth(depth)
    if direction == "in":
        path = f"(s:CodeNode)-[:{pattern}*1..{d}]->(t:CodeNode {{id: $id}})"
    elif direction == "out":
        path = f"(t:CodeNode {{id: $id}})-[:{pattern}*1..{d}]->(s:CodeNode)"
    else:
        raise ValueError(f"direction must be 'in' or 'out', not {direction!r}")
    return f"""
MATCH p = {path}
WHERE s.id <> $id AND ($repo IS NULL OR s.repo = $repo)
WITH s, p ORDER BY length(p)
WITH s, head(collect(p)) AS p
RETURN s.id AS id, s.label AS label, s.repo AS repo, s.source_file AS source_file,
       s.source_location AS source_location, s.community AS community,
       coalesce(s.external, false) AS external,
       length(p) AS hops, type(relationships(p)[0]) AS via,
       relationships(p)[0].source_location AS via_location
ORDER BY hops, repo, label
LIMIT $limit
"""


def impact(depth: int) -> str:
    pattern = rel_pattern(DEPENDENCY_RELS)
    d = clamp_depth(depth)
    return f"""
MATCH (t:CodeNode)
WHERE t.id IN $ids OR any(f IN $files WHERE t.source_file = f OR t.source_file ENDS WITH '/' + f)
MATCH p = (s:CodeNode)-[:{pattern}*1..{d}]->(t)
WHERE s <> t AND s.repo IS NOT NULL
WITH s, min(length(p)) AS hops
ORDER BY hops, s.label
WITH s.repo AS repo, count(s) AS n,
     collect({{label: s.label, source_file: s.source_file,
              source_location: s.source_location, hops: hops}})[0..20] AS sample
RETURN repo, n, sample
ORDER BY n DESC, repo
"""


EXTERNALS = """
MATCH (s:CodeNode)-[:imports|imports_from]->(e:CodeNode)
WHERE e.external = true AND s.repo IS NOT NULL AND ($repo IS NULL OR s.repo = $repo)
WITH split(e.label, '.')[0] AS package, collect(DISTINCT s.repo) AS repos
WHERE size(repos) >= $min_repos
RETURN package, repos
ORDER BY size(repos) DESC, package
LIMIT $limit
"""

CROSS_REPO = """
MATCH (a:CodeNode)-[r]->(b:CodeNode)
WHERE a.repo IS NOT NULL AND b.repo IS NOT NULL AND a.repo <> b.repo
  AND a.source_file <> '' AND b.source_file <> ''
  AND ($a IN [a.repo, b.repo]) AND ($b IS NULL OR $b IN [a.repo, b.repo])
WITH a.repo AS from_repo, b.repo AS to_repo, type(r) AS rel, count(*) AS n,
     collect({from: a.label, to: b.label, source_file: r.source_file,
              source_location: r.source_location})[0..10] AS sample
RETURN from_repo, to_repo, rel, n, sample
ORDER BY n DESC, from_repo, to_repo, rel
"""

REPO_META = """
MATCH (r:Repo)
RETURN r.tag AS tag, r.path AS path, r.pushed_at AS pushed_at,
       r.built_at_commit AS built_at_commit, r.node_count AS node_count,
       r.edge_count AS edge_count, r.skipped_extensions AS skipped_extensions
ORDER BY tag
"""

REPO_TAGS = "MATCH (r:Repo) RETURN r.tag AS tag ORDER BY tag"

NODE_COUNTS = """
MATCH (n:CodeNode)
WHERE n.repo IS NOT NULL AND ($repo IS NULL OR n.repo = $repo)
RETURN n.repo AS repo, count(*) AS n
ORDER BY repo
"""

REL_COUNTS = """
MATCH (a:CodeNode)-[r]->()
WHERE a.repo IS NOT NULL AND ($repo IS NULL OR a.repo = $repo)
RETURN a.repo AS repo, type(r) AS rel, count(*) AS n
ORDER BY repo, n DESC
"""

EXTERNAL_COUNT = "MATCH (n:CodeNode) WHERE n.external = true RETURN count(n) AS n"
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_queries.py`
Expected: 11 passed

- [ ] **Step 6: Write the failing resolve tests**

Create `tests/test_resolve.py`:

```python
from kgc.server import queries as q
from kgc.server.resolve import Node, Resolution, resolve
from tests.fakes import FakeBackend, node_row


def test_exact_label_wins_and_returns_the_node():
    db = FakeBackend([(q.EXACT_LABEL, [node_row("a::f", "f()", source_file="a.py")])])
    res = resolve(db, "f")
    assert res.ok and res.node == Node("a::f", "f()", "alpha", "a.py", "L1", 0, False)
    assert db.queries() == [q.EXACT_LABEL]
    assert db.calls[0][1] == {"label": "f", "repo": None, "limit": q.DEFAULT_LIMIT}


def test_repo_filter_is_passed_to_every_tier():
    db = FakeBackend([(q.CI_LABEL, [node_row("b::g", "G", repo="beta")])])
    res = resolve(db, "g", repo="beta")
    assert res.ok and res.node.repo == "beta"
    assert all(params["repo"] == "beta" for _, params in db.calls)


def test_path_scoped_symbol_uses_the_path_tier_first():
    db = FakeBackend([(q.PATH_SCOPED, [node_row("a::f", "f()", source_file="pkg/a.py")])])
    res = resolve(db, "pkg/a.py::f")
    assert res.ok
    assert db.queries()[0] == q.PATH_SCOPED
    assert db.calls[0][1]["path"] == "pkg/a.py" and db.calls[0][1]["label"] == "f"


def test_a_bare_path_resolves_to_the_file_node():
    db = FakeBackend([(q.FILE_NODE, [node_row("a::a", "a.py", source_file="pkg/a.py")])])
    res = resolve(db, "pkg/a.py")
    assert res.ok and res.node.label == "a.py"
    assert db.queries()[0] == q.FILE_NODE


def test_same_label_in_two_files_is_ambiguous_with_one_candidate_per_file():
    rows = [node_row("a::f1", "f()", source_file="a.py"),
            node_row("a::f2", "f()", source_file="a.py", source_location="L9"),
            node_row("b::f", "f()", repo="beta", source_file="b.py")]
    db = FakeBackend([(q.EXACT_LABEL, rows)])
    res = resolve(db, "f")
    assert not res.ok and res.node is None
    assert [c.id for c in res.candidates] == ["a::f1", "b::f"]


def test_same_label_twice_in_one_file_is_not_ambiguous():
    rows = [node_row("a::f1", "f()", source_file="a.py"),
            node_row("a::f2", "f()", source_file="a.py", source_location="L9")]
    db = FakeBackend([(q.EXACT_LABEL, rows)])
    res = resolve(db, "f")
    assert res.ok and res.node.id == "a::f1"


def test_tiers_run_in_order_until_one_hits():
    db = FakeBackend([(q.CI_LABEL, [node_row("a::f", "F()")])])
    res = resolve(db, "f")
    assert res.ok
    assert db.queries() == [q.EXACT_LABEL, q.CI_LABEL]


def test_fulltext_fallback_resolves_a_clear_winner():
    hits = [dict(node_row("a::f", "frobnicate()"), score=4.0),
            dict(node_row("a::g", "frob_helper()"), score=1.5)]
    db = FakeBackend([(q.SEARCH, hits)])
    res = resolve(db, "frobnicate")
    assert res.ok and res.node.id == "a::f"
    assert db.queries() == [q.EXACT_LABEL, q.CI_LABEL, q.SEARCH]
    assert db.calls[-1][1]["q"] == "frobnicate"
    assert db.calls[-1][1]["limit"] == 5


def test_fulltext_fallback_without_a_clear_winner_reports_nearest_three():
    hits = [dict(node_row(f"a::{i}", f"thing{i}()"), score=3.0 - 0.1 * i) for i in range(5)]
    db = FakeBackend([(q.SEARCH, hits)])
    res = resolve(db, "thing")
    assert not res.ok and res.candidates == ()
    assert [n.id for n in res.nearest] == ["a::0", "a::1", "a::2"]


def test_no_hits_anywhere_is_an_empty_resolution():
    res = resolve(FakeBackend(), "nothing_here")
    assert res == Resolution()
    assert not res.ok


def test_unsearchable_symbol_skips_the_fulltext_call():
    db = FakeBackend()
    resolve(db, "*")
    assert q.SEARCH not in db.queries()


def test_node_round_trips_through_as_row():
    row = node_row("a::f", "f()", community=3)
    assert Node.from_row(row).as_row() == row
```

- [ ] **Step 7: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_resolve.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.resolve'`

- [ ] **Step 8: Write `kgc/server/resolve.py`**

```python
"""Turn what the agent typed into one node, or say precisely why not.

Tiers, first non-empty wins (adapted from graphify's _find_node_tiers):
  path-scoped `file.py::name`  or  bare path -> file node
  exact label (with or without the trailing `()` callables carry)
  case-insensitive label
  full-text, accepted only with a clear winner (top score >= 2x the runner-up)
More than one hit across different files is ambiguity, reported as candidates
- one per file - never a guess. Several hits in one file are ordinary
precedence (a file node plus its members): the first wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from kgc.server import queries as q
from kgc.server.format import lucene_query

_PATHLIKE = re.compile(r"/|\.[A-Za-z0-9]{1,5}$")


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    repo: str | None
    source_file: str | None
    source_location: str | None
    community: int | None
    external: bool

    @classmethod
    def from_row(cls, row: dict) -> "Node":
        return cls(
            id=row["id"], label=row.get("label") or "", repo=row.get("repo"),
            source_file=row.get("source_file"), source_location=row.get("source_location"),
            community=row.get("community"), external=bool(row.get("external")),
        )

    def as_row(self) -> dict:
        return {"id": self.id, "label": self.label, "repo": self.repo,
                "source_file": self.source_file, "source_location": self.source_location,
                "community": self.community, "external": self.external}


@dataclass(frozen=True)
class Resolution:
    node: Node | None = None
    candidates: tuple[Node, ...] = ()
    nearest: tuple[Node, ...] = ()

    @property
    def ok(self) -> bool:
        return self.node is not None


def _decide(rows: list[dict]) -> Resolution:
    nodes = [Node.from_row(r) for r in rows]
    per_file: dict[str, Node] = {}
    for node in nodes:
        per_file.setdefault(node.source_file or "", node)
    if len(per_file) > 1:
        return Resolution(candidates=tuple(per_file.values()))
    return Resolution(node=nodes[0])


def resolve(db, symbol: str, repo: str | None = None) -> Resolution:
    symbol = symbol.strip()
    tiers: list[tuple[str, dict]] = []
    if "::" in symbol:
        path, _, name = symbol.partition("::")
        tiers.append((q.PATH_SCOPED, {"path": path.strip(), "label": name.strip()}))
    elif _PATHLIKE.search(symbol):
        tiers.append((q.FILE_NODE, {"path": symbol}))
    tiers.append((q.EXACT_LABEL, {"label": symbol}))
    tiers.append((q.CI_LABEL, {"label": symbol}))

    for query, params in tiers:
        rows = db.read(query, repo=repo, limit=q.DEFAULT_LIMIT, **params)
        if rows:
            return _decide(rows)

    lucene = lucene_query(symbol)
    if not lucene:
        return Resolution()
    hits = db.read(q.SEARCH, q=lucene, repo=repo, limit=5)
    if not hits:
        return Resolution()
    if len(hits) == 1 or hits[0]["score"] >= 2 * hits[1]["score"]:
        return Resolution(node=Node.from_row(hits[0]))
    return Resolution(nearest=tuple(Node.from_row(h) for h in hits[:3]))
```

- [ ] **Step 9: Run to verify it passes, then the whole suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_resolve.py tests/test_queries.py`
Expected: 23 passed

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 171 passed

- [ ] **Step 10: Commit**

```bash
git add kgc/server/queries.py kgc/server/resolve.py tests/fakes.py tests/test_queries.py tests/test_resolve.py
git commit -q -m "feat(server): every Cypher query as a constant or whitelisted builder; tiered symbol resolution"
```

---

### Task 5: `kgc/server/tools.py` — the nine tools as plain functions

**Files:**
- Create: `kgc/server/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: `queries.*`, `resolve.resolve`, `format.node_line/edge_line/cut_to_budget/lucene_query/DEFAULT_MAX_TOKENS`, `guard.is_write/REFUSED`, `FakeBackend` from `tests/fakes.py`.
- Produces: functions `search(db, query, repo=None, limit=10, max_tokens=1500)`, `describe(db, symbol, repo=None, max_tokens=1500)`, `callers(db, symbol, repo=None, depth=1, max_tokens=1500)`, `callees(db, symbol, repo=None, depth=1, max_tokens=1500)`, `impact(db, symbols=None, files=None, depth=2, max_tokens=1500)`, `externals(db, repo=None, min_repos=1, max_tokens=1500)`, `cross_repo(db, repo_a, repo_b=None, max_tokens=1500)`, `stats(db, repo=None)`, `cypher(db, query, max_tokens=1500)` — each returns `str`; `DESCRIPTIONS: dict[str, str]` keyed by the nine names; `SCHEMA_NOTES: str`; `TOOL_NAMES: tuple[str, ...]` in the spec's order. Errors from `db.read` (`KgcError`) propagate — Task 6 turns them into text.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools.py`:

```python
from kgc.server import queries as q
from kgc.server import tools
from kgc.server.guard import REFUSED
from tests.fakes import FakeBackend, node_row

KR = "knowledge-retrieval"
PUSH_ONE = node_row("kr::push_one", "push_one()", repo=KR,
                    source_file="pipeline/zotero/push.py", source_location="L61", community=7)


def _exact(row, label="push_one"):
    # key on the label too, or resolving *any* symbol would hit this row
    return (lambda qq, p: qq == q.EXACT_LABEL and p.get("label") == label, [row])


# --- names and descriptions ----------------------------------------------------

def test_the_nine_tool_names_in_spec_order():
    assert tools.TOOL_NAMES == ("search", "describe", "callers", "callees", "impact",
                                "externals", "cross_repo", "stats", "cypher")
    assert set(tools.DESCRIPTIONS) == set(tools.TOOL_NAMES)
    assert all(len(d) > 40 for d in tools.DESCRIPTIONS.values())


def test_schema_notes_state_the_direction_convention_and_the_labels():
    for needle in ("CodeNode", "Repo", "caller", "callee", "source_file <> ''",
                   "rationale_for", "imports_from", "external"):
        assert needle in tools.SCHEMA_NOTES
    assert "prefer the specific tools" in tools.DESCRIPTIONS["cypher"]


# --- search ---------------------------------------------------------------------

def test_search_lists_ranked_nodes_one_per_line():
    rows = [dict(PUSH_ONE, score=3.2), dict(node_row("numpy", "numpy", repo=None, external=True), score=1.0)]
    db = FakeBackend([(q.SEARCH, rows)])
    out = tools.search(db, "push one", limit=5)
    assert out.splitlines() == [
        "knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c7",
        "external  numpy",
    ]
    assert db.calls[0][1] == {"q": '"push one"^2 OR push OR one', "repo": None, "limit": 5}


def test_search_reports_no_matches_and_names_the_repo_filter():
    assert tools.search(FakeBackend(), "zzz", repo="graphify") == "no matches for 'zzz' in repo graphify"
    assert tools.search(FakeBackend(), "zzz") == "no matches for 'zzz'"


def test_search_with_nothing_searchable_does_not_hit_the_database():
    db = FakeBackend()
    assert tools.search(db, "***") == "no searchable words in '***'"
    assert db.calls == []


def test_search_honours_max_tokens():
    rows = [dict(node_row(f"a::{i}", f"fn{i}()" + "x" * 40), score=1.0) for i in range(40)]
    out = tools.search(FakeBackend([(q.SEARCH, rows)]), "fn", limit=40, max_tokens=50)
    assert out.startswith("[truncated: showing ")
    assert out.endswith("… truncated; narrow by repo or reduce depth")


# --- describe -------------------------------------------------------------------

def test_describe_shows_node_docs_and_edge_counts():
    db = FakeBackend([
        _exact(PUSH_ONE),
        (q.DESCRIBE_NODE, [dict(PUSH_ONE, file_type="code",
                                docs=["File one paper or book into Zotero."])]),
        (q.EDGE_COUNTS, [{"direction": "out", "rel": "calls", "n": 6},
                         {"direction": "in", "rel": "calls", "n": 2},
                         {"direction": "in", "rel": "imports_from", "n": 1}]),
    ])
    assert tools.describe(db, "push_one").splitlines() == [
        "knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c7",
        "type: code",
        "doc: File one paper or book into Zotero.",
        "in:  calls 2, imports_from 1",
        "out: calls 6",
    ]


def test_describe_unresolved_lists_nearest_hits():
    hits = [dict(node_row("a::1", "pushy()"), score=1.0), dict(node_row("a::2", "pusher()"), score=0.9)]
    out = tools.describe(FakeBackend([(q.SEARCH, hits)]), "push_oen")
    assert out.splitlines()[0] == "no node matches 'push_oen'"
    assert out.splitlines()[1] == "nearest:"
    assert "pushy()" in out and "pusher()" in out


def test_describe_ambiguous_lists_candidates_and_the_retry_hint():
    rows = [node_row("a::f", "f()", source_file="a.py"), node_row("b::f", "f()", repo="beta", source_file="b.py")]
    out = tools.describe(FakeBackend([(q.EXACT_LABEL, rows)]), "f")
    lines = out.splitlines()
    assert lines[0] == "ambiguous: 'f' matches 2 nodes in different files; retry as path::symbol or pass repo="
    assert lines[1:] == ["alpha  f()  a.py:L1  c0", "beta  f()  b.py:L1  c0"]


def test_describe_with_no_edges_says_none():
    db = FakeBackend([_exact(PUSH_ONE), (q.DESCRIBE_NODE, [dict(PUSH_ONE, file_type="code", docs=[])])])
    out = tools.describe(db, "push_one")
    assert "in:  none" in out and "out: none" in out


# --- callers / callees ----------------------------------------------------------

def _neighbour(id, label, repo, hops=1, via="calls", via_location="x.py:L5", **kw):
    return dict(node_row(id, label, repo=repo, **kw), hops=hops, via=via, via_location=via_location)


def test_callers_groups_by_repo_and_marks_multi_hop():
    rows = [
        _neighbour("kr::asset", "zotero_push_asset()", KR, source_file="pipeline/assets/z.py"),
        _neighbour("kr::backfill", "main()", KR, hops=2, via="imports_from", source_file="scripts/backfill.py"),
        _neighbour("g::x", "x()", "graphify", source_file="g.py"),
    ]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "*1..2]->(t:CodeNode {id: $id})" in qq, rows)])
    out = tools.callers(db, "push_one", depth=2)
    assert out.splitlines() == [
        "callers of knowledge-retrieval  push_one()  pipeline/zotero/push.py:L61  c7:",
        "[graphify] 1",
        "x() [graphify]  -calls->  push_one() [knowledge-retrieval]  (x.py:L5)",
        "[knowledge-retrieval] 2",
        "zotero_push_asset() [knowledge-retrieval]  -calls->  push_one() [knowledge-retrieval]  (x.py:L5)",
        "main() [knowledge-retrieval]  -imports_from->  push_one() [knowledge-retrieval]  (x.py:L5)  hops=2",
    ]
    _, params = db.calls[-1]
    assert params == {"id": "kr::push_one", "repo": None, "limit": q.DEFAULT_LIMIT}


def test_callees_points_the_arrow_away_from_the_target_and_omits_inherits():
    rows = [_neighbour("kr::create_items", "create_items()", KR, source_file="pipeline/zotero/client.py")]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "(t:CodeNode {id: $id})-[:" in qq, rows)])
    out = tools.callees(db, "push_one")
    assert out.splitlines()[-1] == (
        "push_one() [knowledge-retrieval]  -calls->  create_items() [knowledge-retrieval]  (x.py:L5)"
    )
    assert "inherits" not in db.queries()[-1]


def test_callers_with_none_says_so_with_the_clamped_depth():
    db = FakeBackend([_exact(PUSH_ONE)])
    assert tools.callers(db, "push_one", depth=9) == "no callers of push_one() within depth 4"
    assert tools.callers(db, "push_one", repo="graphify") == "no callers of push_one() within depth 1 in repo graphify"


def test_callers_propagates_resolution_failures():
    assert tools.callers(FakeBackend(), "ghost") == "no node matches 'ghost'"


def test_callers_passes_repo_to_the_neighbour_query_not_to_resolution():
    db = FakeBackend([_exact(PUSH_ONE)])
    tools.callers(db, "push_one", repo="graphify")
    resolve_calls = [p for qq, p in db.calls if qq == q.EXACT_LABEL]
    assert resolve_calls[0]["repo"] is None
    assert db.calls[-1][1]["repo"] == "graphify"


# --- impact ---------------------------------------------------------------------

def test_impact_groups_dependents_by_repo_with_samples():
    rows = [{"repo": "graphify", "n": 2, "sample": [
                {"label": "a()", "source_file": "a.py", "source_location": "L1", "hops": 1},
                {"label": "b()", "source_file": "b.py", "source_location": "L2", "hops": 2}]},
            {"repo": KR, "n": 1, "sample": [
                {"label": "c()", "source_file": "c.py", "source_location": "L3", "hops": 1}]}]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "t.id IN $ids" in qq, rows)])
    out = tools.impact(db, symbols=["push_one"], files=["pipeline/zotero/items.py"], depth=3)
    assert out.splitlines() == [
        "[graphify] 2 dependents",
        "  a()  a.py:L1  hops=1",
        "  b()  b.py:L2  hops=2",
        "[knowledge-retrieval] 1 dependents",
        "  c()  c.py:L3  hops=1",
    ]
    assert db.calls[-1][1] == {"ids": ["kr::push_one"], "files": ["pipeline/zotero/items.py"]}
    assert "*1..3]->(t)" in db.queries()[-1]


def test_impact_with_only_files_skips_resolution():
    db = FakeBackend([(lambda qq, p: "t.id IN $ids" in qq, [])])
    out = tools.impact(db, files=["x.py"])
    assert out == "nothing depends on x.py within depth 2"
    assert db.calls[-1][1] == {"ids": [], "files": ["x.py"]}


def test_impact_reports_unresolved_symbols_and_still_runs_for_the_rest():
    rows = [{"repo": KR, "n": 1, "sample": [{"label": "c()", "source_file": "c.py", "source_location": "L3", "hops": 1}]}]
    db = FakeBackend([_exact(PUSH_ONE), (lambda qq, p: "t.id IN $ids" in qq, rows)])
    out = tools.impact(db, symbols=["ghost", "push_one"])
    assert out.splitlines()[0] == "no node matches 'ghost'"
    assert "[knowledge-retrieval] 1 dependents" in out


def test_impact_with_nothing_to_look_up():
    assert tools.impact(FakeBackend()) == "give at least one symbol or file"


# --- externals / cross_repo ------------------------------------------------------

def test_externals_lists_packages_with_their_repos():
    rows = [{"package": "numpy", "repos": ["graphify", KR, "diffusion-bridge"]},
            {"package": "dagster", "repos": [KR]}]
    db = FakeBackend([(q.EXTERNALS, rows)])
    out = tools.externals(db, min_repos=1)
    assert out.splitlines() == [
        "numpy  repos: diffusion-bridge, graphify, knowledge-retrieval",
        "dagster  repos: knowledge-retrieval",
    ]
    assert db.calls[0][1]["min_repos"] == 1 and db.calls[0][1]["repo"] is None


def test_externals_and_cross_repo_say_none_when_empty():
    assert tools.externals(FakeBackend(), min_repos=3) == "none"
    assert tools.cross_repo(FakeBackend(), "claude", "graphify") == "none"


def test_cross_repo_lists_edge_groups_with_samples():
    rows = [{"from_repo": KR, "to_repo": "graphify", "rel": "calls", "n": 3,
             "sample": [{"from": "a()", "to": "b()", "source_file": "a.py", "source_location": "L4"}]}]
    db = FakeBackend([(q.CROSS_REPO, rows)])
    out = tools.cross_repo(db, KR)
    assert out.splitlines() == [
        "knowledge-retrieval -calls-> graphify: 3",
        "  a() -> b()  (a.py:L4)",
    ]
    assert db.calls[0][1] == {"a": KR, "b": None}


# --- stats ----------------------------------------------------------------------

def test_stats_reports_each_repo_with_meta_relationships_and_blind_spots():
    db = FakeBackend([
        (q.REPO_META, [{"tag": "alpha", "path": "/src/alpha", "pushed_at": "2026-10-04T10:00:00+00:00",
                        "built_at_commit": None, "node_count": 10, "edge_count": 20,
                        "skipped_extensions": '{".q": 3, ".sas": 1}'}]),
        (q.NODE_COUNTS, [{"repo": "alpha", "n": 10}, {"repo": "beta", "n": 4}]),
        (q.REL_COUNTS, [{"repo": "alpha", "rel": "calls", "n": 12}, {"repo": "alpha", "rel": "imports", "n": 8}]),
        (q.EXTERNAL_COUNT, [{"n": 99}]),
    ])
    assert tools.stats(db).splitlines() == [
        "[alpha] 10 nodes, 20 edges, pushed 2026-10-04T10:00:00+00:00, commit unknown",
        "  skipped extensions: .q (3), .sas (1)",
        "  relationships: calls 12, imports 8",
        "[beta] 4 nodes  last push incomplete",
        "  relationships: none",
        "externals: 99",
    ]


def test_stats_says_not_recorded_before_plan_2_and_filters_by_repo():
    db = FakeBackend([
        (q.REPO_META, [{"tag": "alpha", "path": "", "pushed_at": "t", "built_at_commit": "abc123",
                        "node_count": 1, "edge_count": 1, "skipped_extensions": "null"}]),
        (q.NODE_COUNTS, [{"repo": "alpha", "n": 1}]),
        (q.REL_COUNTS, []),
        (q.EXTERNAL_COUNT, [{"n": 0}]),
    ])
    out = tools.stats(db, repo="alpha")
    assert "commit abc123" in out
    assert "  skipped extensions: not recorded" in out
    assert "externals:" not in out                      # per-repo view omits the global count
    assert db.calls[1][1] == {"repo": "alpha"}


def test_stats_for_an_unknown_repo():
    db = FakeBackend([(q.REPO_META, []), (q.NODE_COUNTS, [])])
    assert tools.stats(db, repo="nope") == "no repo 'nope' indexed"


# --- cypher ---------------------------------------------------------------------

def test_cypher_refuses_writes_before_touching_the_database():
    db = FakeBackend()
    assert tools.cypher(db, "MATCH (n) DETACH DELETE n") == REFUSED
    assert db.calls == []


def test_cypher_renders_rows_as_a_tab_table_and_json_encodes_nested_values():
    rows = [{"repo": KR, "n": 3, "sample": ["a", "b"]}, {"repo": "graphify", "n": 1, "sample": []}]
    db = FakeBackend([("RETURN", rows)])
    out = tools.cypher(db, "MATCH (n) RETURN n.repo AS repo, count(*) AS n, collect(n.label)[0..2] AS sample")
    assert out.splitlines() == [
        "repo\tn\tsample",
        'knowledge-retrieval\t3\t["a", "b"]',
        "graphify\t1\t[]",
    ]


def test_cypher_says_no_rows_and_uses_a_query_specific_hint_when_truncating():
    assert tools.cypher(FakeBackend(), "MATCH (n) RETURN n LIMIT 0") == "no rows"
    rows = [{"x": "y" * 200} for _ in range(30)]
    out = tools.cypher(FakeBackend([("RETURN", rows)]), "MATCH (n) RETURN n.x AS x", max_tokens=20)
    assert out.endswith("… truncated; add LIMIT or RETURN fewer columns")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_tools.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.tools'`

- [ ] **Step 3: Write `kgc/server/tools.py`**

```python
"""The nine tools as plain functions over a Backend.

No MCP types in here, so every tool is testable with a fake backend and a
plain assert on text. Each tool: resolve what the agent named (or not, and
say why), run one query, shape lines, cut to budget. KgcError from db.read
propagates; app.py turns it into the one-line text.
"""
from __future__ import annotations

import json
from collections import defaultdict

from kgc.server import queries as q
from kgc.server.format import (
    DEFAULT_MAX_TOKENS,
    cut_to_budget,
    edge_line,
    lucene_query,
    node_line,
)
from kgc.server.guard import REFUSED, is_write
from kgc.server.resolve import Resolution, resolve

TOOL_NAMES = ("search", "describe", "callers", "callees", "impact",
              "externals", "cross_repo", "stats", "cypher")

SCHEMA_NOTES = """Code graph schema (read-only).
Nodes: (:CodeNode {id, label, repo, external, source_file, source_location, file_type, community}).
  Sourced nodes have repo set and id "repo::local". Externals (stdlib / third-party /
  unresolved names) have external = true, no repo, source_file = '' and merge across repos.
  file_type is code | rationale (a docstring or comment) | external | package.
(:Repo {tag, path, pushed_at, built_at_commit, node_count, edge_count, skipped_extensions}) - one per repo.
Relationships (native types): calls, indirect_call, imports, imports_from, contains, method,
  inherits, references, uses, rationale_for.
Direction: caller -> callee, importer -> imported, container -> contained, class -> method,
  subclass -> superclass, docstring -[:rationale_for]-> code.
Real cross-repo coupling: edges where both ends are sourced -
  WHERE a.repo <> b.repo AND a.source_file <> '' AND b.source_file <> ''.
Full-text index kgc_text on (label, source_file): CALL db.index.fulltext.queryNodes('kgc_text', $q).
The graph is static structure: it cannot see dependency injection, registries or config-file wiring."""

DESCRIPTIONS = {
    "search": ("Find code by words: symbol names, file paths, or docstring text, across every "
               "indexed repo. Returns ranked nodes as 'repo  label  file:line  cCommunity'. "
               "Start here when you do not know the exact name."),
    "describe": ("One symbol in detail: its repo, file and line, docstring, community, and how many "
                 "edges of each type point in and out. Accepts a name or 'path/to/file.py::name'."),
    "callers": ("Who uses this symbol: incoming calls, imports, references and subclassing, across "
                "all repos, grouped by repo. depth > 1 follows the chain (max 4). Use for "
                "'what would break if I change X'."),
    "callees": ("What this symbol uses: outgoing calls, imports and references. depth > 1 follows "
                "the chain (max 4). Use for 'what does X actually invoke'."),
    "impact": ("Transitive dependents of one or more symbols and/or changed files, grouped by repo "
               "with counts and samples. Feed a git diff's file list straight in. depth max 4."),
    "externals": ("Third-party packages each repo imports. min_repos=2 or more returns only packages "
                  "shared by that many repos, with the repo list."),
    "cross_repo": ("Real coupling between two repos' own code: edges whose both ends are sourced "
                   "nodes in different repos, grouped by relationship type with samples. "
                   "Returns 'none' when there is none."),
    "stats": ("What is indexed: per repo, node and relationship counts, when it was pushed, the "
              "commit, and which file extensions were NOT parsed (the graph's blind spots). "
              "Flags a repo whose last push did not complete."),
    "cypher": ("Read-only Cypher escape hatch for questions the other tools do not cover; prefer "
               "the specific tools first. Writes are refused. Rows come back as a tab table. "
               + SCHEMA_NOTES),
}


# --- helpers ---------------------------------------------------------------------

def _unresolved(symbol: str, res: Resolution) -> str:
    if res.candidates:
        lines = [f"ambiguous: {symbol!r} matches {len(res.candidates)} nodes in different files; "
                 "retry as path::symbol or pass repo="]
        lines += [node_line(c.as_row()) for c in res.candidates]
        return "\n".join(lines)
    lines = [f"no node matches {symbol!r}"]
    if res.nearest:
        lines.append("nearest:")
        lines += [node_line(n.as_row()) for n in res.nearest]
    return "\n".join(lines)


def _in_repo(repo: str | None) -> str:
    return f" in repo {repo}" if repo else ""


def _loc(source_file, source_location) -> str:
    return f"{source_file or ''}:{source_location or ''}".rstrip(":")


# --- tools -----------------------------------------------------------------------

def search(db, query: str, repo: str | None = None, limit: int = 10,
           max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    lucene = lucene_query(query)
    if not lucene:
        return f"no searchable words in {query!r}"
    rows = db.read(q.SEARCH, q=lucene, repo=repo, limit=int(limit))
    if not rows:
        return f"no matches for {query!r}{_in_repo(repo)}"
    return cut_to_budget([node_line(r) for r in rows], max_tokens)


def describe(db, symbol: str, repo: str | None = None,
             max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    res = resolve(db, symbol, repo)
    if not res.ok:
        return _unresolved(symbol, res)
    rows = db.read(q.DESCRIBE_NODE, id=res.node.id)
    row = rows[0] if rows else dict(res.node.as_row(), file_type="code", docs=[])
    counts = db.read(q.EDGE_COUNTS, id=res.node.id)
    lines = [node_line(row), f"type: {row.get('file_type') or 'code'}"]
    lines += [f"doc: {d}" for d in (row.get("docs") or []) if d]
    for direction, prefix in (("in", "in:  "), ("out", "out: ")):
        parts = [f"{c['rel']} {c['n']}" for c in counts if c.get("direction") == direction]
        lines.append(prefix + (", ".join(parts) or "none"))
    return cut_to_budget(lines, max_tokens)


def _neighbours(db, direction: str, rels, word: str, symbol: str, repo: str | None,
                depth: int, max_tokens: int) -> str:
    # The symbol resolves globally; `repo` narrows the *results*. A target that
    # is ambiguous across repos comes back as candidates to pick from.
    res = resolve(db, symbol, None)
    if not res.ok:
        return _unresolved(symbol, res)
    rows = db.read(q.neighbours(direction, rels, depth), id=res.node.id, repo=repo,
                   limit=q.DEFAULT_LIMIT)
    if not rows:
        return f"no {word} of {res.node.label} within depth {q.clamp_depth(depth)}{_in_repo(repo)}"
    target = res.node.as_row()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups["external" if r.get("external") else (r.get("repo") or "-")].append(r)
    lines = [f"{word} of {node_line(target)}:"]
    for name in sorted(groups):
        lines.append(f"[{name}] {len(groups[name])}")
        for r in groups[name]:
            src, tgt = (r, target) if direction == "in" else (target, r)
            line = edge_line(src, r.get("via") or "?", tgt, r.get("via_location"))
            if (r.get("hops") or 1) > 1:
                line += f"  hops={r['hops']}"
            lines.append(line)
    return cut_to_budget(lines, max_tokens)


def callers(db, symbol: str, repo: str | None = None, depth: int = 1,
            max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    return _neighbours(db, "in", q.DEPENDENCY_RELS, "callers", symbol, repo, depth, max_tokens)


def callees(db, symbol: str, repo: str | None = None, depth: int = 1,
            max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    return _neighbours(db, "out", q.CALLEE_RELS, "callees", symbol, repo, depth, max_tokens)


def impact(db, symbols: list[str] | None = None, files: list[str] | None = None,
           depth: int = 2, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    ids: list[str] = []
    problems: list[str] = []
    for symbol in symbols or []:
        res = resolve(db, symbol)
        if res.ok:
            ids.append(res.node.id)
        else:
            problems.append(_unresolved(symbol, res))
    files = [f.strip() for f in (files or []) if f and f.strip()]
    if not ids and not files:
        return "\n".join(problems) or "give at least one symbol or file"
    rows = db.read(q.impact(depth), ids=ids, files=files)
    lines = list(problems)
    if not rows:
        named = ", ".join([*(symbols or []), *files])
        lines.append(f"nothing depends on {named} within depth {q.clamp_depth(depth)}")
    for r in rows:
        lines.append(f"[{r['repo']}] {r['n']} dependents")
        for s in r.get("sample") or []:
            lines.append(f"  {s.get('label')}  {_loc(s.get('source_file'), s.get('source_location'))}"
                         f"  hops={s.get('hops')}")
    return cut_to_budget(lines, max_tokens)


def externals(db, repo: str | None = None, min_repos: int = 1,
              max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    rows = db.read(q.EXTERNALS, repo=repo, min_repos=max(1, int(min_repos)),
                   limit=q.DEFAULT_LIMIT * 4)
    if not rows:
        return "none"
    lines = [f"{r['package']}  repos: {', '.join(sorted(r['repos']))}" for r in rows]
    return cut_to_budget(lines, max_tokens)


def cross_repo(db, repo_a: str, repo_b: str | None = None,
               max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    rows = db.read(q.CROSS_REPO, a=repo_a, b=repo_b)
    if not rows:
        return "none"
    lines: list[str] = []
    for r in rows:
        lines.append(f"{r['from_repo']} -{r['rel']}-> {r['to_repo']}: {r['n']}")
        for s in r.get("sample") or []:
            lines.append(f"  {s.get('from')} -> {s.get('to')}"
                         f"  ({_loc(s.get('source_file'), s.get('source_location'))})")
    return cut_to_budget(lines, max_tokens)


def _skipped(raw) -> str:
    if raw in (None, "", "null"):
        return "not recorded"
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "not recorded"
    if not data:
        return "none"
    return ", ".join(f"{ext} ({n})" for ext, n in sorted(data.items(), key=lambda kv: (-kv[1], kv[0])))


def stats(db, repo: str | None = None) -> str:
    meta = {m["tag"]: m for m in db.read(q.REPO_META)}
    nodes = db.read(q.NODE_COUNTS, repo=repo)
    if repo and not nodes:
        return f"no repo {repo!r} indexed"
    rels = db.read(q.REL_COUNTS, repo=repo)
    lines: list[str] = []
    for n in nodes:
        tag = n["repo"]
        m = meta.get(tag)
        if m is None:
            lines.append(f"[{tag}] {n['n']} nodes  last push incomplete")
        else:
            lines.append(f"[{tag}] {n['n']} nodes, {m.get('edge_count')} edges, "
                         f"pushed {m.get('pushed_at')}, commit {m.get('built_at_commit') or 'unknown'}")
            lines.append(f"  skipped extensions: {_skipped(m.get('skipped_extensions'))}")
        parts = [f"{r['rel']} {r['n']}" for r in rels if r.get("repo") == tag]
        lines.append("  relationships: " + (", ".join(parts) or "none"))
    if not repo:
        ext = db.read(q.EXTERNAL_COUNT)
        lines.append(f"externals: {ext[0]['n'] if ext else 0}")
    return "\n".join(lines)


def _cell(value) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return "" if value is None else str(value)


def cypher(db, query: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    if is_write(query):
        return REFUSED
    rows = db.read(query)[:200]
    if not rows:
        return "no rows"
    keys = list(rows[0].keys())
    lines = ["\t".join(keys)] + ["\t".join(_cell(r.get(k)) for k in keys) for r in rows]
    return cut_to_budget(lines, max_tokens, hint="add LIMIT or RETURN fewer columns")
```

- [ ] **Step 4: Run to verify it passes, then the whole suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_tools.py`
Expected: 28 passed

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 199 passed

- [ ] **Step 5: Commit**

```bash
git add kgc/server/tools.py tests/test_tools.py
git commit -q -m "feat(server): the nine tools as plain functions with spec output and error shapes"
```

---

### Task 6: `kgc/server/app.py` and `kgc/server/claude_md.py` — the MCP server object

**Files:**
- Create: `kgc/server/app.py`, `kgc/server/claude_md.py`
- Test: `tests/test_app.py`, `tests/test_claude_md.py`

**Interfaces:**
- Consumes: `MCPServer` (mcp 2.3), `tools.*`, `db.Backend`, `db.KgcError`, `queries.REPO_TAGS`.
- Produces: `app.SERVER_NAME = "kgc"`, `app.STARTUP_TIMEOUT_SECONDS = 3.0`, `app.startup_repo_tags(backend, timeout=3.0) -> list[str]`, `app.build_server(backend, *, repo_tags=None) -> MCPServer`, `app.serve(settings, *, http_port: int | None = None) -> None`; `claude_md.render_claude_md(tags: list[str]) -> str`, `claude_md.MARKER = "# kgc code graph"`.
- The MCP SDK exposes `await server.list_tools()` and `await server.call_tool(name, args)` directly, which is how tests drive it without a transport. `call_tool` returns an object whose `.content[0].text` is the tool's string.

- [ ] **Step 1: Write the failing CLAUDE.md template tests**

Create `tests/test_claude_md.py`:

```python
from kgc.server.claude_md import MARKER, render_claude_md
from kgc.server.tools import TOOL_NAMES


def test_rendered_block_starts_with_the_marker_and_lists_repos_and_tools():
    text = render_claude_md(["alpha", "beta"])
    assert text.startswith(MARKER + "\n")
    assert "`alpha`, `beta`" in text
    for name in TOOL_NAMES:
        assert f"`{name}`" in text
    assert "BEFORE reading files or grepping" in text
    assert "dependency injection" in text          # the blind-spot sentence
    assert text.endswith("\n")


def test_rendered_block_with_no_repos_still_reads_sensibly():
    text = render_claude_md([])
    assert "(none pushed yet)" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_claude_md.py`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `kgc/server/claude_md.py`**

```python
"""The CLAUDE.md block that nudges Claude Code to reach for the graph.

One source of truth: `kgc init` prints or appends it, the project's own
CLAUDE.md is exactly this text, and the harness writes it into the kgc arm's
working directory.
"""
from __future__ import annotations

MARKER = "# kgc code graph"

_TEMPLATE = """{marker}

One MCP server, `kgc`, exposes a code graph of these repos: {repos}.

For any question about how code is structured, what calls or imports what, what
depends on what, or where something lives, use the kgc tools BEFORE reading files
or grepping:

- `search` — find a symbol, file or concept by words
- `describe` — one symbol: file, docstring, community, edge counts
- `callers` / `callees` — who uses it / what it uses, across repos
- `impact` — transitive dependents of symbols or changed files, grouped by repo
- `externals` — third-party packages per repo; `min_repos=2` for shared ones
- `cross_repo` — real coupling between two repos' own code
- `stats` — what is indexed, when, and which file types were NOT parsed
- `cypher` — read-only Cypher when nothing above fits (schema is in its description)

The graph is static code structure. It cannot see dependency injection, registries
or config-file wiring; when it says a symbol has no callers, confirm with grep. The
server is read-only; `kgc build` then `kgc push` refresh the graph.
"""


def render_claude_md(tags: list[str]) -> str:
    repos = ", ".join(f"`{t}`" for t in tags) if tags else "(none pushed yet)"
    return _TEMPLATE.format(marker=MARKER, repos=repos)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_claude_md.py`
Expected: 2 passed

- [ ] **Step 5: Write the failing server tests**

Create `tests/test_app.py`:

```python
import time

import anyio

from kgc.server import queries as q
from kgc.server.app import SERVER_NAME, STARTUP_TIMEOUT_SECONDS, build_server, startup_repo_tags
from kgc.server.db import KgcError
from kgc.server.tools import TOOL_NAMES
from tests.fakes import FakeBackend, node_row


def _text(result) -> str:
    return result.content[0].text


def _call(server, name, args):
    return anyio.run(lambda: server.call_tool(name, args))


def test_server_registers_exactly_the_nine_tools_in_order():
    server = build_server(FakeBackend())
    assert server.name == SERVER_NAME == "kgc"
    listed = anyio.run(server.list_tools)
    assert [t.name for t in listed] == list(TOOL_NAMES)
    assert all(t.description for t in listed)
    assert "caller -> callee" in server.instructions


def test_search_description_names_the_indexed_repos_when_known():
    with_tags = anyio.run(build_server(FakeBackend(), repo_tags=["alpha", "beta"]).list_tools)
    without = anyio.run(build_server(FakeBackend()).list_tools)
    search_with = next(t for t in with_tags if t.name == "search").description
    search_without = next(t for t in without if t.name == "search").description
    assert search_with.endswith(" Repos indexed: alpha, beta.")
    assert "Repos indexed" not in search_without


def test_tool_schemas_expose_the_spec_defaults():
    listed = {t.name: t for t in anyio.run(build_server(FakeBackend()).list_tools)}
    props = listed["callers"].input_schema["properties"]
    assert props["depth"]["default"] == 1
    assert props["max_tokens"]["default"] == 1500
    assert listed["impact"].input_schema["properties"]["depth"]["default"] == 2
    assert "max_tokens" not in listed["stats"].input_schema["properties"]
    assert set(listed["stats"].input_schema["properties"]) == {"repo"}


def test_calling_a_tool_returns_its_text():
    row = node_row("a::f", "f()", repo="alpha", source_file="a.py", community=1)
    server = build_server(FakeBackend([(q.SEARCH, [dict(row, score=1.0)])]))
    result = _call(server, "search", {"query": "f"})
    assert result.is_error is False
    assert _text(result) == "alpha  f()  a.py:L1  c1"


def test_backend_errors_come_back_as_plain_text_not_as_tool_errors():
    class _Down(FakeBackend):
        def read(self, query, **params):
            raise KgcError("cannot reach Neo4j at host.example; is the instance paused?")

    result = _call(build_server(_Down()), "stats", {})
    assert result.is_error is False
    assert _text(result) == "cannot reach Neo4j at host.example; is the instance paused?"


def test_unexpected_exceptions_become_internal_error_with_only_the_type_name():
    class _Buggy(FakeBackend):
        def read(self, query, **params):
            raise ValueError("neo4j+s://user:s3cret@host blew up")

    result = _call(build_server(_Buggy()), "externals", {})
    assert _text(result) == "internal error: ValueError"


def test_cypher_tool_refuses_writes_through_the_server_too():
    db = FakeBackend()
    result = _call(build_server(db), "cypher", {"query": "MATCH (n) SET n.x = 1"})
    assert _text(result) == "refused: read-only server"
    assert db.calls == []


def test_startup_repo_tags_reads_tags_or_falls_back_quietly():
    assert startup_repo_tags(FakeBackend([(q.REPO_TAGS, [{"tag": "b"}, {"tag": "a"}])])) == ["b", "a"]

    class _Down(FakeBackend):
        def read(self, query, **params):
            raise KgcError("down")

    assert startup_repo_tags(_Down()) == []


def test_startup_repo_tags_gives_up_after_the_timeout():
    class _Slow(FakeBackend):
        def read(self, query, **params):
            time.sleep(0.5)
            return [{"tag": "late"}]

    assert STARTUP_TIMEOUT_SECONDS == 3.0
    started = time.monotonic()
    assert startup_repo_tags(_Slow(), timeout=0.05) == []
    assert time.monotonic() - started < 0.4
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_app.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.app'`

- [ ] **Step 7: Write `kgc/server/app.py`**

```python
"""The MCP server object: nine tools wired to a Backend, plus startup and serve.

This is the only module that imports `mcp`. Tool bodies live in tools.py; here
each one is wrapped so a KgcError becomes its one-line text and anything else
becomes `internal error: <Type>` - the SDK would otherwise hide our message
behind a generic ToolError.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kgc-startup")
    future = pool.submit(lambda: [r["tag"] for r in backend.read(REPO_TAGS)])
    try:
        return future.result(timeout=timeout)
    except Exception:  # noqa: BLE001 - timeout, KgcError, anything: the fallback is generic text
        return []
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


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
```

- [ ] **Step 8: Run to verify it passes, then the whole suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_app.py tests/test_claude_md.py`
Expected: 11 passed. (mcp 2.3 spells these `input_schema` and `is_error`; both were checked against the installed SDK.)

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 210 passed

- [ ] **Step 9: Commit**

```bash
git add kgc/server/app.py kgc/server/claude_md.py tests/test_app.py tests/test_claude_md.py
git commit -q -m "feat(server): MCPServer with nine tools, startup repo tags, error wrapping; CLAUDE.md template"
```

---

### Task 7: CLI `serve` and `init`, `setup_mcp.py --out`, the project's CLAUDE.md, README, pyproject

**Files:**
- Create: `kgc/server/setup.py`
- Modify: `kgc/cli.py` (two subcommands), `setup_mcp.py` (`--out`), `pyproject.toml`, `README.md`
- Move: `CLAUDE.md` → `verify/arms/treatment/CLAUDE.md`; then write a new `CLAUDE.md`
- Test: `tests/test_setup.py` (new), `tests/test_cli.py` (+4), `tests/test_claude_md.py` (+2), `tests/test_setup_mcp.py` (+1), `tests/test_pyproject.py` (+1)

**Interfaces:**
- Consumes: `kgc.server.app.serve(settings, *, http_port)`, `kgc.server.claude_md.render_claude_md/MARKER`, `kgc.config.load_settings/load_repos`.
- Produces: `kgc.server.setup.build_mcp_config(env_path: Path, python: Path | None = None) -> dict`, `write_private(path: Path, text: str) -> None`, `append_claude_md(path: Path, block: str) -> bool`; CLI `kgc serve [--env PATH] [--http PORT]`, `kgc init [--env PATH] [--out PATH] [--claude-md PATH]`; `setup_mcp.main(argv=None)` with `--out`.

- [ ] **Step 1: Write the failing setup tests**

Create `tests/test_setup.py`:

```python
import json
import stat
from pathlib import Path

from kgc.server.claude_md import MARKER, render_claude_md
from kgc.server.setup import append_claude_md, build_mcp_config, write_private


def test_mcp_config_has_one_server_and_no_secrets(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=neo4j+s://h\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=s3cret\nNEO4J_DATABASE=d\n")
    cfg = build_mcp_config(env, Path("/venv/bin/python"))
    assert list(cfg["mcpServers"]) == ["kgc"]
    kgc = cfg["mcpServers"]["kgc"]
    assert kgc["command"] == "/venv/bin/python"
    assert kgc["args"] == ["-m", "kgc.cli", "serve", "--env", str(env.resolve())]
    assert "env" not in kgc
    raw = json.dumps(cfg)
    for forbidden in ("s3cret", "NEO4J", "neo4j+s://h", '"u"', '"d"'):
        assert forbidden not in raw


def test_mcp_config_defaults_to_the_running_interpreter(tmp_path):
    import sys
    cfg = build_mcp_config(tmp_path / ".env")
    assert cfg["mcpServers"]["kgc"]["command"] == sys.executable


def test_write_private_creates_0600_and_tightens_a_wider_existing_file(tmp_path):
    out = tmp_path / "x.json"
    out.write_text("stale and much longer than the replacement text" * 3)
    out.chmod(0o644)
    write_private(out, "{}")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert out.read_text() == "{}"


def test_append_claude_md_creates_appends_and_is_idempotent(tmp_path):
    path = tmp_path / "CLAUDE.md"
    block = render_claude_md(["alpha"])
    assert append_claude_md(path, block) is True
    assert path.read_text() == block

    path.write_text("# Existing notes\n\nkeep me\n")
    assert append_claude_md(path, block) is True
    text = path.read_text()
    assert text.startswith("# Existing notes\n\nkeep me\n\n" + MARKER)
    assert append_claude_md(path, block) is False
    assert path.read_text() == text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest -q -W error tests/test_setup.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'kgc.server.setup'`

- [ ] **Step 3: Write `kgc/server/setup.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest -q -W error tests/test_setup.py`
Expected: 4 passed

- [ ] **Step 5: Write the failing CLI tests**

Append to `tests/test_cli.py`:

```python
import json
import stat


def _env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=neo4j+s://h.example\nNEO4J_USERNAME=u\n"
                   "NEO4J_PASSWORD=s3cret\nNEO4J_DATABASE=d\n", encoding="utf-8")
    return env


def _repos(tmp_path, monkeypatch):
    repos = tmp_path / "repos.yaml"
    repos.write_text("repos:\n  - path: /tmp/a\n    tag: alpha\n  - path: /tmp/b\n    tag: beta\n",
                     encoding="utf-8")
    monkeypatch.setattr("kgc.cli.REPOS_PATH", repos)
    return repos


def test_init_writes_a_secret_free_mcp_json_and_prints_the_claude_md_block(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    _repos(tmp_path, monkeypatch)
    out = tmp_path / ".mcp.json"

    assert main(["init", "--env", str(env), "--out", str(out)]) == 0

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    data = json.loads(out.read_text(encoding="utf-8"))
    assert list(data["mcpServers"]) == ["kgc"]
    assert data["mcpServers"]["kgc"]["args"][-2:] == ["--env", str(env.resolve())]
    raw = out.read_text(encoding="utf-8")
    for forbidden in ("s3cret", "NEO4J", "h.example", '"u"'):
        assert forbidden not in raw
    printed = capsys.readouterr().out
    assert "Add this to your CLAUDE.md" in printed
    assert "# kgc code graph" in printed and "`alpha`, `beta`" in printed


def test_init_can_append_the_block_to_a_claude_md_instead_of_printing(tmp_path, monkeypatch, capsys):
    env = _env(tmp_path)
    _repos(tmp_path, monkeypatch)
    target = tmp_path / "CLAUDE.md"
    target.write_text("# mine\n", encoding="utf-8")

    assert main(["init", "--env", str(env), "--out", str(tmp_path / ".mcp.json"),
                 "--claude-md", str(target)]) == 0

    text = target.read_text(encoding="utf-8")
    assert text.startswith("# mine\n\n# kgc code graph")
    printed = capsys.readouterr().out
    assert "appended kgc block to" in printed
    assert "Add this to your CLAUDE.md" not in printed


def test_init_refuses_a_broken_env_without_writing_anything(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=neo4j+s://h\n", encoding="utf-8")   # three keys missing
    _repos(tmp_path, monkeypatch)
    out = tmp_path / ".mcp.json"

    assert main(["init", "--env", str(env), "--out", str(out)]) == 1

    assert not out.exists()
    err = capsys.readouterr().err
    assert "missing required key" in err
    assert "s3cret" not in err


def test_serve_loads_settings_and_hands_off_to_the_server(tmp_path, monkeypatch):
    env = _env(tmp_path)
    recorded = {}

    def fake_serve(settings, *, http_port=None):
        recorded["uri"] = settings.neo4j_uri
        recorded["http_port"] = http_port

    monkeypatch.setattr("kgc.server.app.serve", fake_serve)

    assert main(["serve", "--env", str(env)]) == 0
    assert recorded == {"uri": "neo4j+s://h.example", "http_port": None}

    assert main(["serve", "--env", str(env), "--http", "8765"]) == 0
    assert recorded["http_port"] == 8765
```

- [ ] **Step 6: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_cli.py`
Expected: 4 new FAIL with `argparse` errors (`invalid choice: 'init'` / `'serve'`), surfacing as `SystemExit`

- [ ] **Step 7: Add the subcommands to `kgc/cli.py`**

Add `import json` to the imports. Add these two functions before `main`:

```python
def _cmd_serve(args: argparse.Namespace) -> int:
    from kgc.server.app import serve

    settings = load_settings(Path(args.env))
    serve(settings, http_port=args.http)
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    from kgc.server.claude_md import render_claude_md
    from kgc.server.setup import append_claude_md, build_mcp_config, write_private

    env_path = Path(args.env)
    try:
        load_settings(env_path)          # validate now; never echo a value
        repos = load_repos(REPOS_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print(f"init refused: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    write_private(out, json.dumps(build_mcp_config(env_path, Path(sys.executable)), indent=2) + "\n")
    print(f"wrote {out} (mode 600; credentials stay in {env_path})")

    block = render_claude_md([r.tag for r in repos])
    if args.claude_md:
        target = Path(args.claude_md)
        changed = append_claude_md(target, block)
        print(f"{'appended kgc block to' if changed else 'kgc block already present in'} {target}")
    else:
        print("\nAdd this to your CLAUDE.md:\n")
        print(block, end="")
    return 0
```

In `main`, after the `status` parser:

```python
    p_serve = sub.add_parser("serve", help="run the kgc MCP server (stdio; --http PORT for HTTP)")
    p_serve.add_argument("--env", default=str(ENV_PATH), help="path to .env (default: ./.env)")
    p_serve.add_argument("--http", type=int, default=None, metavar="PORT",
                         help="serve over streamable HTTP on 127.0.0.1:PORT instead of stdio (no auth)")
    p_serve.set_defaults(func=_cmd_serve)

    p_init = sub.add_parser("init", help="write .mcp.json for this checkout and print the CLAUDE.md block")
    p_init.add_argument("--env", default=str(ENV_PATH), help="path to .env (default: ./.env)")
    p_init.add_argument("--out", default=".mcp.json", help="where to write the MCP config (default: ./.mcp.json)")
    p_init.add_argument("--claude-md", default=None, metavar="PATH",
                        help="append the block to this CLAUDE.md instead of printing it")
    p_init.set_defaults(func=_cmd_init)
```

Update the module docstring's first line to `"""Command line entry point: doctor / build / push / status / serve / init.`

- [ ] **Step 8: Run to verify they pass**

Run: `.venv/bin/python -m pytest -q -W error tests/test_cli.py`
Expected: all pass (existing + 4)

- [ ] **Step 9: `setup_mcp.py --out` (treatment-arm config can live outside `.mcp.json`)**

Append to `tests/test_setup_mcp.py`:

```python
def test_main_out_flag_writes_the_treatment_config_elsewhere(tmp_path, monkeypatch):
    out = tmp_path / "treatment-mcp.json"
    monkeypatch.setattr("setup_mcp.load_settings", lambda path: SETTINGS)
    monkeypatch.setattr("setup_mcp.global_graph_path", lambda: Path("/tmp/g.json"))

    assert setup_mcp.main(["--out", str(out)]) == 0

    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert set(json.loads(out.read_text(encoding="utf-8"))["mcpServers"]) == {"graphify", "neo4j"}
```

Run: `.venv/bin/python -m pytest -q -W error tests/test_setup_mcp.py` → the new test FAILS (`main() takes 0 positional arguments`).

Replace `main` in `setup_mcp.py`:

```python
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
```

Update the module docstring's second paragraph to: `This is the TREATMENT-ARM config for the verification harness (graphify + mcp-neo4j-cypher). Daily use is `kgc init`. Plan 2 deletes this file with graphify.`

Run: `.venv/bin/python -m pytest -q -W error tests/test_setup_mcp.py` → all pass.

- [ ] **Step 10: Move the old CLAUDE.md to the treatment arm and write the kgc one**

```bash
cd /Users/osianshelley/Projects/knowledge-graph-code
mkdir -p verify/arms/treatment
git mv CLAUDE.md verify/arms/treatment/CLAUDE.md
.venv/bin/python - <<'PY'
from pathlib import Path
from kgc.config import load_repos
from kgc.server.claude_md import render_claude_md
tags = [r.tag for r in load_repos(Path("repos.yaml"))]
Path("CLAUDE.md").write_text(render_claude_md(tags), encoding="utf-8")
print(Path("CLAUDE.md").read_text())
PY
```

Append to `tests/test_claude_md.py`:

```python
from pathlib import Path

from kgc.config import load_repos

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_project_claude_md_is_exactly_the_rendered_block():
    tags = [r.tag for r in load_repos(PROJECT_ROOT / "repos.yaml")]
    assert (PROJECT_ROOT / "CLAUDE.md").read_text(encoding="utf-8") == render_claude_md(tags)


def test_treatment_arm_keeps_the_pre_kgc_instructions_verbatim():
    text = (PROJECT_ROOT / "verify" / "arms" / "treatment" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "read_neo4j_cypher" in text and "query_graph" in text
    assert MARKER not in text
```

Run: `.venv/bin/python -m pytest -q -W error tests/test_claude_md.py` → 4 passed.

- [ ] **Step 11: pyproject declares the SDK floor; README**

Append to `tests/test_pyproject.py`:

```python
def test_mcp_sdk_v2_is_declared_explicitly():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    assert any(dep.startswith("mcp>=2.3,<3") for dep in deps), (
        "kgc.server.app imports mcp.server.mcpserver.MCPServer, a 2.x API; the floor "
        f"must be explicit rather than inherited from graphifyy[mcp]'s 'mcp<3,>=1'. Got: {deps}"
    )
```

Add `"mcp>=2.3,<3",` to `[project] dependencies` in `pyproject.toml` (after `"graphifyy[mcp]==0.9.74",`), then `.venv/bin/pip install -e . -q` and run the test.

In `README.md`, replace the body of `## Setup` (everything between that heading and `## Run order`) with:

```markdown
```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env            # then fill in the four NEO4J_* values; .env is gitignored, mode 0600
.venv/bin/kgc doctor            # connection check; prints no secrets
.venv/bin/kgc init              # writes .mcp.json (no secrets inside) and prints the CLAUDE.md block
```

`kgc init --claude-md CLAUDE.md` appends the block instead of printing it. This
project's own `CLAUDE.md` is exactly that block, so a fresh checkout needs only
the `.mcp.json`.
```

Replace the body of `## Run order` with:

```markdown
```bash
.venv/bin/kgc build             # extract every repo in repos.yaml (zero tokens), ~40 s for five repos
.venv/bin/kgc push              # reap + upsert each repo into Neo4j; ensures indexes; writes a Repo node last
.venv/bin/kgc serve             # the MCP server Claude Code spawns via .mcp.json (stdio)
```

Run from the project root: `.env` and `repos.yaml` are resolved relative to the
current directory. `kgc serve --http PORT` serves over HTTP on localhost with no
authentication, for experiments only.

The nine tools: `search`, `describe`, `callers`, `callees`, `impact`,
`externals`, `cross_repo`, `stats`, `cypher` (read-only). `stats` names the
file extensions the extractor did not parse — the graph's blind spots.
```

- [ ] **Step 12: Whole suite, then commit**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 222 passed

```bash
git add kgc/server/setup.py kgc/cli.py setup_mcp.py pyproject.toml README.md CLAUDE.md verify/arms/treatment/CLAUDE.md tests/test_setup.py tests/test_cli.py tests/test_setup_mcp.py tests/test_claude_md.py tests/test_pyproject.py
git commit -q -m "feat(cli): kgc serve and kgc init; setup_mcp --out for the treatment arm; project CLAUDE.md describes kgc"
```

---

### Task 8: Harness — the `kgc` arm

**Files:**
- Modify: `verify/run_arm.py`, `tests/test_verify_runner.py`, `.gitignore`, `verify/results.md`, `README.md` (Verification section)

**Interfaces:**
- Consumes: `kgc.server.claude_md.render_claude_md`, `verify/arms/treatment/CLAUDE.md` (Task 7).
- Produces: `ARMS = ("control", "treatment", "kgc")`, `MCP_ARMS = ("treatment", "kgc")`, `ARM_MCP_CONFIG: dict[str, Path]`, `TREATMENT_CLAUDE_MD: Path`, `EXPECTED_SERVERS: dict[str, frozenset[str]]`, `Invocation(argv, cwd: Path | None, claude_md: str | None = None)`, `isolation_ok(arm, init) -> bool | None`, `_materialised_cwd(inv)` context manager, `kgc_calls` in `parse_stream_json` and the CSV, `--arm {control,treatment,kgc,both,all}` default `all`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_verify_runner.py`, change the import of `MCP_CONFIG_PATH` to import `ARM_MCP_CONFIG, EXPECTED_SERVERS, MCP_ARMS, TREATMENT_CLAUDE_MD, isolation_ok` (keep everything else imported as before). Delete `test_treatment_invocation_uses_project_cwd_and_only_the_projects_mcp_config` and `test_dry_run_prints_both_arms_by_default`. Edit `test_csv_header_matches_the_documented_columns` so the expected list reads `..., "graphify_calls", "neo4j_calls", "kgc_calls", "answer_file"`. In `test_parse_stream_json_tallies_mcp_tool_calls_by_server`, add a fourth assistant event with `{"type": "tool_use", "name": "mcp__kgc__callers", "input": {}}` and assert `parsed["kgc_calls"] == 1`. Then add:

```python
from kgc.server.claude_md import MARKER, render_claude_md


def test_arms_are_control_treatment_and_kgc():
    assert ARMS == ("control", "treatment", "kgc")
    assert MCP_ARMS == ("treatment", "kgc")
    assert set(ARM_MCP_CONFIG) == set(MCP_ARMS)
    assert ARM_MCP_CONFIG["treatment"].name == "treatment-mcp.json"
    assert ARM_MCP_CONFIG["kgc"].name == "kgc-mcp.json"
    assert EXPECTED_SERVERS == {"control": frozenset(), "treatment": frozenset({"graphify", "neo4j"}),
                                "kgc": frozenset({"kgc"})}


def test_mcp_arms_use_their_own_mcp_config_and_carry_their_own_claude_md():
    q = _first_question()
    for arm in MCP_ARMS:
        inv = build_invocation(arm, q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
        assert inv.cwd is None                                   # materialised per run
        idx = inv.argv.index("--mcp-config")
        assert inv.argv[idx + 1] == str(ARM_MCP_CONFIG[arm])
        assert "--strict-mcp-config" in inv.argv
        assert q.text in " ".join(inv.argv)
    treatment = build_invocation("treatment", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
    kgc = build_invocation("kgc", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
    assert treatment.claude_md == TREATMENT_CLAUDE_MD.read_text(encoding="utf-8")
    assert "read_neo4j_cypher" in treatment.claude_md and MARKER not in treatment.claude_md
    assert kgc.claude_md == render_claude_md(sorted(repo_path_map()))


def test_control_arm_still_runs_in_the_question_repo_with_no_claude_md():
    q = _first_question()
    inv = build_invocation("control", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)
    assert inv.cwd == repo_path_map()[q.repo]
    assert inv.claude_md is None


def test_mcp_arm_runs_in_a_temp_cwd_holding_only_its_claude_md(monkeypatch):
    from verify.run_arm import _execute

    recorded = {}

    def fake_run(argv, **kwargs):
        cwd = Path(kwargs["cwd"])
        recorded["cwd"] = cwd
        recorded["files"] = sorted(p.name for p in cwd.iterdir())
        recorded["claude_md"] = (cwd / "CLAUDE.md").read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            argv, 0, stdout=json.dumps({"type": "result", "result": "ok", "usage": {}}), stderr="",
        )

    monkeypatch.setattr("verify.run_arm.subprocess.run", fake_run)
    q = _first_question()
    inv = build_invocation("kgc", q, run=1, model="sonnet", permission_mode="default", allowed_tools=None)

    _execute(inv, timeout=5)

    assert recorded["cwd"] != PROJECT_ROOT and PROJECT_ROOT not in recorded["cwd"].parents
    assert recorded["files"] == ["CLAUDE.md"]
    assert recorded["claude_md"] == inv.claude_md
    assert not recorded["cwd"].exists(), "temp cwd is removed after the run"


def test_isolation_ok_compares_the_init_servers_to_the_arms_expectation():
    assert isolation_ok("kgc", {"mcp_servers": [{"name": "kgc", "status": "connected"}]}) is True
    assert isolation_ok("kgc", {"mcp_servers": [{"name": "kgc"}, {"name": "neo4j"}]}) is False
    assert isolation_ok("treatment", {"mcp_servers": [{"name": "graphify"}, {"name": "neo4j"}]}) is True
    assert isolation_ok("control", {"mcp_servers": []}) is True
    assert isolation_ok("control", {}) is None


def test_dry_run_prints_all_three_arms_by_default(capsys):
    assert main(["--dry-run", "--questions", "1", "--runs", "1"]) == 0
    out = capsys.readouterr().out
    for arm in ARMS:
        assert f"{arm}-q01-r1" in out


def test_arm_both_means_control_and_treatment_only(capsys):
    assert main(["--dry-run", "--questions", "1", "--runs", "1", "--arm", "both"]) == 0
    out = capsys.readouterr().out
    assert "control-q01-r1" in out and "treatment-q01-r1" in out
    assert "kgc-q01-r1" not in out


def test_run_one_records_kgc_calls_and_isolation(tmp_path, monkeypatch):
    # Copy the monkeypatch lines from test_run_one_persists_malformed_lines_in_json_and_csv
    # VERBATIM (they redirect every path _run_one writes to, including whatever
    # answer_file is made relative to); the two lines below are the minimum and
    # may need that test's extra ones. Then fake _execute with the stream.
    import verify.run_arm as ra
    monkeypatch.setattr(ra, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(ra, "RESULTS_CSV", tmp_path / "results.csv")
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init", "cwd": "/tmp/x",
                    "mcp_servers": [{"name": "kgc", "status": "connected"}]}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__kgc__search", "input": {"query": "x"}}]}}),
        json.dumps({"type": "result", "is_error": False, "num_turns": 2, "duration_ms": 5,
                    "total_cost_usd": 0.0, "result": "answer", "usage": {"input_tokens": 1}}),
    ])
    monkeypatch.setattr(ra, "_execute", lambda inv, *, timeout: stream)
    q = _first_question()

    ra._run_one("kgc", q, 1, model="sonnet", permission_mode="default", allowed_tools=None,
                repos=repo_path_map(), timeout=1.0)

    data = json.loads((tmp_path / "runs" / "kgc-q01-r1.json").read_text(encoding="utf-8"))
    assert data["isolation_ok"] is True
    assert data["kgc_calls"] == 1
    header, row = (tmp_path / "results.csv").read_text(encoding="utf-8").splitlines()[:2]
    assert header.split(",")[-2] == "kgc_calls"
    assert row.split(",")[header.split(",").index("kgc_calls")] == "1"


def test_results_csv_with_an_older_header_is_refused_not_corrupted(tmp_path):
    from verify.run_arm import _ensure_csv_header
    path = tmp_path / "results.csv"
    path.write_text("question,arm,run,old\n1,control,1,x\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="older header"):
        _ensure_csv_header(path)
```

(`pytest` and `subprocess` are already imported at the top of the test file; add `import pytest` if not.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_verify_runner.py`
Expected: FAIL at import (`ImportError: cannot import name 'ARM_MCP_CONFIG'`)

- [ ] **Step 3: Modify `verify/run_arm.py`**

Imports: add `import contextlib`, `import tempfile`, and `from kgc.server.claude_md import render_claude_md`.

Replace the constants block from `MCP_CONFIG_PATH = ...` through `ARMS = ("control", "treatment")` so it reads (keep the other constants in between exactly as they are, only the lines named here change):

```python
TREATMENT_CLAUDE_MD = HERE / "arms" / "treatment" / "CLAUDE.md"
ARM_MCP_CONFIG = {
    "treatment": HERE / "treatment-mcp.json",   # python setup_mcp.py --out verify/treatment-mcp.json
    "kgc": HERE / "kgc-mcp.json",               # kgc init --out verify/kgc-mcp.json
}
```

```python
CSV_HEADER = [
    "question", "arm", "run", "model", "input_tokens", "output_tokens",
    "cache_creation_tokens", "cache_read_tokens", "context_tokens",
    "total_cost_usd", "duration_ms", "num_turns", "permission_denials",
    "contamination", "malformed_lines", "graphify_calls", "neo4j_calls", "kgc_calls",
    "answer_file",
]
```

```python
ARMS = ("control", "treatment", "kgc")
MCP_ARMS = ("treatment", "kgc")
# What the stream's init event must report for a run to count. A kgc run that
# also loaded another server, or a treatment run missing one, is invalid.
EXPECTED_SERVERS = {
    "control": frozenset(),
    "treatment": frozenset({"graphify", "neo4j"}),
    "kgc": frozenset({"kgc"}),
}
```

`Invocation`:

```python
@dataclass(frozen=True)
class Invocation:
    """A `claude` invocation this script would run. `cwd` is the directory to
    run in, or None for the MCP arms, whose cwd is a per-run temporary
    directory holding only `claude_md` as CLAUDE.md (see module docstring)."""
    argv: list[str]
    cwd: Path | None
    claude_md: str | None = None
```

Tail of `build_invocation` (replace from `if arm == "treatment":` to the end):

```python
    if arm in MCP_ARMS:
        argv += ["--mcp-config", str(ARM_MCP_CONFIG[arm]), "--strict-mcp-config"]
        claude_md = (
            TREATMENT_CLAUDE_MD.read_text(encoding="utf-8") if arm == "treatment"
            else render_claude_md(sorted(repos))
        )
        return Invocation(argv=argv, cwd=None, claude_md=claude_md)
    argv += ["--mcp-config", str(EMPTY_MCP_PATH), "--strict-mcp-config"]
    return Invocation(argv=argv, cwd=repos[question.repo])
```

Add before `_execute`:

```python
@contextlib.contextmanager
def _materialised_cwd(inv: Invocation):
    """The MCP arms run in a fresh temp dir containing only their CLAUDE.md.
    Claude Code reads CLAUDE.md from the cwd and every parent, so running
    under the project root would also load the project's (kgc) CLAUDE.md
    into the treatment arm."""
    if inv.claude_md is None:
        yield inv.cwd
        return
    with tempfile.TemporaryDirectory(prefix="kgc-arm-") as tmp:
        (Path(tmp) / "CLAUDE.md").write_text(inv.claude_md, encoding="utf-8")
        yield Path(tmp)


def isolation_ok(arm: str, init: dict) -> bool | None:
    servers = init.get("mcp_servers")
    if servers is None:
        return None
    names = {s.get("name") if isinstance(s, dict) else str(s) for s in servers}
    return names == set(EXPECTED_SERVERS[arm])
```

`_execute`: wrap the `subprocess.run` call:

```python
def _execute(inv: Invocation, *, timeout: float) -> str:
    with _materialised_cwd(inv) as cwd:
        try:
            proc = subprocess.run(
                inv.argv, cwd=cwd, capture_output=True, text=True, check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RunFailed(
                f"claude timed out after {timeout}s", stderr_tail=_stderr_tail(exc.stderr),
            ) from exc
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RunFailed(
            f"claude exited {proc.returncode} with no stdout",
            stderr_tail=_stderr_tail(proc.stderr),
        )
    return proc.stdout
```

`parse_stream_json`: add `kgc_calls = 0` beside the other counters, the branch `elif name.startswith("mcp__kgc__"): kgc_calls += 1` after the neo4j branch, and `"kgc_calls": kgc_calls,` in the returned dict. Update the docstring's prefix list to `(mcp__graphify__* / mcp__neo4j__* / mcp__kgc__*)`.

`_run_one`: compute `iso = isolation_ok(arm, parsed["init"])` after parsing; add `"isolation_ok": iso,` and `"kgc_calls": parsed["kgc_calls"],` to the JSON; add `"kgc_calls": parsed["kgc_calls"],` to the CSV row after `neo4j_calls`; after the `print(f"  wrote ...")` line add:

```python
    if iso is False:
        print(f"  WARNING: isolation_ok=False for {label}: servers were "
              f"{[s.get('name') for s in parsed['init'].get('mcp_servers', [])]}, "
              f"expected {sorted(EXPECTED_SERVERS[arm])}")
```

`_ensure_csv_header`: where it currently returns early because the file exists, first compare the existing first line:

```python
    if path.exists():
        first = path.read_text(encoding="utf-8").splitlines()[:1]
        if first and first[0] != ",".join(CSV_HEADER):
            raise RuntimeError(
                f"{path} has an older header; move it aside and re-run (its rows would misalign)"
            )
        return
```

Arg parser: `p.add_argument("--arm", choices=(*ARMS, "both", "all"), default="all", help="one arm, 'both' (control+treatment), or 'all' (default)")`. In `main`:

```python
    if args.arm == "all":
        arms = ARMS
    elif args.arm == "both":
        arms = ("control", "treatment")
    else:
        arms = (args.arm,)
```

Dry-run printing: run `grep -n "inv.cwd" verify/run_arm.py`; in the dry-run print replace the cwd expression with `str(inv.cwd) if inv.cwd is not None else "<tempdir with the arm's CLAUDE.md>"`.

Module docstring, section "Arms": replace the treatment paragraph with:

```
- **treatment** and **kgc** run with `--mcp-config verify/<arm>-mcp.json
  --strict-mcp-config`, so that arm's server(s) are the ONLY MCP servers in the
  session (treatment: graphify + neo4j, generated by `python setup_mcp.py --out
  verify/treatment-mcp.json`; kgc: the kgc server, generated by `kgc init --out
  verify/kgc-mcp.json`). Each runs in a per-run temporary directory holding
  only that arm's CLAUDE.md (treatment: verify/arms/treatment/CLAUDE.md, the
  pre-kgc text; kgc: the block `kgc init` prints). Claude Code loads CLAUDE.md
  from the cwd and every parent, which is why neither arm runs under the
  project root. The init event's `mcp_servers` is compared with the arm's
  expectation and recorded as `isolation_ok`.
```

`--allowed-tools` help: change the treatment example to `'mcp__graphify mcp__neo4j Read Grep Glob' for treatment or 'mcp__kgc Read Grep Glob' for kgc`.

- [ ] **Step 4: Run the harness tests, then the whole suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_verify_runner.py`
Expected: all pass

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 229 passed (222, minus the two deleted harness tests, plus nine new)

- [ ] **Step 5: `.gitignore`, `results.md`, README Verification**

Append to `.gitignore` under the `.mcp.json` comment:

```
# Per-arm MCP configs for the harness: treatment's carries the password,
# kgc's carries machine-specific absolute paths.
verify/*-mcp.json
```

In `verify/results.md`, under `## Results`, add this paragraph before the table:

```markdown
**Third arm, `kgc`** (added 2026-10-04): the bespoke server from
`docs/specs/2026-10-04-kgc-bespoke-mcp-design.md`, same twelve questions, same
metrics, tool calls tallied as `kgc_calls`. Acceptance: `kgc` matches or beats
`treatment` on correct answers while using no more `context_tokens`, and the
fraction of runs with at least one graph-tool call is reported alongside. The
`treatment` baseline must exist before `kgc` is judged.
```

Under `## How to run`, add before the first command:

```markdown
```bash
.venv/bin/kgc push                                       # once after Plan 1 lands: creates kgc_text + Repo nodes
.venv/bin/python setup_mcp.py --out verify/treatment-mcp.json
.venv/bin/kgc init --out verify/kgc-mcp.json
.venv/bin/python verify/run_arm.py --questions 1 --runs 1 --arm kgc   # pre-flight: isolation_ok, kgc_calls > 0
```
```

In `README.md` `## Verification`, add the same four commands and one sentence: `The harness has three arms: control (no MCP), treatment (graphify + mcp-neo4j-cypher) and kgc (this server).`

- [ ] **Step 6: Commit**

```bash
git add verify/run_arm.py tests/test_verify_runner.py .gitignore verify/results.md README.md
git commit -q -m "feat(verify): kgc arm - per-arm mcp config, isolated CLAUDE.md cwd, kgc_calls, isolation_ok"
```

---

### Task 9: Opt-in read-only integration suite

**Files:**
- Create: `tests/integration/__init__.py`, `tests/integration/test_live_tools.py`

**Interfaces:**
- Consumes: everything above; a reachable instance and a `.env` in the project root.
- Precondition for a *meaningful* run: the human has run `kgc push` once since Task 1 (creates `kgc_text` and the `Repo` nodes). Until then `search` and `describe` will report a query error about the missing index — that is the expected failure mode, not a bug. **The implementer must not run `kgc push`.** Without `KGC_INTEGRATION=1` the whole module skips.

- [ ] **Step 1: Write the suite**

`tests/integration/__init__.py` is empty. Create `tests/integration/test_live_tools.py`:

```python
"""Read-only checks against the real instance. Opt in with KGC_INTEGRATION=1.

Asserts shape, not content: the graph changes with every push. Nothing here
writes; the Backend only ever opens read transactions.
"""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KGC_INTEGRATION"),
    reason="set KGC_INTEGRATION=1 to run read-only against the real Neo4j instance",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOS = {"knowledge-retrieval", "graphify", "neural-particle-method", "diffusion-bridge", "claude"}


@pytest.fixture(scope="module")
def db():
    from kgc.config import load_settings
    from kgc.server.db import Backend

    backend = Backend(load_settings(PROJECT_ROOT / ".env"))
    yield backend
    backend.close()


def test_stats_lists_the_pushed_repos(db):
    from kgc.server import tools
    out = tools.stats(db)
    assert "[knowledge-retrieval]" in out
    assert "externals:" in out


def test_search_finds_push_one_by_words(db):
    from kgc.server import tools
    assert "push_one()" in tools.search(db, "push one zotero", repo="knowledge-retrieval")


def test_describe_resolves_a_path_scoped_symbol(db):
    from kgc.server import tools
    out = tools.describe(db, "pipeline/zotero/push.py::push_one")
    assert out.startswith("knowledge-retrieval  push_one()  pipeline/zotero/push.py:")
    assert "out: " in out


def test_callees_of_push_one_are_edges(db):
    from kgc.server import tools
    out = tools.callees(db, "pipeline/zotero/push.py::push_one")
    assert out.startswith("callees of ") and "-calls->" in out


def test_callers_of_build_from_json_report_or_say_none(db):
    from kgc.server import tools
    out = tools.callers(db, "graphify/build.py::build_from_json", depth=1)
    assert out.startswith("callers of ") or out.startswith("no callers of ")


def test_externals_shared_by_three_repos(db):
    from kgc.server import tools
    out = tools.externals(db, min_repos=3)
    assert out == "none" or all("repos:" in line for line in out.splitlines())


def test_cross_repo_claude_graphify_is_none(db):
    from kgc.server import tools
    assert tools.cross_repo(db, "claude", "graphify") == "none"


def test_impact_by_file(db):
    from kgc.server import tools
    out = tools.impact(db, files=["pipeline/zotero/push.py"])
    assert "dependents" in out or out.startswith("nothing depends on")


def test_cypher_reads_and_refuses_writes(db):
    from kgc.server import tools
    out = tools.cypher(db, "MATCH (n:CodeNode) RETURN n.repo AS repo, count(*) AS n ORDER BY n DESC LIMIT 3")
    assert out.splitlines()[0] == "repo\tn"
    assert tools.cypher(db, "MATCH (n) SET n.x = 1") == "refused: read-only server"


def test_server_builds_with_the_live_tags(db):
    from kgc.server.app import build_server, startup_repo_tags
    tags = startup_repo_tags(db)
    assert set(tags) <= REPOS
    build_server(db, repo_tags=tags)
```

- [ ] **Step 2: Verify it skips by default and is collected**

Run: `.venv/bin/python -m pytest -q -W error tests/integration`
Expected: `10 skipped`

Run: `.venv/bin/python -m pytest -q -W error`
Expected: 229 passed, 10 skipped

- [ ] **Step 3: Commit**

```bash
git add tests/integration
git commit -q -m "test: opt-in read-only integration suite (KGC_INTEGRATION=1) for the nine tools"
```

---

## After the plan lands — human steps (the agent must not run these)

1. `cd ~/Projects/knowledge-graph-code && .venv/bin/kgc push` — creates `kgc_text`, the constraints, and a `Repo` node per repo. Writes to Neo4j, so it is yours to run.
2. `.venv/bin/python setup_mcp.py --out verify/treatment-mcp.json` and `.venv/bin/kgc init --out verify/kgc-mcp.json`, then `.venv/bin/kgc init` for the daily `.mcp.json`.
3. `KGC_INTEGRATION=1 .venv/bin/python -m pytest -q tests/integration` — read-only smoke against the live graph.
4. Pre-flight the arm: `.venv/bin/python verify/run_arm.py --questions 1 --runs 1 --arm kgc`; in `verify/runs/kgc-q01-r1.json` confirm `isolation_ok: true`, `kgc_calls > 0`, `permission_denials: 0`.
5. The experiment: `.venv/bin/python verify/run_arm.py --runs 2` (all three arms), judge correctness against the code, fill `verify/results.md`.

## Self-review against the spec

- **Spec coverage.** Nine tools with the spec signatures (Task 5/6); symbol-resolution tiers and ambiguity rule (Task 4); full-text search with the external factor and rationale follow-through (Task 4 `SEARCH`); 1,500-token default and 4-chars/token cutter (Task 2); read-only twice over and the guard with literal stripping (Task 3); 10 s timeout (Task 3); error shapes (Tasks 3, 5, 6); lazy driver and startup description with 3 s timeout (Tasks 3, 6); `kgc serve --http` (Task 7); `kgc init` with 0600 and no credentials (Task 7); `graph/schema.py` constraints, indexes, legacy-index drop, `Repo` node last (Task 1); `stats` incomplete-push flag and `not recorded` (Task 5); harness arm with `kgc_calls` (Task 8); opt-in integration suite (Task 9); 107 existing tests preserved at every commit.
- **Deviations from the spec, deliberate:** `.mcp.json` runs `python -m kgc.cli serve` rather than `<venv>/bin/kgc serve` (no dependency on the console-script shim); the guard handles escaped quotes (spec amended in Task 3 Step 9); `setup_mcp.py` survives until Plan 2; MCP arms run in a temp cwd (reason in the header).
- **Type consistency.** `Backend.read(query, **params) -> list[dict]` is the only I/O signature and is what `FakeBackend` mimics. `Node`/`Resolution` names are used identically in Tasks 4, 5. `neighbours(direction, rels, depth)` and `impact(depth)` take the same arguments in Tasks 4 and 5. `render_claude_md(tags)` and `MARKER` are used identically in Tasks 6, 7, 8. `Invocation.claude_md` is introduced in Task 8 and nowhere earlier.
