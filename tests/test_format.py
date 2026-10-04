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
    assert out == (
        "[truncated: showing 0 of 100 lines; first line alone exceeds the budget, raise max_tokens; add LIMIT]\n"
        "… truncated; add LIMIT"
    )
    assert "y" not in out


def test_cut_to_budget_very_long_first_line():
    """First line of 1000 chars followed by short lines, budget fits none."""
    lines = ["z" * 1000] + ["short"] * 10
    out = cut_to_budget(lines, max_tokens=5, hint="increase budget")
    assert out == (
        "[truncated: showing 0 of 11 lines; first line alone exceeds the budget, raise max_tokens; increase budget]\n"
        "… truncated; increase budget"
    )
    assert "z" not in out
