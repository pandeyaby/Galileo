# === dizzygraph/viz.py ===
"""Graph visualization — node colors by type, cycle highlighting."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

log = logging.getLogger("dizzygraph.viz")

if TYPE_CHECKING:
    from .graph import Graph

COLORS = {
    "atomic": "#4ade80",
    "loop": "#f59e0b",
    "subgraph": "#818cf8",
    "agent": "#f472b6",
    "base": "#94a3b8",
}


def visualize_graph(
    graph: "Graph",
    *,
    path: str | Path | None = "dizzygraph.png",
    show: bool = False,
    title: str | None = None,
) -> Path | None:
    try:
        import matplotlib.pyplot as plt
        import networkx as nx
    except ImportError:
        log.warning("networkx/matplotlib not installed — skip visualize")
        return None

    g = nx.DiGraph()
    for nid, node in graph.nodes.items():
        g.add_node(nid, kind=getattr(node, "node_kind", "base"), label=node.name or nid)
    for e in graph.edges:
        g.add_edge(e.source_id, e.target_id, label=e.label)

    cycle_nodes: set[str] = set()
    for cyc in graph.detect_cycles():
        cycle_nodes.update(cyc)

    pos = nx.spring_layout(g, seed=7, k=1.4)
    node_colors = []
    for n in g.nodes:
        kind = g.nodes[n].get("kind", "base")
        node_colors.append(COLORS.get(kind, COLORS["base"]))

    plt.figure(figsize=(10, 7))
    nx.draw_networkx_nodes(g, pos, node_color=node_colors, node_size=1600, edgecolors="#111")
    # highlight cycles with thicker edges
    edge_colors = []
    widths = []
    for u, v in g.edges:
        if u in cycle_nodes and v in cycle_nodes:
            edge_colors.append("#ef4444")
            widths.append(2.8)
        else:
            edge_colors.append("#64748b")
            widths.append(1.4)
    nx.draw_networkx_edges(g, pos, edge_color=edge_colors, width=widths, arrows=True, arrowsize=18)
    labels = {n: g.nodes[n].get("label", n) for n in g.nodes}
    nx.draw_networkx_labels(g, pos, labels=labels, font_size=8)
    edge_labels = {(e.source_id, e.target_id): e.label for e in graph.edges if e.label}
    if edge_labels:
        nx.draw_networkx_edge_labels(g, pos, edge_labels=edge_labels, font_size=7)
    plt.title(title or graph.name)
    plt.axis("off")
    out = None
    if path:
        out = Path(path)
        plt.savefig(out, dpi=160, bbox_inches="tight")
        log.info("wrote %s", out)
    if show:
        plt.show()
    else:
        plt.close()
    return out
