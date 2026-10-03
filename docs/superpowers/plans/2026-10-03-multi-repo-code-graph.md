# Multi-Repo Code Graph POC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make five local repositories queryable from a Claude Code session via a merged code graph, served by graphify's stdio MCP for cheap retrieval and Neo4j Aura for precise Cypher, then measure whether it actually saves tokens.

**Architecture:** A small Python package (`kgc`) wrapping graphify's library API. `build` runs deterministic code-only AST extraction per repo and merges each into graphify's global graph (which handles ID namespacing, stale pruning and cross-repo linking itself). `push` reaps a repo's slice from Neo4j by its `repo` property then re-pushes it, because graphify's exporter is MERGE-only and never deletes. Two MCP servers are configured for Claude Code: graphify stdio (token-budgeted) and `mcp-neo4j-cypher` (read-only).

**Tech Stack:** Python 3.11+, `graphifyy` (library, not CLI), `neo4j` Python driver, `pyyaml`, `pytest`. Neo4j AuraDB Free.

## Global Constraints

- **Project root:** `/Users/osianshelley/Projects/knowledge-graph-code`. All paths below are relative to it.
- **Spec:** `docs/specs/2026-10-03-multi-repo-code-graph-design.md`. Read it before Task 1.
- **Zero token spend during extraction.** Code-only extraction must be provably deterministic AST work. Never invoke graphify's semantic/LLM stage. Any task that could dispatch an LLM subagent is a plan violation.
- **Credentials live only in `.env`** (already present, mode 0600, gitignored). Never hardcode, never commit, never print a password — not in logs, errors, or test output.
- **`.env` keys, verbatim:** `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `AURA_INSTANCEID`, `AURA_INSTANCENAME`.
- **Neo4j is read-only from the agent's side.** `mcp-neo4j-cypher` is configured without `write-cypher`. Only `kgc` writes.
- **The five repos, with exact tags** (from the spec, decided 2026-10-03):
  | path | tag |
  |---|---|
  | `~/Projects/knowledge-retrieval` | `knowledge-retrieval` |
  | `~/Projects/graphify` | `graphify` |
  | `~/Projects/neural_particle_method` | `neural-particle-method` |
  | `~/Projects/diffusion-bridge` | `diffusion-bridge` |
  | `~/Projects/claude` | `claude` |
- **Tags are permanent.** A tag is the Neo4j reap key. Renaming one orphans that repo's nodes.
- **Pin `graphifyy`.** It is fast-moving and `~/.graphify/global-graph.json` is shared with any CLI install. Pin an exact version in `pyproject.toml`.
- **Never `DETACH DELETE` unscoped.** Every delete must carry a `repo` predicate or an `external = true` + orphan predicate.
- **Verified graphify API — use these exact signatures:**
  ```python
  graphify.detect.detect(root: Path, *, follow_symlinks=None, google_workspace=None,
                         extra_excludes=None, cache_root=None, gitignore=True) -> dict
  graphify.extract.collect_files(target: Path, *, follow_symlinks=False, root=None) -> list[Path]
  graphify.extract.extract(paths: list[Path], cache_root=None, *, root=None,
                           parallel=True, max_workers=None, ...) -> dict
  graphify.build.build_from_json(extraction: dict, *, directed=False, root=None) -> nx.Graph
  graphify.global_graph.global_add(source_path: Path, repo_tag: str) -> dict
  graphify.global_graph.global_remove(repo_tag: str) -> int
  graphify.global_graph.global_list() -> dict
  graphify.global_graph.global_path() -> Path
  ```
  `global_add` returns keys: `repo_tag`, `nodes_added`, `nodes_removed`, `skipped`, `cross_repo_calls`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Deps and pytest config. Create. |
| `repos.yaml` | The repo inventory. The only file deciding scope. Create. |
| `kgc/__init__.py` | Package marker. Create. |
| `kgc/config.py` | Load and validate `.env` + `repos.yaml`. Owns `Settings` and `Repo`. Create. |
| `kgc/extract.py` | Code-only extraction for one repo. Owns the zero-token guarantee. Create. |
| `kgc/globalgraph.py` | Wrap `global_add`; build the merged graph object. Create. |
| `kgc/neo4j_io.py` | Driver lifecycle, doctor/preflight, scoped reap, batched push. Create. |
| `kgc/cli.py` | `doctor` / `build` / `push` / `status` subcommands. Create. |
| `setup_mcp.py` | Generate `.mcp.json` from `.env`. Create. |
| `tests/test_config.py` | Config loading and validation. Create. |
| `tests/test_extract.py` | Code-only guarantee. Create. |
| `tests/test_neo4j_io.py` | Cypher generation and reap scoping, against a fake driver. Create. |
| `verify/questions.md` | The 10 benchmark questions. Create. |
| `verify/results.md` | Recorded measurements. Create. |
| `.gitignore` | Add `.mcp.json`, `.venv/`, `__pycache__/`. Modify. |

---

### Task 1: Project scaffold and configuration

**Files:**
- Create: `pyproject.toml`, `repos.yaml`, `kgc/__init__.py`, `kgc/config.py`
- Modify: `.gitignore`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `kgc.config.Repo` dataclass: fields `path: Path`, `tag: str`, `enabled: bool`.
  - `kgc.config.Settings` dataclass: fields `neo4j_uri: str`, `neo4j_username: str`, `neo4j_password: str`, `neo4j_database: str`.
  - `kgc.config.load_settings(env_path: Path) -> Settings`
  - `kgc.config.load_repos(yaml_path: Path) -> list[Repo]` — returns enabled repos only, in file order.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
import pytest
from pathlib import Path
from kgc.config import load_settings, load_repos, Repo, Settings


def test_load_settings_reads_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        "NEO4J_URI=neo4j+s://abc.databases.neo4j.io\n"
        "NEO4J_USERNAME=abc\n"
        "NEO4J_PASSWORD=secret-value\n"
        "NEO4J_DATABASE=abc\n"
    )
    s = load_settings(env)
    assert s.neo4j_uri == "neo4j+s://abc.databases.neo4j.io"
    assert s.neo4j_username == "abc"
    assert s.neo4j_password == "secret-value"
    assert s.neo4j_database == "abc"


def test_load_settings_missing_key_raises_without_leaking_password(tmp_path):
    env = tmp_path / ".env"
    env.write_text("NEO4J_URI=x\nNEO4J_PASSWORD=super-secret\n")
    with pytest.raises(ValueError) as exc:
        load_settings(env)
    msg = str(exc.value)
    assert "NEO4J_USERNAME" in msg
    assert "super-secret" not in msg


def test_load_settings_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.env")


def test_load_repos_returns_enabled_only_in_order(tmp_path):
    y = tmp_path / "repos.yaml"
    y.write_text(
        "repos:\n"
        "  - path: ~/Projects/alpha\n"
        "    tag: alpha\n"
        "    enabled: true\n"
        "  - path: ~/Projects/beta\n"
        "    tag: beta\n"
        "    enabled: false\n"
        "  - path: ~/Projects/gamma\n"
        "    tag: gamma\n"
        "    enabled: true\n"
    )
    repos = load_repos(y)
    assert [r.tag for r in repos] == ["alpha", "gamma"]
    assert repos[0].path == Path("~/Projects/alpha").expanduser()
    assert repos[0].enabled is True


def test_load_repos_rejects_duplicate_tags(tmp_path):
    y = tmp_path / "repos.yaml"
    y.write_text(
        "repos:\n"
        "  - path: ~/a\n    tag: same\n    enabled: true\n"
        "  - path: ~/b\n    tag: same\n    enabled: true\n"
    )
    with pytest.raises(ValueError, match="duplicate tag"):
        load_repos(y)


def test_load_repos_rejects_tag_with_separator(tmp_path):
    # '::' is graphify's node-ID namespace separator; a tag containing it
    # would corrupt ID prefixing and break the Neo4j reap predicate.
    y = tmp_path / "repos.yaml"
    y.write_text("repos:\n  - path: ~/a\n    tag: 'bad::tag'\n    enabled: true\n")
    with pytest.raises(ValueError, match="must not contain"):
        load_repos(y)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kgc'`

