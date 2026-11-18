"""Metrics computed from a :class:`~optiswitch.sim.SimResult`.

All functions are pure and operate on completed flows unless stated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .sim import FlowRecord, SimResult


class MetricsError(ValueError):
    pass


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, ``q`` in [0, 100]."""
    if not values:
        raise MetricsError("percentile of empty sequence")
    if not 0.0 <= q <= 100.0:
        raise MetricsError("q must be in [0, 100]")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(xs[lo])
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def mean(values: Sequence[float]) -> float:
    if not values:
        raise MetricsError("mean of empty sequence")
    return sum(values) / len(values)


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def jain_fairness(values: Sequence[float]) -> float:
    """Jain's index: 1.0 when all values are equal, 1/n when one dominates."""
    if not values:
        raise MetricsError("fairness of empty sequence")
    s = sum(values)
    if s == 0:
        return 1.0
    return s * s / (len(values) * sum(v * v for v in values))


@dataclass
class FctStats:
    count: int
    mean_ns: float
    median_ns: float
    p99_ns: float
    max_ns: int
    mean_slowdown: float
    """Mean of FCT / ideal FCT; 1.0 means no queueing or reconfiguration cost."""
    p99_slowdown: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "count": self.count,
            "mean_ns": self.mean_ns,
            "median_ns": self.median_ns,
            "p99_ns": self.p99_ns,
            "max_ns": self.max_ns,
            "mean_slowdown": self.mean_slowdown,
            "p99_slowdown": self.p99_slowdown,
        }


def fct_stats(records: Sequence[FlowRecord]) -> FctStats:
    done = [r for r in records if r.completed]
    if not done:
        raise MetricsError("no completed flows")
    fcts = [float(r.fct_ns) for r in done]  # type: ignore[arg-type]
    slow = [r.fct_ns / r.ideal_fct_ns for r in done]  # type: ignore[operator]
    return FctStats(
        count=len(done),
        mean_ns=mean(fcts),
        median_ns=percentile(fcts, 50),
        p99_ns=percentile(fcts, 99),
        max_ns=int(max(fcts)),
        mean_slowdown=mean(slow),
        p99_slowdown=percentile(slow, 99),
    )


def throughput_gbps(result: SimResult) -> float:
    """Delivered bits over the makespan (from first arrival to last completion)."""
    done = result.completed()
    if not done:
        return 0.0
    first = min(r.flow.arrival_ns for r in result.records)
    span = result.makespan_ns - first
    if span <= 0:
        return 0.0
    bits = sum(r.flow.size_bytes for r in done) * 8
    return bits / span  # bit/ns == Gbit/s


def offered_load_gbps(result: SimResult) -> float:
    """Offered bits over the arrival window."""
    arrivals = [r.flow.arrival_ns for r in result.records]
    span = max(arrivals) - min(arrivals)
    if span <= 0:
        return math.inf
    return sum(r.flow.size_bytes for r in result.records) * 8 / span


def port_utilisation(result: SimResult) -> Dict[str, List[float]]:
    """Fraction of the makespan each ingress/egress port was holding a circuit."""
    first = min(r.flow.arrival_ns for r in result.records) if result.records else 0
    span = max(result.makespan_ns - first, 1)
    return {
        "ingress": [u.busy_ns / span for u in result.ingress],
        "egress": [u.busy_ns / span for u in result.egress],
    }


def per_host_throughput_gbps(result: SimResult) -> List[float]:
    """Bits each host *sent* over the makespan."""
    n = result.topology.n_hosts
    sent = [0] * n
    for r in result.completed():
        sent[r.flow.src] += r.flow.size_bytes * 8
    first = min(r.flow.arrival_ns for r in result.records) if result.records else 0
    span = max(result.makespan_ns - first, 1)
    return [b / span for b in sent]


def reconfiguration_overhead(result: SimResult) -> float:
    """Fraction of total circuit-holding time spent in reconfiguration."""
    done = result.completed()
    if not done:
        return 0.0
    hold = sum(r.done_ns - r.grant_ns for r in done)  # type: ignore[operator]
    reconf = sum(r.start_ns - r.grant_ns for r in done)  # type: ignore[operator]
    return reconf / hold if hold else 0.0


