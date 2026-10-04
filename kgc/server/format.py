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
    if cut <= 0:
        # No newline found in budget window; can't fit even one full line
        return (
            f"[truncated: showing 0 of {len(lines)} lines; first line alone exceeds the budget, raise max_tokens; {hint}]\n"
            + f"… truncated; {hint}"
        )
    kept = output[:cut]
    shown = kept.count("\n") + 1
    return (
        f"[truncated: showing {shown} of {len(lines)} lines; {hint}]\n"
        + kept
        + f"\n… truncated; {hint}"
    )