- [ ] **Step 3: Create `pyproject.toml`**

Pin `graphifyy` to the version currently installed. Find it first:

```bash
/Users/osianshelley/.local/share/uv/tools/graphifyy/bin/python -c \
  "import importlib.metadata as m; print(m.version('graphifyy'))"
```

Then write `pyproject.toml`, substituting that exact version for `<PINNED>`:

```toml
[project]
name = "kgc"
version = "0.1.0"
description = "Multi-repo code graph POC: graphify + Neo4j + MCP"
requires-python = ">=3.11"
dependencies = [
    "graphifyy==<PINNED>",
    "neo4j>=5.28",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
kgc = "kgc.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 4: Create the venv and install**

```bash
cd /Users/osianshelley/Projects/knowledge-graph-code
uv venv
uv pip install -e ".[dev]"
.venv/bin/python -c "import graphify, neo4j, yaml, networkx; print('deps ok')"
```

Expected: `deps ok`

- [ ] **Step 5: Create `repos.yaml`**

```yaml
# The only file that decides what is in scope.
# tag is permanent: it namespaces node IDs as "{tag}::{id}" and is the key the
# Neo4j reaper scopes deletes on. Renaming a tag orphans that repo's nodes.
repos:
  - path: ~/Projects/knowledge-retrieval
    tag: knowledge-retrieval
    enabled: true

  - path: ~/Projects/graphify
    tag: graphify
    enabled: true

  - path: ~/Projects/neural_particle_method
    tag: neural-particle-method
    enabled: true

  - path: ~/Projects/diffusion-bridge
    tag: diffusion-bridge
    enabled: true

  - path: ~/Projects/claude
    tag: claude
    enabled: true
