"""Per-node retry policy."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    initial_interval_s: float = 0.2
    backoff_factor: float = 2.0
    max_interval_s: float = 8.0
    jitter: bool = True
    retry_on: tuple[type[BaseException], ...] = (Exception,)

    def should_retry(self, exc: BaseException) -> bool:
        return isinstance(exc, self.retry_on) and not isinstance(exc, type(None))

    def sleep(self, attempt: int) -> None:
        delay = min(
            self.max_interval_s,
            self.initial_interval_s * (self.backoff_factor ** max(0, attempt - 1)),
        )
        if self.jitter:
            delay *= 0.5 + random.random()
        time.sleep(delay)


def run_with_retry(
    fn: Callable[[], Any],
    policy: RetryPolicy | None,
    *,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> Any:
    """Run ``fn`` with optional retries. ``attempt`` is 1-indexed."""
    if policy is None or policy.max_attempts <= 1:
        return fn()
    last: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:
            last = exc
            # Never retry control-flow interrupts
            from .interrupt import GraphInterrupt

            if isinstance(exc, GraphInterrupt):
                raise
            if attempt >= policy.max_attempts or not policy.should_retry(exc):
                raise
            if on_retry:
                on_retry(attempt, exc)
            policy.sleep(attempt)
    assert last is not None
    raise last
