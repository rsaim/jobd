"""Run events: one stream, three consumers.

The graph's nodes call :meth:`Emitter.emit` and know nothing about who is
watching. Consumers differ only in how they read:

* the CLI passes a ``sink`` and prints lines as they happen;
* the dashboard's SSE route long-polls :meth:`wait` from its own thread;
* the run summary reads the final counters off the last event.

Thread-safe on purpose — the graph runs in a worker thread when the dashboard
starts it, while SSE readers arrive on server threads. A bounded ring buffer
(not an unbounded log) backs replay: a dashboard that connects mid-run gets
the recent history and follows live, and a run that emits millions of events
cannot eat the process. Sequence numbers make the hand-off race-free: a reader
resumes from the last ``seq`` it saw, so nothing is dropped between snapshot
and subscribe.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    ts: float
    kind: str
    data: dict[str, Any]


@dataclass
class Emitter:
    """Fan-out point for one scrape run."""

    sink: Callable[[Event], None] | None = None
    _buf: deque[Event] = field(default_factory=lambda: deque(maxlen=2000))
    _seq: int = 0
    _cond: threading.Condition = field(default_factory=threading.Condition)

    def emit(self, kind: str, **data: Any) -> None:
        with self._cond:
            self._seq += 1
            event = Event(seq=self._seq, ts=time.time(), kind=kind, data=data)
            self._buf.append(event)
            self._cond.notify_all()
        if self.sink is not None:
            self.sink(event)

    def after(self, seq: int) -> list[Event]:
        """Everything currently buffered past ``seq`` (possibly empty)."""
        with self._cond:
            return [e for e in self._buf if e.seq > seq]

    def wait(self, seq: int, timeout: float = 15.0) -> list[Event]:
        """Block until something newer than ``seq`` exists, or timeout.

        A timeout returns ``[]`` rather than raising — the SSE route turns
        that into a heartbeat comment, which is exactly what keeps proxies
        from killing an idle stream.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                fresh = [e for e in self._buf if e.seq > seq]
                if fresh:
                    return fresh
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)

    @property
    def last_seq(self) -> int:
        with self._cond:
            return self._seq
