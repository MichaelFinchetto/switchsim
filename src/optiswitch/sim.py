"""The simulation engine.

Flow lifecycle::

    ARRIVAL ──► pending ──(scheduler grants)──► RECONFIG ──► TRANSMIT ──► DONE
                                                 reconfig_ns   size/bw + latency

A granted flow holds its ingress and egress ports from the grant until the
last bit has been *received* at the destination (transmission time plus
one-way path latency), plus the switch guard time. Ports are then returned
and the scheduler is invoked again.

The engine is deterministic: given the same topology, flows and scheduler
it produces the same :class:`SimResult` every time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from .events import Clock, Event, EventKind, transmission_time_ns
from .scheduler import PendingFlow, Scheduler, SchedulerError, check_selection
from .topology import Topology
from .workload import Flow, validate as validate_flows


class SimulationError(RuntimeError):
    pass


@dataclass
class FlowRecord:
    """Timestamps of one flow's life. ``None`` until the stage is reached."""

    flow: Flow
    grant_ns: Optional[int] = None
    start_ns: Optional[int] = None
    done_ns: Optional[int] = None
    attempts: int = 0

    @property
    def completed(self) -> bool:
        return self.done_ns is not None

    @property
    def fct_ns(self) -> Optional[int]:
        """Flow completion time: arrival to last byte received."""
        return None if self.done_ns is None else self.done_ns - self.flow.arrival_ns

    @property
    def wait_ns(self) -> Optional[int]:
        """Time spent pending before a circuit was granted."""
        return None if self.grant_ns is None else self.grant_ns - self.flow.arrival_ns

    @property
    def ideal_fct_ns(self) -> int:
        """FCT with an always-free circuit and no reconfiguration."""
        return self._ideal

    _ideal: int = field(default=0, repr=False)


@dataclass
class PortUsage:
    busy_ns: int = 0
    circuits: int = 0
    _busy_since: Optional[int] = field(default=None, repr=False)

    def acquire(self, now_ns: int) -> None:
        if self._busy_since is not None:
            raise SimulationError("port acquired while busy")
        self._busy_since = now_ns
        self.circuits += 1

    def release(self, now_ns: int) -> None:
        if self._busy_since is None:
            raise SimulationError("port released while free")
        self.busy_ns += now_ns - self._busy_since
        self._busy_since = None

    @property
    def busy(self) -> bool:
        return self._busy_since is not None


@dataclass
class SimResult:
    topology: Topology
    scheduler: Dict[str, object]
    records: List[FlowRecord]
    ingress: List[PortUsage]
    egress: List[PortUsage]
    makespan_ns: int
    events_processed: int
    stalled: bool = False
    """True if the run ended with pending flows and no further events."""

    def completed(self) -> List[FlowRecord]:
        return [r for r in self.records if r.completed]

    def pending(self) -> List[FlowRecord]:
        return [r for r in self.records if not r.completed]