def littles_law_check(result: SimResult) -> Dict[str, float]:
    """Compare mean pending count with arrival rate x mean wait (Little's law).

    Returns the two sides and their relative difference. For a stable,
    long-enough run the difference is small; a large one indicates the run
    did not reach steady state or flows are still pending.
    """
    recs = result.completed()
    if len(recs) < 2:
        raise MetricsError("need at least two completed flows")
    first = min(r.flow.arrival_ns for r in recs)
    last = max(r.grant_ns for r in recs)  # type: ignore[type-var]
    span = max(last - first, 1)
    arrival_rate = len(recs) / span
    mean_wait = mean([float(r.wait_ns) for r in recs])  # type: ignore[arg-type]
    # time-average of pending count: integrate (grant - arrival) over the window
    pending_area = sum(r.wait_ns for r in recs)  # type: ignore[misc]
    mean_pending = pending_area / span
    rhs = arrival_rate * mean_wait
    rel = abs(mean_pending - rhs) / max(rhs, 1e-12)
    return {"mean_pending": mean_pending, "rate_x_wait": rhs, "relative_difference": rel}


@dataclass
class Summary:
    scheduler: str
    flows: int
    completed: int
    makespan_ns: int
    throughput_gbps: float
    offered_gbps: float
    fct: FctStats
    fairness: float
    reconfig_overhead: float
    mean_ingress_util: float
    mean_egress_util: float
    stalled: bool
    extra: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        d = {
            "scheduler": self.scheduler,
            "flows": self.flows,
            "completed": self.completed,
            "makespan_ns": self.makespan_ns,
            "throughput_gbps": self.throughput_gbps,
            "offered_gbps": self.offered_gbps,
            "fct": self.fct.to_dict(),
            "fairness": self.fairness,
            "reconfig_overhead": self.reconfig_overhead,
            "mean_ingress_util": self.mean_ingress_util,
            "mean_egress_util": self.mean_egress_util,
            "stalled": self.stalled,
        }
        d.update(self.extra)
        return d


def summarise(result: SimResult) -> Summary:
    util = port_utilisation(result)
    return Summary(
        scheduler=str(result.scheduler.get("name", "?")),
        flows=len(result.records),
        completed=len(result.completed()),
        makespan_ns=result.makespan_ns,
        throughput_gbps=throughput_gbps(result),
        offered_gbps=offered_load_gbps(result),
        fct=fct_stats(result.records),
        fairness=jain_fairness(per_host_throughput_gbps(result)),
        reconfig_overhead=reconfiguration_overhead(result),
        mean_ingress_util=mean(util["ingress"]),
        mean_egress_util=mean(util["egress"]),
        stalled=result.stalled,
    )


def check_invariants(result: SimResult) -> List[str]:
    """Return a list of violated invariants (empty if all hold)."""
    problems: List[str] = []
    for r in result.records:
        if r.grant_ns is not None and r.grant_ns < r.flow.arrival_ns:
            problems.append(f"flow {r.flow.id} granted before arrival")
        if r.start_ns is not None and r.grant_ns is not None and r.start_ns - r.grant_ns != result.topology.switch.reconfig_ns:
            problems.append(f"flow {r.flow.id} reconfiguration time wrong")
        if r.completed and r.fct_ns < r.ideal_fct_ns:  # type: ignore[operator]
            problems.append(f"flow {r.flow.id} completed faster than physically possible")
    # no two concurrent circuits share a port
    events: Dict[int, List[tuple]] = {}
    for r in result.records:
        if r.grant_ns is None:
            continue
        end = r.done_ns if r.done_ns is not None else result.makespan_ns
        events.setdefault(r.flow.src, []).append((r.grant_ns, end, "in", r.flow.id))
        events.setdefault(1_000_000 + r.flow.dst, []).append((r.grant_ns, end, "out", r.flow.id))
    for port, ivals in events.items():
        ivals.sort()
        for (s1, e1, _, f1), (s2, e2, _, f2) in zip(ivals, ivals[1:]):
            if s2 < e1:
                side = "egress" if port >= 1_000_000 else "ingress"
                problems.append(f"{side} port {port % 1_000_000}: flows {f1} and {f2} overlap")
    delivered = sum(r.flow.size_bytes for r in result.completed())
    offered = sum(r.flow.size_bytes for r in result.records)
    if delivered > offered:
        problems.append("delivered more bytes than offered")
    return problems
