"""
DizzyGraph — graphs made of loops, and loops over those graphs.

Implements the 2026 vision: LoopNode (loop engineering), SubGraphNode (hierarchy),
GraphExecutor (cyclic/DAG runs), MetaLoopExecutor (meta-loops over whole graphs).
"""

from .state import State, merge_state
from .nodes import (
    Node,
    AtomicNode,
    LoopNode,
    SubGraphNode,
    AgentNode,
    register_node_type,
    NODE_REGISTRY,
)
from .edges import Edge
from .graph import Graph
from .executor import GraphExecutor, ExecutionTrace, NodeTrace
from .meta import MetaLoopExecutor, MetaLoopResult
from .viz import visualize_graph
from .persist import save_graph, load_graph, save_state, load_state

__all__ = [
    "State",
    "merge_state",
    "Node",
    "AtomicNode",
    "LoopNode",
    "SubGraphNode",
    "AgentNode",
    "register_node_type",
    "NODE_REGISTRY",
    "Edge",
    "Graph",
    "GraphExecutor",
    "ExecutionTrace",
    "NodeTrace",
    "MetaLoopExecutor",
    "MetaLoopResult",
    "visualize_graph",
    "save_graph",
    "load_graph",
    "save_state",
    "load_state",
]

__version__ = "0.1.0"
