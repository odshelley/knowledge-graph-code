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
                   "references", "uses", "inherits", "extends", "implements",
                   "re_exports", "dynamic_import")
CALLEE_RELS = ("calls", "indirect_call", "imports", "imports_from", "references", "uses",
              "re_exports", "dynamic_import")
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
WHERE e.external = true AND s.repo IS NOT NULL
WITH split(e.label, '.')[0] AS package, collect(DISTINCT s.repo) AS repos
WHERE size(repos) >= $min_repos AND ($repo IS NULL OR $repo IN repos)
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
