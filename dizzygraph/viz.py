"""Graph visualization — Mermaid (primary) + optional networkx PNG."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

log = logging.getLogger("dizzygraph.viz")

if TYPE_CHECKING:
    from .executor import ExecutionTrace
    from .graph import Graph

COLORS = {
    "atomic": "#4ade80",
    "loop": "#f59e0b",
    "subgraph": "#818cf8",
    "agent": "#f472b6",
    "map": "#38bdf8",
    "base": "#94a3b8",
}

MERMAID_SHAPES = {
    "loop": ("{{", "}}"),  # hexagon-ish via stadium alternative — use [[ ]]
    "agent": ("([", "])"),
    "subgraph": ("[[", "]]"),
    "map": ("[/", "/]"),
    "atomic": ("[", "]"),
}


def to_mermaid(graph: "Graph", *, highlight_path: list[str] | None = None) -> str:
    """
    Emit a Mermaid flowchart. Paste into GitHub Markdown or mermaid.live.

    LoopNodes render as stadium nodes; cycles get a note.
    """
    highlight = set(highlight_path or [])
    lines = ["flowchart TD"]
    for nid, node in graph.nodes.items():
        kind = getattr(node, "node_kind", "base")
        left, right = MERMAID_SHAPES.get(kind, ("[", "]"))
        if kind == "loop":
            left, right = "([", "])"
        label = (node.name or nid).replace('"', "'")
        suffix = f" \\n«{kind}»" if kind != "atomic" else ""
        style_id = nid.replace("-", "_")
        lines.append(f'  {style_id}{left}"{label}{suffix}"{right}')
        if nid in highlight:
            lines.append(f"  style {style_id} fill:#fef08a,stroke:#ca8a04,stroke-width:2px")
        elif kind == "loop":
            lines.append(f"  style {style_id} fill:#fef3c7,stroke:#d97706")
        elif kind == "agent":
            lines.append(f"  style {style_id} fill:#fce7f3,stroke:#db2777")

    for e in graph.edges:
        src = e.source_id.replace("-", "_")
        tgt = e.target_id.replace("-", "_")
        if e.label:
            lab = e.label.replace('"', "'")
            lines.append(f"  {src} -->|{lab}| {tgt}")
        else:
            lines.append(f"  {src} --> {tgt}")

    cycles = graph.detect_cycles()
    if cycles:
        lines.append(f"  %% cycles: {cycles}")
    return "\n".join(lines)


def path_from_trace(trace: "ExecutionTrace") -> list[str]:
    return [t.node_id for t in trace.node_traces]


def visualize_graph(
    graph: "Graph",
    *,
    path: str | Path | None = "dizzygraph.png",
    show: bool = False,
    title: str | None = None,
    highlight_path: list[str] | None = None,
) -> Path | None:
    """Optional PNG via networkx/matplotlib. Prefer ``to_mermaid`` for docs."""
    try:
        import matplotlib.pyplot as plt
        import networkx as nx
    except ImportError:
        log.warning("networkx/matplotlib not installed — use to_mermaid() instead")
        return None

    g = nx.DiGraph()
    for nid, node in graph.nodes.items():
        g.add_node(nid, kind=getattr(node, "node_kind", "base"), label=node.name or nid)
    for e in graph.edges:
        g.add_edge(e.source_id, e.target_id, label=e.label)

    cycle_nodes: set[str] = set()
    for cyc in graph.detect_cycles():
        cycle_nodes.update(cyc)
    highlight = set(highlight_path or [])

    pos = nx.spring_layout(g, seed=7, k=1.4)
    node_colors = []
    for n in g.nodes:
        if n in highlight:
            node_colors.append("#fef08a")
        else:
            kind = g.nodes[n].get("kind", "base")
            node_colors.append(COLORS.get(kind, COLORS["base"]))

    plt.figure(figsize=(10, 7))
    nx.draw_networkx_nodes(g, pos, node_color=node_colors, node_size=1600, edgecolors="#111")
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