```

- [ ] **Step 6: Write `kgc/__init__.py` and `kgc/config.py`**

```python
# kgc/__init__.py
"""Multi-repo code graph POC."""
```

```python
# kgc/config.py
"""Load and validate the two configuration inputs: .env and repos.yaml.

Nothing here prints or raises with a password in the message - the whole file
is written on the assumption that its error strings may end up in a log.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

REQUIRED_ENV_KEYS = (
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
)

# graphify namespaces global-graph node IDs as "{tag}::{local_id}", so a tag
# containing the separator would corrupt prefixing and the reap predicate.
FORBIDDEN_IN_TAG = "::"


@dataclass(frozen=True)
class Repo:
    path: Path
    tag: str
    enabled: bool


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str

    def redacted(self) -> dict[str, str]:
        """Safe to log."""
        return {
            "uri": self.neo4j_uri,
            "username": self.neo4j_username,
            "database": self.neo4j_database,
            "password": "***",
        }


def _parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def load_settings(env_path: Path) -> Settings:
    if not env_path.exists():
        raise FileNotFoundError(f"no .env at {env_path}")
    values = _parse_env(env_path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED_ENV_KEYS if not values.get(k)]
    if missing:
        # Name only the missing keys. Never echo any value.
        raise ValueError(f"{env_path} is missing required key(s): {', '.join(missing)}")
    return Settings(
        neo4j_uri=values["NEO4J_URI"],
        neo4j_username=values["NEO4J_USERNAME"],
        neo4j_password=values["NEO4J_PASSWORD"],
        neo4j_database=values["NEO4J_DATABASE"],
    )


def load_repos(yaml_path: Path) -> list[Repo]:
    if not yaml_path.exists():
        raise FileNotFoundError(f"no repos.yaml at {yaml_path}")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    entries = data.get("repos") or []

    repos: list[Repo] = []
    seen: set[str] = set()
    for entry in entries:
        tag = str(entry["tag"])
        if FORBIDDEN_IN_TAG in tag:
            raise ValueError(f"tag {tag!r} must not contain {FORBIDDEN_IN_TAG!r}")
        if tag in seen:
            raise ValueError(f"duplicate tag {tag!r} in {yaml_path}")
        seen.add(tag)
        repos.append(
            Repo(
                path=Path(str(entry["path"])).expanduser(),
                tag=tag,
                enabled=bool(entry.get("enabled", True)),
            )
        )
    return [r for r in repos if r.enabled]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: 6 passed

- [ ] **Step 8: Update `.gitignore`**

Append:

```
# Python
.venv/
__pycache__/
*.egg-info/

# Generated MCP config contains the Neo4j password.
.mcp.json
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml repos.yaml kgc/ tests/test_config.py .gitignore
git commit -m "feat: project scaffold, config loading, repo inventory"
```

---

### Task 2: Neo4j doctor — resolve the database name and prove connectivity

This task exists because `push_to_neo4j` takes no `database` argument while Aura
supplied `NEO4J_DATABASE=f35a6a04`. Whether the real database is `neo4j` or the
instance ID is the riskiest unknown in the plan, and Task 5's push depends on
the answer. Resolve it empirically, do not assume.

**Files:**
- Create: `kgc/neo4j_io.py`
- Test: `tests/test_neo4j_io.py`

**Interfaces:**
- Consumes: `kgc.config.Settings` from Task 1.
- Produces:
  - `kgc.neo4j_io.connect(settings: Settings)` -> `neo4j.Driver`
  - `kgc.neo4j_io.DoctorReport` dataclass: fields `reachable: bool`, `database: str`, `server_version: str`, `node_count: int`, `relationship_count: int`, `message: str`.
  - `kgc.neo4j_io.doctor(settings: Settings) -> DoctorReport` — tries `settings.neo4j_database`, falls back to `"neo4j"`, reports which worked.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_neo4j_io.py
import pytest
from kgc.config import Settings
from kgc.neo4j_io import doctor, DoctorReport, resolve_database


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, ok_databases, counts):
        self._ok = ok_databases
        self._counts = counts
        self.database = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        if self.database not in self._ok:
            raise RuntimeError(f"database {self.database!r} does not exist")
        if "count(n)" in query:
            return _FakeResult([{"nodes": self._counts[0]}])
        if "count(r)" in query:
            return _FakeResult([{"rels": self._counts[1]}])
        return _FakeResult([{"version": "5.28.0"}])


class _FakeDriver:
    def __init__(self, ok_databases, counts=(0, 0)):
        self._ok = ok_databases
        self._counts = counts

    def session(self, database=None):
        s = _FakeSession(self._ok, self._counts)
        s.database = database
        return s

    def close(self):
        pass


SETTINGS = Settings(
    neo4j_uri="neo4j+s://abc.databases.neo4j.io",
    neo4j_username="abc",
    neo4j_password="secret",
    neo4j_database="abc",
)


def test_resolve_database_prefers_configured_name():
    driver = _FakeDriver(ok_databases={"abc"})
    assert resolve_database(driver, "abc") == "abc"


def test_resolve_database_falls_back_to_neo4j():
    driver = _FakeDriver(ok_databases={"neo4j"})
    assert resolve_database(driver, "abc") == "neo4j"


def test_resolve_database_raises_when_neither_works():
    driver = _FakeDriver(ok_databases=set())
    with pytest.raises(RuntimeError, match="neither"):
        resolve_database(driver, "abc")


def test_doctor_reports_counts_and_database():
    driver = _FakeDriver(ok_databases={"neo4j"}, counts=(42, 99))
    report = doctor(SETTINGS, driver=driver)
    assert isinstance(report, DoctorReport)
    assert report.reachable is True
    assert report.database == "neo4j"
    assert report.node_count == 42
    assert report.relationship_count == 99
    assert "secret" not in report.message


def test_doctor_reports_unreachable_without_leaking_password():
    class _Broken:
        def session(self, database=None):
            raise OSError("connection refused to neo4j+s://abc with password secret")

        def close(self):
            pass

    report = doctor(SETTINGS, driver=_Broken())
    assert report.reachable is False
    assert "secret" not in report.message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_neo4j_io.py -v`
Expected: FAIL — `ImportError: cannot import name 'doctor'`

- [ ] **Step 3: Write `kgc/neo4j_io.py` (doctor portion)**

```python
# kgc/neo4j_io.py
"""Neo4j access: connection, diagnostics, scoped reap, batched push.

