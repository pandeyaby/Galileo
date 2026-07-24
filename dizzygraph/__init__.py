"""
DizzyGraph — graphs made of loops, and loops over those graphs.

Runtime features: channel reducers, per-node checkpoints, streaming events,
HITL interrupt/resume, retries, timeouts, Mermaid viz, meta-loops.
"""

from .state import State, merge_state, apply_values
from .reducers import (
    replace,
    append,
    unique_append,
    merge_dicts,
    register_data_reducer,
    clear_data_reducers,
)
from .nodes import (
    Node,
    AtomicNode,
    LoopNode,
    SubGraphNode,
    AgentNode,
    MapNode,
    register_node_type,
    NODE_REGISTRY,
)
from .edges import Edge
from .graph import Graph
from .executor import GraphExecutor, ExecutionTrace, NodeTrace
from .compile import CompiledGraph, compile_graph
from .meta import MetaLoopExecutor, MetaLoopResult
from .checkpoint import Checkpointer, MemoryCheckpointer, FileCheckpointer, Checkpoint
from .fail_policy import FailPolicy, coerce_fail_policy
from .interrupt import interrupt, GraphInterrupt
from .retry import RetryPolicy
from .events import StreamEvent
from .callbacks import BaseCallbackHandler, LoggingCallback, FanoutCallbacks
from .otel import (
    DizzyGraphTracer,
    OpenTelemetryCallback,
    OtelConfig,
    build_sampler,
    maybe_open_telemetry_callback,
    otel_available,
    otel_enabled,
    resolve_sampler_name,
    setup_galileo_tracer_provider,
    setup_tracer_provider,
)
from .viz import visualize_graph, to_mermaid, path_from_trace
from .persist import (
    save_graph,
    load_graph,
    save_graph_skeleton,
    load_graph_skeleton,
    save_state,
    load_state,
)

__all__ = [
    "State",
    "merge_state",
    "apply_values",
    "replace",
    "append",
    "unique_append",
    "merge_dicts",
    "register_data_reducer",
    "clear_data_reducers",
    "Node",
    "AtomicNode",
    "LoopNode",
    "SubGraphNode",
    "AgentNode",
    "MapNode",
    "register_node_type",
    "NODE_REGISTRY",
    "Edge",
    "Graph",
    "GraphExecutor",
    "CompiledGraph",
    "compile_graph",
    "ExecutionTrace",
    "NodeTrace",
    "MetaLoopExecutor",
    "MetaLoopResult",
    "Checkpointer",
    "MemoryCheckpointer",
    "FileCheckpointer",
    "Checkpoint",
    "FailPolicy",
    "coerce_fail_policy",
    "interrupt",
    "GraphInterrupt",
    "RetryPolicy",
    "StreamEvent",
    "BaseCallbackHandler",
    "LoggingCallback",
    "FanoutCallbacks",
    "DizzyGraphTracer",
    "OpenTelemetryCallback",
    "OtelConfig",
    "build_sampler",
    "maybe_open_telemetry_callback",
    "otel_available",
    "otel_enabled",
    "resolve_sampler_name",
    "setup_galileo_tracer_provider",
    "setup_tracer_provider",
    "visualize_graph",
    "to_mermaid",
    "path_from_trace",
    "save_graph",
    "load_graph",
    "save_graph_skeleton",
    "load_graph_skeleton",
    "save_state",
    "load_state",
]

__version__ = "0.5.0"
