# Code graph MCP servers

This project exposes two MCP servers over a merged code graph of five repos:
`knowledge-retrieval`, `graphify`, `neural-particle-method`, `diffusion-bridge`,
`claude`.

For any question about how code is structured, what calls or imports what, or
where something lives: use the **graphify** server's `query_graph`, `get_node`,
`get_neighbors`, or `shortest_path` tools FIRST — before reading files or grepping.

For cross-repo aggregates, "which repos import X", or impact / reverse-dependency
questions: use the **neo4j** server's `read_neo4j_cypher` tool (`get_neo4j_schema`
first to inspect labels/relationship types; these are the tool names of
`mcp-neo4j-cypher` 0.6.0, which `setup_mcp.py` pins). Relationships run in the
code's own direction — `(caller)-[:calls]->(callee)`,
`(module)-[:imports]->(dependency)`, `(subclass)-[:inherits]->(base)` — one
relationship type per relation. Every node has label `CodeNode` with properties
`id`, `label`, `repo`, `source_file`, `community`, `local_id`. Shared stubs —
stdlib / third-party modules, and unresolved names such as `Exception` or `Path`
that several repos reference — carry `external: true`, no `repo`, and
`source_file = ''`; they are what lets "which repos depend on X" unify across
repos. For real cross-repo coupling (edges between two repos' own code) filter
both ends to sourced nodes: `WHERE a.source_file <> '' AND b.source_file <> ''`.

Examples:

```cypher
MATCH (n:CodeNode)-[:imports]->(m:CodeNode {label: 'dagster'}) RETURN DISTINCT n.repo
```

```cypher
MATCH (a:CodeNode)-[r]->(b:CodeNode)
WHERE a.repo <> b.repo AND a.source_file <> '' AND b.source_file <> ''
RETURN a.repo, b.repo, type(r), count(*) AS n ORDER BY n DESC
```

Only `kgc` writes to Neo4j — the MCP server is read-only. Run `kgc build` then
`kgc push` to refresh the graph.