Every error path here scrubs the password before it can reach a log, because
driver exceptions routinely include the connection string.
"""
from __future__ import annotations

from dataclasses import dataclass

from neo4j import GraphDatabase

from kgc.config import Settings

COUNT_NODES = "MATCH (n) RETURN count(n) AS nodes"
COUNT_RELS = "MATCH ()-[r]->() RETURN count(r) AS rels"
SERVER_VERSION = (
    "CALL dbms.components() YIELD versions "
    "RETURN head(versions) AS version"
)


@dataclass(frozen=True)
class DoctorReport:
    reachable: bool
    database: str
    server_version: str
    node_count: int
    relationship_count: int
    message: str


def _scrub(text: str, password: str) -> str:
    """Remove the password from any string before it is shown or logged."""
    if password and password in text:
        return text.replace(password, "***")
    return text


def connect(settings: Settings):
    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )


def resolve_database(driver, configured: str) -> str:
    """Return the database name that actually accepts a query.

    Aura supplies NEO4J_DATABASE set to the instance id, but the real database
    is often named 'neo4j'. Try the configured name, then 'neo4j'.
    """
    candidates = [c for c in (configured, "neo4j") if c]
    errors: list[str] = []
    for name in candidates:
        try:
            with driver.session(database=name) as session:
                session.run(COUNT_NODES).single()
            return name
        except Exception as exc:  # noqa: BLE001 - probing on purpose
            errors.append(f"{name}: {type(exc).__name__}")
    raise RuntimeError(
        f"neither database name worked ({'; '.join(errors)})"
    )


def doctor(settings: Settings, driver=None) -> DoctorReport:
    own_driver = driver is None
    driver = driver or connect(settings)
    try:
        database = resolve_database(driver, settings.neo4j_database)
        with driver.session(database=database) as session:
            nodes = session.run(COUNT_NODES).single()["nodes"]
            rels = session.run(COUNT_RELS).single()["rels"]
            try:
                version = session.run(SERVER_VERSION).single()["version"]
            except Exception:  # noqa: BLE001 - Aura may restrict dbms procs
                version = "unknown"
        return DoctorReport(
            reachable=True,
            database=database,
            server_version=str(version),
            node_count=int(nodes),
            relationship_count=int(rels),
            message=f"connected to {database!r} ({nodes} nodes, {rels} rels)",
        )
    except Exception as exc:  # noqa: BLE001 - doctor never raises
        return DoctorReport(
            reachable=False,
            database="",
            server_version="",
            node_count=0,
            relationship_count=0,
            message=_scrub(f"{type(exc).__name__}: {exc}", settings.neo4j_password),
        )
    finally:
        if own_driver:
            driver.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_neo4j_io.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the doctor against the real instance**

```bash
.venv/bin/python -c "
from pathlib import Path
from kgc.config import load_settings
from kgc.neo4j_io import doctor
s = load_settings(Path('.env'))
print(s.redacted())
r = doctor(s)
print(r)
"
```

Expected: `reachable=True` and a database name. **Record which name worked** — Task 5 uses it. If `reachable=False` because the instance is paused, resume it at https://console.neo4j.io and retry; Aura Free pauses after 3 days idle.

- [ ] **Step 6: Commit**

```bash
git add kgc/neo4j_io.py tests/test_neo4j_io.py
git commit -m "feat: Neo4j doctor with database-name resolution"
```

---

### Task 3: Code-only extraction for one repo

**Files:**
- Create: `kgc/extract.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes: `kgc.config.Repo` from Task 1.
- Produces:
  - `kgc.extract.ExtractResult` dataclass: fields `tag: str`, `nodes: int`, `edges: int`, `files: int`, `seconds: float`, `graph_path: Path`.
  - `kgc.extract.code_only_detect(root: Path) -> dict` — `detect()` output with every non-code category emptied.
  - `kgc.extract.extract_repo(repo: Repo) -> ExtractResult` — writes `<repo>/graphify-out/graph.json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract.py
from pathlib import Path
import pytest
from kgc.config import Repo
from kgc.extract import code_only_detect, extract_repo, ExtractResult


def test_code_only_detect_empties_non_code_categories(tmp_path):
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n")
    (tmp_path / "README.md").write_text("# docs\n\nprose that must not be extracted\n")
    (tmp_path / "notes.txt").write_text("more prose\n")

    result = code_only_detect(tmp_path)

    assert result["files"]["document"] == []
    assert result["files"]["paper"] == []
    assert result["files"]["image"] == []
    assert result["files"]["video"] == []
    assert any(f.endswith("mod.py") for f in result["files"]["code"])
    assert result["total_files"] == len(result["files"]["code"])


def test_extract_repo_produces_graph_and_costs_no_tokens(tmp_path):
    (tmp_path / "a.py").write_text(
        "def helper():\n    return 2\n\n\ndef caller():\n    return helper()\n"
    )
    (tmp_path / "README.md").write_text("# prose\n")
    repo = Repo(path=tmp_path, tag="fixture", enabled=True)

    res = extract_repo(repo)

    assert isinstance(res, ExtractResult)
    assert res.tag == "fixture"
    assert res.nodes > 0
    assert res.graph_path.exists()
    assert res.graph_path == tmp_path / "graphify-out" / "graph.json"


def test_extract_repo_graph_contains_no_document_nodes(tmp_path):
    import json
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "GUIDE.md").write_text("# a guide\n\nlots of prose here\n")
    repo = Repo(path=tmp_path, tag="fixture", enabled=True)

    res = extract_repo(repo)
    data = json.loads(res.graph_path.read_text())
    types = {n.get("file_type") for n in data["nodes"]}

    assert "document" not in types
    assert "paper" not in types


def test_extract_repo_missing_path_raises(tmp_path):
    repo = Repo(path=tmp_path / "absent", tag="x", enabled=True)
    with pytest.raises(FileNotFoundError):
        extract_repo(repo)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kgc.extract'`

- [ ] **Step 3: Write `kgc/extract.py`**

```python
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
from graphify.detect import detect
from graphify.extract import collect_files, extract

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
    """detect() with every non-code category emptied."""
    result = detect(root)
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

    # Write the graph.json shape global_add consumes (node-link with 'links').
    from networkx.readwrite import json_graph
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_extract.py -v`
Expected: 4 passed

- [ ] **Step 5: Run against one real repo and confirm zero tokens**

```bash
.venv/bin/python -c "
from pathlib import Path
from kgc.config import Repo
from kgc.extract import extract_repo
r = Repo(path=Path('~/Projects/diffusion-bridge').expanduser(), tag='diffusion-bridge', enabled=True)
print(extract_repo(r))
"
```

Expected: an `ExtractResult` with `nodes > 0`. If it raises `AssertionError` about token spend, the code-only guard has failed — stop and investigate before continuing.

- [ ] **Step 6: Commit**

```bash
git add kgc/extract.py tests/test_extract.py
git commit -m "feat: zero-token code-only extraction per repo"
```

---

### Task 4: Global graph merge and the build CLI

**Files:**
- Create: `kgc/globalgraph.py`, `kgc/cli.py`
- Test: `tests/test_globalgraph.py`

**Interfaces:**
- Consumes: `kgc.config.Repo`, `kgc.config.load_repos`, `kgc.config.load_settings`, `kgc.extract.extract_repo`, `kgc.extract.ExtractResult`, `kgc.neo4j_io.doctor`.
- Produces:
  - `kgc.globalgraph.MergeResult` dataclass: fields `tag: str`, `nodes_added: int`, `nodes_removed: int`, `skipped: bool`, `cross_repo_calls: int`.
  - `kgc.globalgraph.merge_repo(result: ExtractResult) -> MergeResult`
  - `kgc.globalgraph.load_global_graph() -> nx.Graph`
  - `kgc.globalgraph.global_graph_path() -> Path`
  - `kgc.cli.main(argv: list[str] | None = None) -> int` with subcommands `doctor`, `build`, `push`, `status`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_globalgraph.py
from pathlib import Path
from kgc.extract import ExtractResult
from kgc.globalgraph import merge_repo, MergeResult, global_graph_path


def test_global_graph_path_is_under_home_graphify():
    p = global_graph_path()
    assert p.name == "global-graph.json"


def test_merge_repo_maps_global_add_summary(monkeypatch, tmp_path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text('{"nodes": [], "links": []}')

    captured = {}

    def fake_global_add(source_path, repo_tag):
        captured["source_path"] = source_path
        captured["repo_tag"] = repo_tag
        return {
            "repo_tag": repo_tag,
            "nodes_added": 7,
            "nodes_removed": 2,
            "skipped": False,
            "cross_repo_calls": 3,
        }

    monkeypatch.setattr("kgc.globalgraph.global_add", fake_global_add)

    res = merge_repo(
        ExtractResult(tag="alpha", nodes=7, edges=9, files=3,
                      seconds=0.1, graph_path=graph_path)
    )

    assert isinstance(res, MergeResult)
    assert captured["repo_tag"] == "alpha"
    assert captured["source_path"] == graph_path
    assert res.nodes_added == 7
    assert res.nodes_removed == 2
    assert res.cross_repo_calls == 3
    assert res.skipped is False


def test_merge_repo_propagates_skipped(monkeypatch, tmp_path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text('{"nodes": [], "links": []}')
    monkeypatch.setattr(
        "kgc.globalgraph.global_add",
        lambda source_path, repo_tag: {
            "repo_tag": repo_tag, "nodes_added": 0, "nodes_removed": 0,
            "skipped": True, "cross_repo_calls": 0,
        },
    )
    res = merge_repo(
        ExtractResult(tag="beta", nodes=0, edges=0, files=0,
                      seconds=0.0, graph_path=graph_path)
    )
    assert res.skipped is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_globalgraph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kgc.globalgraph'`

- [ ] **Step 3: Write `kgc/globalgraph.py`**

```python
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
```

- [ ] **Step 4: Write `kgc/cli.py`**

```python
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
from kgc.globalgraph import load_global_graph, merge_repo, registered_repos

ENV_PATH = Path(".env")
REPOS_PATH = Path("repos.yaml")


def _cmd_doctor(_args: argparse.Namespace) -> int:
    from kgc.neo4j_io import doctor

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

    graph = load_global_graph()
    print(f"\nglobal graph: {graph.number_of_nodes()} nodes, "
          f"{graph.number_of_edges()} edges")
    if failures:
        print(f"failed repos: {', '.join(failures)}", file=sys.stderr)
    return 1 if failures else 0


def _cmd_push(args: argparse.Namespace) -> int:
    from kgc.neo4j_io import push_global_graph

    settings = load_settings(ENV_PATH)
    repos = load_repos(REPOS_PATH)
    tags = set(args.repo) if args.repo else {r.tag for r in repos}
    graph = load_global_graph()
    summary = push_global_graph(graph, settings, tags, dry_run=args.dry_run)
    for line in summary:
        print(line)
    return 0


def _cmd_status(_args: argparse.Namespace) -> int:
    for tag, meta in sorted(registered_repos().get("repos", {}).items()):
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_globalgraph.py -v`
Expected: 3 passed

- [ ] **Step 6: Build all five repos for real**

```bash
.venv/bin/kgc build
```

Expected: a line per repo with node/edge counts, then a global graph total.
`claude` is TypeScript-heavy and the largest — if it fails, the run must
continue and report it rather than aborting. Record the totals; they feed the
Aura cap pre-flight in Task 5.

- [ ] **Step 7: Commit**

```bash
git add kgc/globalgraph.py kgc/cli.py tests/test_globalgraph.py
git commit -m "feat: global graph merge and build/status CLI"
```

---

### Task 5: Scoped reap and batched push to Neo4j

**Files:**
- Modify: `kgc/neo4j_io.py` (append; keep Task 2's doctor code unchanged)
- Test: `tests/test_push.py`

**Interfaces:**
- Consumes: `kgc.config.Settings`, `kgc.neo4j_io.connect`, `kgc.neo4j_io.resolve_database`.
- Produces:
  - `kgc.neo4j_io.REAP_REPO` — Cypher string, parameterised on `$tag`.
  - `kgc.neo4j_io.REAP_ORPHAN_EXTERNALS` — Cypher string.
  - `kgc.neo4j_io.preflight_capacity(graph, limit: int) -> tuple[bool, str]`
  - `kgc.neo4j_io.push_global_graph(graph, settings: Settings, tags: set[str], *, dry_run: bool = False, driver=None) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_push.py
import networkx as nx
import pytest
from kgc.config import Settings
from kgc.neo4j_io import (
    REAP_REPO,
    REAP_ORPHAN_EXTERNALS,
    preflight_capacity,
    push_global_graph,
)

SETTINGS = Settings(
    neo4j_uri="neo4j+s://abc.databases.neo4j.io",
    neo4j_username="abc",
    neo4j_password="secret",
    neo4j_database="neo4j",
)


def _graph():
    g = nx.Graph()
    g.add_node("alpha::mod_f", label="f", file_type="code", repo="alpha")
    g.add_node("beta::mod_g", label="g", file_type="code", repo="beta")
    g.add_node("numpy", label="numpy", file_type="code", external=True)
    g.add_edge("alpha::mod_f", "numpy", relation="imports", confidence="EXTRACTED")
    g.add_edge("beta::mod_g", "numpy", relation="imports", confidence="EXTRACTED")
    return g


def test_reap_repo_is_scoped_to_a_repo_property():
    # The single most dangerous query in the project. It must never be able to
    # match the whole database.
    assert "$tag" in REAP_REPO
    assert "n.repo = $tag" in REAP_REPO
    assert "DETACH DELETE" in REAP_REPO


def test_reap_orphan_externals_only_targets_unreferenced_externals():
    assert "external" in REAP_ORPHAN_EXTERNALS
    assert "NOT (n)--()" in REAP_ORPHAN_EXTERNALS
    assert "$tag" not in REAP_ORPHAN_EXTERNALS


def test_preflight_capacity_passes_under_limit():
    ok, msg = preflight_capacity(_graph(), limit=1000)
    assert ok is True
    assert "3" in msg


def test_preflight_capacity_fails_over_limit():
    ok, msg = preflight_capacity(_graph(), limit=2)
    assert ok is False
    assert "exceeds" in msg


class _Recorder:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        self.calls.append((query, params))

        class _R:
            def single(self_inner):
                return {"nodes": 0, "rels": 0}

            def consume(self_inner):
                return None
        return _R()


class _RecordingDriver:
    def __init__(self):
        self.session_obj = _Recorder()

    def session(self, database=None):
        return self.session_obj

    def close(self):
        pass


def test_push_reaps_each_tag_before_writing():
    driver = _RecordingDriver()
    push_global_graph(_graph(), SETTINGS, {"alpha", "beta"}, driver=driver)
    reaps = [p["tag"] for q, p in driver.session_obj.calls
             if "DETACH DELETE" in q and "tag" in p]
    assert set(reaps) == {"alpha", "beta"}


def test_push_dry_run_writes_nothing():
    driver = _RecordingDriver()
    lines = push_global_graph(_graph(), SETTINGS, {"alpha"},
                             dry_run=True, driver=driver)
    assert driver.session_obj.calls == []
    assert any("dry run" in line.lower() for line in lines)


def test_push_refuses_unknown_tag():
    driver = _RecordingDriver()
    with pytest.raises(ValueError, match="not present in the graph"):
        push_global_graph(_graph(), SETTINGS, {"nonexistent"}, driver=driver)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_push.py -v`
Expected: FAIL — `ImportError: cannot import name 'REAP_REPO'`

- [ ] **Step 3: Append the push implementation to `kgc/neo4j_io.py`**

```python
# --- append to kgc/neo4j_io.py ---

# AuraDB Free: Neo4j support docs say 200k nodes / 400k relationships; the
# pricing page says 50k / 175k. Use the lower pair so we fail early rather
# than half-pushing.
AURA_FREE_NODE_LIMIT = 50_000

# The only destructive queries in the project. Both are deliberately narrow.
# REAP_REPO is scoped on the 'repo' property that graphify's
# prefix_graph_for_global sets on every repo-local node and on no external
# node, so a tag matching nothing deletes nothing.
REAP_REPO = """
MATCH (n) WHERE n.repo = $tag
DETACH DELETE n
"""

# External stubs (stdlib / third-party) are shared across repos and carry no
# 'repo' property, so REAP_REPO never touches them. They orphan when the last
# repo referencing them goes away; this sweeps those and only those.
REAP_ORPHAN_EXTERNALS = """
MATCH (n) WHERE n.external = true AND NOT (n)--()
DELETE n
"""

UPSERT_NODES = """
UNWIND $rows AS row
MERGE (n:CodeNode {id: row.id})
SET n += row.props
"""

UPSERT_EDGES = """
UNWIND $rows AS row
MATCH (a:CodeNode {id: row.source})
MATCH (b:CodeNode {id: row.target})
MERGE (a)-[r:RELATES {relation: row.relation}]->(b)
SET r += row.props
"""

ENSURE_INDEX = "CREATE INDEX IF NOT EXISTS FOR (n:CodeNode) ON (n.id)"
ENSURE_REPO_INDEX = "CREATE INDEX IF NOT EXISTS FOR (n:CodeNode) ON (n.repo)"

BATCH = 1000


def _scalar(value):
    """Neo4j properties must be primitives or arrays of primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def preflight_capacity(graph, limit: int = AURA_FREE_NODE_LIMIT):
    """Return (ok, message). Refuse rather than half-push."""
    n = graph.number_of_nodes()
    if n > limit:
        return False, (
            f"graph has {n} nodes which exceeds the configured limit of {limit}; "
            "reduce repos.yaml or upgrade the instance"
        )
    return True, f"capacity ok: {n} nodes against a limit of {limit}"


