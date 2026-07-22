"""Observability hooks — wire Galileo, logging, or custom sinks without forking the executor."""

from __future__ import annotations

from typing import Any, Protocol

from .events import StreamEvent
from .state import State


class CallbackHandler(Protocol):
    def on_graph_start(self, graph_id: str, state: State) -> None: ...
    def on_graph_end(self, graph_id: str, state: State, duration_s: float) -> None: ...
    def on_node_start(self, node_id: str, state: State) -> None: ...
    def on_node_end(self, node_id: str, state: State, duration_s: float) -> None: ...
    def on_node_error(self, node_id: str, error: str) -> None: ...
    def on_event(self, event: StreamEvent) -> None: ...


class BaseCallbackHandler:
    """No-op base — subclass what you need."""

    def on_graph_start(self, graph_id: str, state: State) -> None:
        pass

    def on_graph_end(self, graph_id: str, state: State, duration_s: float) -> None:
        pass

    def on_node_start(self, node_id: str, state: State) -> None:
        pass

    def on_node_end(self, node_id: str, state: State, duration_s: float) -> None:
        pass

    def on_node_error(self, node_id: str, error: str) -> None:
        pass

    def on_event(self, event: StreamEvent) -> None:
        pass


class LoggingCallback(BaseCallbackHandler):
    def __init__(self, logger: Any | None = None):
        import logging

        self.log = logger or logging.getLogger("dizzygraph")

    def on_node_start(self, node_id: str, state: State) -> None:
        self.log.info("→ %s", node_id)

    def on_node_end(self, node_id: str, state: State, duration_s: float) -> None:
        self.log.info("← %s  %.3fs", node_id, duration_s)

    def on_node_error(self, node_id: str, error: str) -> None:
        self.log.error("✗ %s  %s", node_id, error)


class FanoutCallbacks(BaseCallbackHandler):
    def __init__(self, handlers: list[BaseCallbackHandler] | None = None):
        self.handlers = handlers or []

    def add(self, handler: BaseCallbackHandler) -> None:
        self.handlers.append(handler)

    def on_graph_start(self, graph_id: str, state: State) -> None:
        for h in self.handlers:
            h.on_graph_start(graph_id, state)

    def on_graph_end(self, graph_id: str, state: State, duration_s: float) -> None:
        for h in self.handlers:
            h.on_graph_end(graph_id, state, duration_s)

    def on_node_start(self, node_id: str, state: State) -> None:
        for h in self.handlers:
            h.on_node_start(node_id, state)

    def on_node_end(self, node_id: str, state: State, duration_s: float) -> None:
        for h in self.handlers:
            h.on_node_end(node_id, state, duration_s)

    def on_node_error(self, node_id: str, error: str) -> None:
        for h in self.handlers:
            h.on_node_error(node_id, error)

    def on_event(self, event: StreamEvent) -> None:
        for h in self.handlers:
            h.on_event(event)
