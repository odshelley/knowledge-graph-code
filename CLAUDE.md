# Code graph MCP servers

This project exposes two MCP servers over a merged code graph of five repos:
`knowledge-retrieval`, `graphify`, `neural-particle-method`, `diffusion-bridge`,
`claude`.

For any question about how code is structured, what calls or imports what, or
where something lives: use the **graphify** server's `query_graph`, `get_node`,
`get_neighbors`, or `shortest_path` tools FIRST — before reading files or grepping.

For cross-repo aggregates, "which repos import X", or impact / reverse-dependency
questions: use the **neo4j** server's `read-cypher` tool. Edges point from
`_src` to `_tgt`, one relationship type per relation (e.g. `calls`, `imports`).
Every node has label `CodeNode` with properties `id`, `label`, `repo` (absent
on shared external stubs, which carry `external: true` instead), `source_file`,
`community`.

Example:

```cypher
MATCH (n:CodeNode)-[:imports]->(m:CodeNode {label: 'dagster'}) RETURN DISTINCT n.repo
```

Only `kgc` writes to Neo4j — the MCP server is read-only. Run `kgc build` to
refresh the graph.