class Simulation:
    def __init__(
        self,
        topology: Topology,
        flows: Sequence[Flow],
        scheduler: Scheduler,
        max_time_ns: Optional[int] = None,
    ) -> None:
        topology.validate()
        validate_flows(flows, topology.n_hosts)
        if scheduler.n_hosts != topology.n_hosts:
            raise SimulationError("scheduler was built for a different number of hosts")
        self.topology = topology
        self.flows = list(flows)
        self.scheduler = scheduler
        self.max_time_ns = max_time_ns
        self.clock = Clock()
        self.records: Dict[int, FlowRecord] = {}
        self.pending: Dict[int, PendingFlow] = {}
        self.free_in: Set[int] = set(topology.host_ids())
        self.free_out: Set[int] = set(topology.host_ids())
        self.ingress = [PortUsage() for _ in topology.hosts]
        self.egress = [PortUsage() for _ in topology.hosts]
        self._schedule_queued = False
        self._done = 0

        c = self.clock
        c.on(EventKind.ARRIVAL, self._on_arrival)
        c.on(EventKind.SCHEDULE, self._on_schedule)
        c.on(EventKind.RECONFIG_DONE, self._on_reconfig_done)
        c.on(EventKind.TX_DONE, self._on_tx_done)
        c.on(EventKind.CUSTOM, self._on_custom)

        for f in self.flows:
            rec = FlowRecord(f)
            rec._ideal = self._ideal_fct(f)
            self.records[f.id] = rec
            c.schedule_at(f.arrival_ns, EventKind.ARRIVAL, f.id)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _ideal_fct(self, f: Flow) -> int:
        bw = self.topology.circuit_bandwidth_gbps(f.src, f.dst)
        return transmission_time_ns(f.size_bytes, bw) + self.topology.path_latency_ns(f.src, f.dst)

    def _request_schedule(self) -> None:
        if not self._schedule_queued:
            self._schedule_queued = True
            self.clock.schedule(0, EventKind.SCHEDULE)

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------
    def _on_arrival(self, clock: Clock, ev: Event) -> None:
        f = self.records[ev.payload].flow
        self.pending[f.id] = PendingFlow(f, clock.now_ns)
        self._request_schedule()

    def _on_schedule(self, clock: Clock, ev: Event) -> None:
        self._schedule_queued = False
        if not self.pending:
            return
        candidates = list(self.pending.values())
        chosen = self.scheduler.select(candidates, set(self.free_in), set(self.free_out), clock.now_ns)
        check_selection(chosen, self.free_in, self.free_out)
        chosen_ids = {pf.flow.id for pf in chosen}
        for pf in candidates:
            if pf.flow.id not in chosen_ids:
                pf.attempts += 1
        for pf in chosen:
            f = pf.flow
            del self.pending[f.id]
            self.free_in.discard(f.src)
            self.free_out.discard(f.dst)
            self.ingress[f.src].acquire(clock.now_ns)
            self.egress[f.dst].acquire(clock.now_ns)
            rec = self.records[f.id]
            rec.grant_ns = clock.now_ns
            rec.attempts = pf.attempts
            self.scheduler.on_grant(pf, clock.now_ns)
            clock.schedule(self.topology.switch.reconfig_ns, EventKind.RECONFIG_DONE, f.id)

    def _on_reconfig_done(self, clock: Clock, ev: Event) -> None:
        rec = self.records[ev.payload]
        rec.start_ns = clock.now_ns
        clock.schedule(rec.ideal_fct_ns, EventKind.TX_DONE, ev.payload)

    def _on_tx_done(self, clock: Clock, ev: Event) -> None:
        rec = self.records[ev.payload]
        rec.done_ns = clock.now_ns
        self._done += 1
        guard = self.topology.switch.guard_ns
        if guard > 0:
            clock.schedule(guard, EventKind.CUSTOM, ("release", ev.payload))
        else:
            self._release(rec.flow, clock.now_ns)

    def _on_custom(self, clock: Clock, ev: Event) -> None:
        tag, payload = ev.payload
        if tag == "release":
            self._release(self.records[payload].flow, clock.now_ns)
        else:
            raise SimulationError(f"unknown custom event {tag!r}")

    def _release(self, f: Flow, now_ns: int) -> None:
        self.ingress[f.src].release(now_ns)
        self.egress[f.dst].release(now_ns)
        self.free_in.add(f.src)
        self.free_out.add(f.dst)
        self.scheduler.on_release(f, now_ns)
        if self.pending:
            self._request_schedule()

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------
    def run(self) -> SimResult:
        self.scheduler.reset()
        self.clock.run(until_ns=self.max_time_ns)
        stalled = bool(self.pending) and not self.clock.queue
        # close any open busy intervals at the final time for utilisation accounting
        now = self.clock.now_ns
        for usage in (*self.ingress, *self.egress):
            if usage.busy:
                usage.busy_ns += now - usage._busy_since  # type: ignore[operator]
                usage._busy_since = now
        done_times = [r.done_ns for r in self.records.values() if r.done_ns is not None]
        makespan = max(done_times) if done_times else now
        return SimResult(
            topology=self.topology,
            scheduler=self.scheduler.describe(),
            records=[self.records[f.id] for f in self.flows],
            ingress=self.ingress,
            egress=self.egress,
            makespan_ns=makespan,
            events_processed=self.clock.processed,
            stalled=stalled,
        )


def simulate(
    topology: Topology,
    flows: Sequence[Flow],
    scheduler: Scheduler,
    max_time_ns: Optional[int] = None,
) -> SimResult:
    """Convenience wrapper: build and run a :class:`Simulation`."""
    return Simulation(topology, flows, scheduler, max_time_ns).run()
