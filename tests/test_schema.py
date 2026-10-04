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
