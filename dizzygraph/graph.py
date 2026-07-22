# === dizzygraph/graph.py ===
"""Graph container: nodes, edges, validation, serialization hooks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .edges import Edge
from .nodes import Node


class Graph(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str = "graph"
    name: str = "DizzyGraph"
    description: str = ""
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    entry_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def add_node(self, node: Node) -> "Graph":
        if node.id in self.nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self.nodes[node.id] = node
        return self

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        *,
        condition=None,
        label: str = "",
        metadata: dict | None = None,
    ) -> "Graph":
        self.edges.append(
            Edge(
                source_id=source_id,
                target_id=target_id,
                condition=condition,
                label=label,
                metadata=metadata or {},
            )
        )
        return self

    def set_entry(self, *node_ids: str) -> "Graph":
        for nid in node_ids:
            if nid not in self.nodes:
                raise ValueError(f"Unknown entry node: {nid}")
        self.entry_ids = list(node_ids)
        return self

    def get_entry_nodes(self) -> list[str]:
        if self.entry_ids:
            return list(self.entry_ids)
        targets = {e.target_id for e in self.edges}
        entries = [nid for nid in self.nodes if nid not in targets]
        return entries or list(self.nodes.keys())[:1]

    def successors(self, node_id: str, state) -> list[str]:
        out: list[str] = []
        for e in self.edges:
            if e.source_id == node_id and e.is_active(state):
                out.append(e.target_id)
        return out

    def validate(self, *, allow_cycles: bool = True) -> list[str]:
        """Return warnings; raise on hard errors."""
        warnings: list[str] = []
        for e in self.edges:
            if e.source_id not in self.nodes:
                raise ValueError(f"Edge source missing: {e.source_id}")
            if e.target_id not in self.nodes:
                raise ValueError(f"Edge target missing: {e.target_id}")
        if not self.get_entry_nodes():
            raise ValueError("Graph has no entry nodes")
        cycles = self.detect_cycles()
        if cycles and not allow_cycles:
            raise ValueError(f"Cycles not allowed: {cycles}")
        if cycles:
            warnings.append(f"cycles detected: {cycles}")
        return warnings

    def detect_cycles(self) -> list[list[str]]:
        """DFS cycle enumeration (simple paths)."""
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            adj[e.source_id].append(e.target_id)

        cycles: list[list[str]] = []
        path: list[str] = []
        on_path: set[str] = set()

        def dfs(u: str) -> None:
            path.append(u)
            on_path.add(u)
            for v in adj[u]:
                if v in on_path:
                    i = path.index(v)
                    cycles.append(path[i:] + [v])
                else:
                    dfs(v)
            path.pop()
            on_path.discard(u)

        for n in self.nodes:
            dfs(n)
        # unique by frozenset of nodes in cycle
        uniq: list[list[str]] = []
        seen: set[frozenset[str]] = set()
        for c in cycles:
            key = frozenset(c[:-1] if c and c[0] == c[-1] else c)
            if key and key not in seen:
                seen.add(key)
                uniq.append(c)
        return uniq

    def topological_layers(self) -> list[list[str]] | None:
        """Kahn layers; None if cyclic."""
        indeg = {n: 0 for n in self.nodes}
        adj: dict[str, list[str]] = {n: [] for n in self.nodes}
        for e in self.edges:
            adj[e.source_id].append(e.target_id)
            indeg[e.target_id] += 1
        queue = [n for n, d in indeg.items() if d == 0]
        layers: list[list[str]] = []
        seen = 0
        while queue:
            layers.append(list(queue))
            nxt: list[str] = []
            for u in queue:
                seen += 1
                for v in adj[u]:
                    indeg[v] -= 1
                    if indeg[v] == 0:
                        nxt.append(v)
            queue = nxt
        if seen != len(self.nodes):
            return None
        return layers

    def to_serializable(self) -> dict[str, Any]:
        """JSON-friendly skeleton (callables omitted — rebind after load)."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "entry_ids": self.entry_ids,
            "metadata": self.metadata,
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "description": n.description,
                    "kind": getattr(n, "node_kind", type(n).__name__),
                    "metadata": n.metadata,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "label": e.label,
                    "metadata": e.metadata,
                    "has_condition": e.condition is not None,
                }
                for e in self.edges
            ],
            "cycles": self.detect_cycles(),
        }
