"""Circuit schedulers.

A scheduler is asked, whenever something changes, which of the *pending*
flows should be granted a circuit given the currently free ingress and
egress ports. It must return flows whose ``src`` ports are distinct, whose
``dst`` ports are distinct, and which are all currently free. The engine
checks these invariants and raises :class:`SchedulerError` on violation.

Only ``FifoScheduler`` ships with the baseline; see ``TASK.md`` for the
schedulers to add.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Type

from .workload import Flow


class SchedulerError(RuntimeError):
    pass


@dataclass
class PendingFlow:
    """A flow waiting for a circuit, with bookkeeping the scheduler may use."""

    flow: Flow
    arrival_ns: int
    attempts: int = 0
    """Number of scheduling rounds in which this flow was not granted."""

    @property
    def src(self) -> int:
        return self.flow.src

    @property
    def dst(self) -> int:
        return self.flow.dst

    @property
    def size_bytes(self) -> int:
        return self.flow.size_bytes


class Scheduler:
    """Base class. Subclasses implement :meth:`select`."""

    name: str = "base"

    def __init__(self, n_hosts: int) -> None:
        if n_hosts < 2:
            raise SchedulerError("scheduler needs at least two hosts")
        self.n_hosts = n_hosts

    def reset(self) -> None:
        """Called once before a simulation starts."""

    def select(
        self,
        pending: Sequence[PendingFlow],
        free_in: Set[int],
        free_out: Set[int],
        now_ns: int,
    ) -> List[PendingFlow]:  # pragma: no cover - interface
        raise NotImplementedError

    def on_grant(self, pf: PendingFlow, now_ns: int) -> None:
        """Hook called by the engine for each granted flow."""

    def on_release(self, flow: Flow, now_ns: int) -> None:
        """Hook called by the engine when a circuit is torn down."""

    def describe(self) -> Dict[str, object]:
        return {"name": self.name, "n_hosts": self.n_hosts}


class FifoScheduler(Scheduler):
    """Grant in arrival order; a blocked flow does not block later ones
    whose ports are free (head-of-line blocking is avoided per port)."""

    name = "fifo"

    def select(self, pending, free_in, free_out, now_ns):
        chosen: List[PendingFlow] = []
        used_in: Set[int] = set()
        used_out: Set[int] = set()
        for pf in sorted(pending, key=lambda p: (p.arrival_ns, p.flow.id)):
            if pf.src in free_in and pf.dst in free_out and pf.src not in used_in and pf.dst not in used_out:
                chosen.append(pf)
                used_in.add(pf.src)
                used_out.add(pf.dst)
        return chosen


def check_selection(selected: Sequence[PendingFlow], free_in: Set[int], free_out: Set[int]) -> None:
    """Validate a scheduler's answer. Raises :class:`SchedulerError`."""
    seen_in: Set[int] = set()
    seen_out: Set[int] = set()
    for pf in selected:
        if pf.src not in free_in:
            raise SchedulerError(f"flow {pf.flow.id}: ingress port {pf.src} is busy")
        if pf.dst not in free_out:
            raise SchedulerError(f"flow {pf.flow.id}: egress port {pf.dst} is busy")
        if pf.src in seen_in:
            raise SchedulerError(f"ingress port {pf.src} granted twice in one round")
        if pf.dst in seen_out:
            raise SchedulerError(f"egress port {pf.dst} granted twice in one round")
        seen_in.add(pf.src)
        seen_out.add(pf.dst)


_REGISTRY: Dict[str, Type[Scheduler]] = {"fifo": FifoScheduler}


def register(cls: Type[Scheduler]) -> Type[Scheduler]:
    """Class decorator adding a scheduler to the registry under ``cls.name``."""
    if not cls.name or cls.name == "base":
        raise SchedulerError("scheduler class must define a unique name")
    _REGISTRY[cls.name] = cls
    return cls


def available() -> Sequence[str]:
    return tuple(sorted(_REGISTRY))


def make(name: str, n_hosts: int, **kwargs) -> Scheduler:
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise SchedulerError(f"unknown scheduler {name!r}; available: {', '.join(available())}") from None
    return cls(n_hosts, **kwargs)
