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