def _tags_in_graph(graph) -> set[str]:
    return {
        d["repo"] for _, d in graph.nodes(data=True) if d.get("repo")
    }


def _chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def push_global_graph(graph, settings: Settings, tags: set[str], *,
                      dry_run: bool = False, driver=None) -> list[str]:
    """Reap then re-push each tag's slice. Externals are pushed once, shared."""
    present = _tags_in_graph(graph)
    unknown = tags - present
    if unknown:
        raise ValueError(
            f"tag(s) {sorted(unknown)} not present in the graph "
            f"(present: {sorted(present)})"
        )

    ok, capacity_msg = preflight_capacity(graph)
    lines = [capacity_msg]
    if not ok:
        return lines

    if dry_run:
        for tag in sorted(tags):
            count = sum(1 for _, d in graph.nodes(data=True) if d.get("repo") == tag)
            lines.append(f"dry run: would reap and push {count} nodes for {tag!r}")
        return lines

    own_driver = driver is None
    driver = driver or connect(settings)
    try:
        database = resolve_database(driver, settings.neo4j_database)
        with driver.session(database=database) as session:
            session.run(ENSURE_INDEX).consume()
            session.run(ENSURE_REPO_INDEX).consume()

            for tag in sorted(tags):
                session.run(REAP_REPO, tag=tag).consume()
                lines.append(f"reaped {tag!r}")

            wanted = tags | {None}
            node_rows = [
                {
                    "id": nid,
                    "props": {k: _scalar(v) for k, v in data.items()},
                }
                for nid, data in graph.nodes(data=True)
                if data.get("repo") in wanted or data.get("external")
            ]
            ids = {r["id"] for r in node_rows}
            for batch in _chunks(node_rows, BATCH):
                session.run(UPSERT_NODES, rows=batch).consume()
            lines.append(f"upserted {len(node_rows)} nodes")

            edge_rows = [
                {
                    "source": u,
                    "target": v,
                    "relation": str(data.get("relation") or "RELATED"),
                    "props": {k: _scalar(val) for k, val in data.items()},
                }
                for u, v, data in graph.edges(data=True)
                if u in ids and v in ids
            ]
            for batch in _chunks(edge_rows, BATCH):
                session.run(UPSERT_EDGES, rows=batch).consume()
            lines.append(f"upserted {len(edge_rows)} edges")

            session.run(REAP_ORPHAN_EXTERNALS).consume()
            lines.append("swept orphaned external nodes")
    finally:
        if own_driver:
            driver.close()
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_push.py -v`
Expected: 8 passed

- [ ] **Step 5: Dry run, then push for real**

```bash
.venv/bin/kgc push --dry-run
.venv/bin/kgc push
.venv/bin/kgc doctor
```

Expected: `doctor` afterwards reports a node count matching the global graph.
If `preflight_capacity` refuses, lower the limit decision consciously —
`AURA_FREE_NODE_LIMIT` is set to the pessimistic 50k; raise it to 200_000 only
after confirming the instance's real cap in the Aura console.

- [ ] **Step 6: Commit**

```bash
git add kgc/neo4j_io.py tests/test_push.py
git commit -m "feat: scoped reap and batched Neo4j push"
```

---

### Task 6: MCP configuration for both servers

**Files:**
- Create: `setup_mcp.py`
- Test: `tests/test_setup_mcp.py`

**Interfaces:**
- Consumes: `kgc.config.load_settings`, `kgc.globalgraph.global_graph_path`.
- Produces: `setup_mcp.build_config(settings, graph_path, python_path) -> dict`, and a written `.mcp.json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_setup_mcp.py
from pathlib import Path
from kgc.config import Settings
from setup_mcp import build_config

