"""Event bus — SQLite log + optional Redis pub/sub for multi-worker fan-out."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

Subscriber = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self, store, *, redis_url: str | None = None):
        self.store = store
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()
        self._redis = None
        self._pubsub_thread: threading.Thread | None = None
        self._channel = "dizzygraph:events"
        self._origin_id = uuid.uuid4().hex
        self._stop = threading.Event()
        url = redis_url or os.environ.get("DIZZY_REDIS_URL") or os.environ.get("REDIS_URL")
        self.redis_url = url
        if url:
            self._init_redis(url)

    def _init_redis(self, url: str) -> None:
        try:
            import redis
        except ImportError as exc:
            raise ImportError(
                "Redis bus requires redis package: pip install 'dizzygraph[control-redis]'"
            ) from exc
        self._redis = redis.Redis.from_url(url, decode_responses=True)
        self._redis.ping()
        self._pubsub_thread = threading.Thread(target=self._redis_listen, daemon=True)
        self._pubsub_thread.start()

    def _redis_listen(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(self._channel)
        for message in pubsub.listen():
            if self._stop.is_set():
                break
            if message.get("type") != "message":
                continue
            try:
                event = json.loads(message["data"])
            except Exception:
                continue
            if event.get("_origin") == self._origin_id:
                continue
            event.pop("_origin", None)
            self._notify_local(event)

    def close(self) -> None:
        self._stop.set()
        if self._redis:
            try:
                self._redis.close()
            except Exception:
                pass

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsub() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsub

    def _notify_local(self, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event)
            except Exception:
                pass

    def publish(
        self,
        *,
        thread_id: str,
        graph_id: str,
        type: str,
        node_id: str | None = None,
        payload: dict | None = None,
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        event = self.store.append_event(
            thread_id=thread_id,
            graph_id=graph_id,
            type=type,
            node_id=node_id,
            payload=payload,
            tenant_id=tenant_id,
        )
        self._notify_local(event)
        if self._redis is not None:
            try:
                wire = dict(event)
                wire["_origin"] = self._origin_id
                self._redis.publish(self._channel, json.dumps(wire, default=str))
            except Exception:
                pass
        return event

    def poll(
        self, last_id: int = 0, *, tenant_id: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        return self.store.events_since(last_id, tenant_id=tenant_id, limit=limit)

    def wait(
        self,
        last_id: int = 0,
        *,
        tenant_id: str | None = None,
        timeout_s: float = 15.0,
    ) -> list[dict[str, Any]]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            batch = self.poll(last_id, tenant_id=tenant_id, limit=200)
            if batch:
                return batch
            time.sleep(0.15)
        return []
