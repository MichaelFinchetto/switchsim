"""Discrete-event core: simulation clock and a stable priority queue.

Events are ordered by ``(time_ns, priority, seq)``. ``seq`` is a monotonically
increasing counter assigned at scheduling time, so two events with the same
time and priority are processed in the order they were scheduled. This makes
every simulation run deterministic for a given input.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, Iterator, List, Optional


class EventKind(IntEnum):
    """Event types. The integer value doubles as a tie-break priority: when
    several events share a timestamp, lower values run first. Releases run
    before arrivals so that a port freed at time t can be re-used by a flow
    arriving at t; scheduling always runs last."""

    TX_DONE = 0
    RECONFIG_DONE = 1
    ARRIVAL = 2
    CUSTOM = 3
    SCHEDULE = 4


@dataclass(order=True)
class Event:
    time_ns: int
    priority: int
    seq: int
    kind: EventKind = field(compare=False)
    payload: Any = field(compare=False, default=None)
    cancelled: bool = field(compare=False, default=False)


class EventQueue:
    """Min-heap of :class:`Event` with lazy cancellation."""

    def __init__(self) -> None:
        self._heap: List[Event] = []
        self._seq = 0
        self._live = 0

    def __len__(self) -> int:
        return self._live

    def __bool__(self) -> bool:
        return self._live > 0

    def push(self, time_ns: int, kind: EventKind, payload: Any = None, priority: Optional[int] = None) -> Event:
        if time_ns < 0:
            raise ValueError("event time must be non-negative")
        ev = Event(int(time_ns), int(kind) if priority is None else int(priority), self._seq, kind, payload)
        self._seq += 1
        self._live += 1
        heapq.heappush(self._heap, ev)
        return ev

    def cancel(self, ev: Event) -> None:
        if not ev.cancelled:
            ev.cancelled = True
            self._live -= 1

    def peek(self) -> Optional[Event]:
        self._drop_cancelled()
        return self._heap[0] if self._heap else None

    def pop(self) -> Event:
        self._drop_cancelled()
        if not self._heap:
            raise IndexError("pop from empty event queue")
        ev = heapq.heappop(self._heap)
        self._live -= 1
        return ev

    def _drop_cancelled(self) -> None:
        while self._heap and self._heap[0].cancelled:
            heapq.heappop(self._heap)

    def drain(self) -> Iterator[Event]:
        while self:
            yield self.pop()


Handler = Callable[["Clock", Event], None]


class Clock:
    """Simulation clock plus dispatch loop.

    Handlers are registered per :class:`EventKind`. ``run`` processes events
    in order until the queue is empty, ``until_ns`` is reached, or
    ``max_events`` have been processed.
    """

    def __init__(self) -> None:
        self.now_ns = 0
        self.queue = EventQueue()
        self.processed = 0
        self._handlers: Dict[EventKind, Handler] = {}

    def on(self, kind: EventKind, handler: Handler) -> None:
        self._handlers[kind] = handler

    def schedule(self, delay_ns: int, kind: EventKind, payload: Any = None) -> Event:
        if delay_ns < 0:
            raise ValueError("delay must be non-negative")
        return self.queue.push(self.now_ns + int(delay_ns), kind, payload)

    def schedule_at(self, time_ns: int, kind: EventKind, payload: Any = None) -> Event:
        if time_ns < self.now_ns:
            raise ValueError("cannot schedule an event in the past")
        return self.queue.push(int(time_ns), kind, payload)

    def step(self) -> Optional[Event]:
        ev = self.queue.peek()
        if ev is None:
            return None
        self.queue.pop()
        if ev.time_ns < self.now_ns:
            raise RuntimeError("event queue produced a time in the past")
        self.now_ns = ev.time_ns
        handler = self._handlers.get(ev.kind)
        if handler is None:
            raise RuntimeError(f"no handler registered for {ev.kind.name}")
        handler(self, ev)
        self.processed += 1
        return ev

    def run(self, until_ns: Optional[int] = None, max_events: Optional[int] = None) -> int:
        count = 0
        while True:
            if max_events is not None and count >= max_events:
                break
            nxt = self.queue.peek()
            if nxt is None:
                break
            if until_ns is not None and nxt.time_ns > until_ns:
                self.now_ns = until_ns
                break
            self.step()
            count += 1
        return count


def ns_to_us(ns: int) -> float:
    return ns / 1e3


def ns_to_ms(ns: int) -> float:
    return ns / 1e6


def bytes_to_gbit(nbytes: int) -> float:
    return nbytes * 8 / 1e9


def transmission_time_ns(size_bytes: int, bandwidth_gbps: float) -> int:
    """Serialisation delay, rounded up to the next nanosecond."""
    if bandwidth_gbps <= 0:
        raise ValueError("bandwidth must be positive")
    if size_bytes < 0:
        raise ValueError("size must be non-negative")
    bits = size_bytes * 8
    ns = bits / bandwidth_gbps  # Gbit/s == bit/ns
    return int(-(-ns // 1))  # ceil without importing math