SETTINGS = Settings(
    neo4j_uri="neo4j+s://abc.databases.neo4j.io",
    neo4j_username="abc",
    neo4j_password="secret",
    neo4j_database="neo4j",
)


def test_config_declares_both_servers():
    cfg = build_config(SETTINGS, Path("/tmp/global-graph.json"), Path("/usr/bin/python3"))
    assert set(cfg["mcpServers"]) == {"graphify", "neo4j"}


def test_graphify_server_points_at_the_global_graph():
    cfg = build_config(SETTINGS, Path("/tmp/global-graph.json"), Path("/usr/bin/python3"))
    args = cfg["mcpServers"]["graphify"]["args"]
    assert "/tmp/global-graph.json" in " ".join(args)


def test_neo4j_server_is_read_only():
    cfg = build_config(SETTINGS, Path("/tmp/g.json"), Path("/usr/bin/python3"))
    neo = cfg["mcpServers"]["neo4j"]
    joined = " ".join(neo.get("args", [])) + " " + " ".join(neo.get("env", {}))
    assert "write" not in joined.lower()
    assert neo["env"]["NEO4J_PASSWORD"] == "secret"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_setup_mcp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'setup_mcp'`

- [ ] **Step 3: Write `setup_mcp.py`**

```python
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
            # Precise path: read-only Cypher. write-cypher is deliberately not
            # enabled - only kgc writes to this database.
            "neo4j": {
                "command": "uvx",
                "args": ["mcp-neo4j-cypher@latest", "--read-only"],
                "env": {
                    "NEO4J_URI": settings.neo4j_uri,
                    "NEO4J_USERNAME": settings.neo4j_username,
                    "NEO4J_PASSWORD": settings.neo4j_password,
                    "NEO4J_DATABASE": settings.neo4j_database,
                },
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_setup_mcp.py -v`
Expected: 3 passed

- [ ] **Step 5: Generate the config and verify both servers start**

```bash
.venv/bin/python setup_mcp.py
.venv/bin/python -m graphify.serve "$(.venv/bin/python -c 'from kgc.globalgraph import global_graph_path; print(global_graph_path())')" --help 2>&1 | head -5
```

Then restart Claude Code in this directory and confirm both servers appear via
`/mcp`. If `mcp-neo4j-cypher` rejects `--read-only`, check its actual flag with
`uvx mcp-neo4j-cypher@latest --help` and correct `setup_mcp.py` — the
requirement is that no write tool is exposed, however that is spelled.

- [ ] **Step 6: Commit**

```bash
git add setup_mcp.py tests/test_setup_mcp.py
git commit -m "feat: generate MCP config for graphify and read-only Neo4j"
```

---

### Task 7: Token-saving verification harness

The POC's entire premise. Without this the build is unfalsifiable.

**Files:**
- Create: `verify/questions.md`, `verify/results.md`

**Interfaces:**
- Consumes: the running MCP servers from Task 6.
- Produces: a completed `verify/results.md`.

- [ ] **Step 1: Write `verify/questions.md`**

```markdown
# Verification questions

Ten questions spanning the five repos. Each is answered twice in **separate
Claude Code sessions** so context does not leak between runs:

- **Control:** MCP servers disabled; the agent reads files normally.
- **Treatment:** the agent may use only the `graphify` and `neo4j` MCP servers.

Record tokens, wall time, and a correctness verdict judged against the code.

## Single-repo (4)

1. In `knowledge-retrieval`, how does the Zotero push path handle a storage
   quota error differently from a transient error?
2. In `graphify`, what does `prefix_graph_for_global` do to external stub nodes,
   and why?
3. In `neural_particle_method`, what are the core abstractions and how do they
   relate?
4. In `diffusion-bridge`, where is the bridge sampling step implemented?

## Cross-repo (4) — unanswerable before this POC

5. Which of the five repos depend on `dagster`?
6. Which repos define a type or class with the same name as one in another repo?
7. `knowledge-retrieval` and `graphify` both build graphs from extraction JSON.
   Where does each do it, and do they share any code?
8. Across all repos, which third-party dependency is used by the most repos?

## Known-blind-spot (2) — the graph should be wrong or silent

9. In `knowledge-retrieval`, what does `push_one` actually call on its `client`
   argument? (`client` is injected untyped, so no static edge exists. A graph-only
   answer should be absent or hedged, never confidently wrong.)
10. What does the kdb+/q code in any repo do? (No `.q` extractor exists; the
    correct answer is that the graph cannot see it.)
```

- [ ] **Step 2: Write the `verify/results.md` skeleton**

```markdown
# Verification results

Instance: AuraDB Free. Graph built from 5 repos. Date: ____

| # | Control tokens | Treatment tokens | Ratio | Control correct | Treatment correct | Notes |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |
| 7 | | | | | | |
| 8 | | | | | | |
| 9 | | | | | | |
| 10 | | | | | | |

## Verdict

- Median token ratio (treatment vs control): ____
- Questions where treatment was **wrong**: ____
- Questions only treatment could answer: ____
- Blind spots correctly identified (Q9, Q10): ____

**Accept or reject?** The POC succeeds only if treatment is materially cheaper
**and** no less correct. A good median hiding one confidently wrong answer is a
failure, because the failure mode of a stale graph is plausibility.

## Which MCP server answered

Record, per question, whether `graphify` alone sufficed or `neo4j` Cypher was
needed. If `neo4j` was never needed, that is a useful negative result and
simplifies the work build — see open question 1 in the spec.
```

- [ ] **Step 3: Run the control arm**

For each of the 10 questions, in a session with MCP disabled, record tokens from
`/cost` and judge correctness against the code. Fill the Control columns.

- [ ] **Step 4: Run the treatment arm**

Repeat in a session with both MCP servers enabled, restricting the agent to
them. Fill the Treatment columns and the "which server answered" section.

- [ ] **Step 5: Write the verdict**

Complete the Verdict section with an explicit accept/reject and the reasoning.

- [ ] **Step 6: Commit**

```bash
git add verify/
git commit -m "docs: verification harness and recorded results"
```

---

## Self-Review

**Spec coverage.** Goal → Tasks 3–6. Success criteria → Task 7. Non-goals: no
trigger/CI task (correct, dropped); kdb+/q appears only as blind-spot Q10;
prose excluded by Task 3's category-emptying; single-user stdio in Task 6;
Neo4j read-only in Task 6. Architecture's four components → Tasks 1, 3, 4, 5, 6.
Failure modes: per-repo isolation (Task 4 `_cmd_build`), paused instance (Task 2
Step 5), node cap (`preflight_capacity`), bad tag (`test_push_refuses_unknown_tag`
and `REAP_REPO` scoping), size cap (surfaces from graphify), partial push
(MERGE idempotence). Constraints: Aura caps in `AURA_FREE_NODE_LIMIT`; Community
vs Aura divergence is spec-level, no task needed; merged-graph ceiling is a
future concern; credential rotation is operational; SCIP unused. **One gap
found and accepted:** the spec mentions a per-repo `.graphifyignore` as
belt-and-braces; Task 3's category-emptying makes it redundant for `kgc`'s own
path, so it is deliberately not implemented — noted here so it is a decision,
not an omission.

**Placeholder scan.** No TBD/TODO/"handle edge cases". The `<PINNED>` token in
Task 1 Step 3 is resolved by the command immediately above it. Blank tables in
Task 7 are the deliverable (a measurement form), not placeholders.

**Type consistency.** `Repo`/`Settings` (Task 1) consumed unchanged in 2–6.
`ExtractResult` (Task 3) consumed by `merge_repo` (Task 4). `MergeResult` fields
match `global_add`'s verified return keys. `connect`/`resolve_database` (Task 2)
reused by `push_global_graph` (Task 5) — same module, no re-definition.
`global_graph_path()` (Task 4) consumed by `setup_mcp` (Task 6). `push_global_graph`
is referenced by `_cmd_push` in Task 4 and defined in Task 5: Task 4's import is
function-local so Task 4's tests pass before Task 5 exists, but **`kgc push` does
not work until Task 5 is complete** — flagged so the order is not mistaken for a bug.
