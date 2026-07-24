"""Admission / backpressure helpers for the fleet control plane."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class AdmissionRejected(RuntimeError):
    """Raised when the fleet refuses a new run under backpressure."""

    def __init__(self, reason: str, *, inflight: int, max_inflight: int):
        super().__init__(reason)
        self.reason = reason
        self.inflight = inflight
        self.max_inflight = max_inflight


@dataclass
class AdmissionController:
    """Bound in-flight runs; reject (or optionally wait) when saturated.

    Thread-pool queues are otherwise unbounded — this is the control-plane
    admission gate so API callers get a clear 429-style failure instead of
    unbounded memory growth.
    """

    max_inflight: int = 64
    _inflight: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cond: threading.Condition | None = field(default=None, repr=False)
    rejected: int = 0
    admitted: int = 0

    def __post_init__(self) -> None:
        if self._cond is None:
            self._cond = threading.Condition(self._lock)

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def try_acquire(self) -> bool:
        with self._lock:
            if self._inflight >= self.max_inflight:
                self.rejected += 1
                return False
            self._inflight += 1
            self.admitted += 1
            return True

    def acquire(self, *, block: bool = False, timeout_s: float | None = None) -> None:
        assert self._cond is not None
        deadline = (time.time() + timeout_s) if timeout_s is not None else None
        with self._cond:
            while self._inflight >= self.max_inflight:
                if not block:
                    self.rejected += 1
                    raise AdmissionRejected(
                        f"fleet at capacity ({self._inflight}/{self.max_inflight})",
                        inflight=self._inflight,
                        max_inflight=self.max_inflight,
                    )
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self.rejected += 1
                        raise AdmissionRejected(
                            f"admission wait timed out ({self._inflight}/{self.max_inflight})",
                            inflight=self._inflight,
                            max_inflight=self.max_inflight,
                        )
                self._cond.wait(timeout=remaining)
            self._inflight += 1
            self.admitted += 1

    def release(self) -> None:
        assert self._cond is not None
        with self._cond:
            if self._inflight > 0:
                self._inflight -= 1
            self._cond.notify_all()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "inflight": self._inflight,
                "max_inflight": self.max_inflight,
                "admitted": self.admitted,
                "rejected": self.rejected,
            }
