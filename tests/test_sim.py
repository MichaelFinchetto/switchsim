import pytest

from optiswitch.metrics import check_invariants
from optiswitch.scheduler import FifoScheduler, PendingFlow, Scheduler, SchedulerError, check_selection, make, register
from optiswitch.sim import PortUsage, SimulationError, Simulation, simulate
from optiswitch.topology import Topology
from optiswitch.workload import FixedSize, Flow, WorkloadSpec, generate


def test_single_flow_timing(star8):
    # 1 MB at 100 Gbit/s = 80 us; latency 1 us; reconfig 20 us
    flows = [Flow(0, 0, 1, 1_000_000, 100)]
    res = simulate(star8, flows, FifoScheduler(8))
    r = res.records[0]
    assert r.grant_ns == 100
    assert r.start_ns == 100 + 20_000
    assert r.done_ns == 100 + 20_000 + 80_000 + 1_000
    assert r.fct_ns == 101_000
    assert r.ideal_fct_ns == 81_000
    assert r.wait_ns == 0 and r.attempts == 0
    assert res.makespan_ns == r.done_ns
    assert res.ingress[0].busy_ns == 101_000 and res.egress[1].busy_ns == 101_000
    assert res.ingress[0].circuits == 1 and res.egress[0].circuits == 0
    assert not res.stalled and check_invariants(res) == []


def test_port_contention_serialises(star8):
    flows = [Flow(0, 0, 1, 1_000_000, 0), Flow(1, 0, 2, 1_000_000, 0)]
    res = simulate(star8, flows, FifoScheduler(8))
    a, b = res.records
    assert a.grant_ns == 0
    assert b.grant_ns == a.done_ns  # waits for ingress port 0
    assert b.attempts == 1
    assert check_invariants(res) == []


def test_disjoint_flows_run_in_parallel(star8):
    flows = [Flow(0, 0, 1, 1_000_000, 0), Flow(1, 2, 3, 1_000_000, 0), Flow(2, 4, 5, 1_000_000, 0)]
    res = simulate(star8, flows, FifoScheduler(8))
    assert all(r.grant_ns == 0 for r in res.records)
    assert res.makespan_ns == 101_000


def test_fifo_skips_blocked_head(star8):
    # flow 1 is blocked by flow 0 on egress 1; flow 2 (2->3) must not wait behind it
    flows = [Flow(0, 0, 1, 1_000_000, 0), Flow(1, 2, 1, 1_000_000, 10), Flow(2, 2, 3, 1_000_000, 20)]
    res = simulate(star8, flows, FifoScheduler(8))
    assert res.records[2].grant_ns == 20  # ingress 2 was free at its arrival; not stuck behind flow 1
    # flow 1 needs egress 1 (free at 101000) and ingress 2 (free when flow 2 finishes at 101020)
    assert res.records[1].grant_ns == res.records[2].done_ns == 101_020
    assert res.records[1].attempts == 3


def test_guard_time_delays_reuse():
    topo = Topology.star(4, reconfig_ns=1000, guard_ns=500)
    flows = [Flow(0, 0, 1, 1_000_000, 0), Flow(1, 0, 2, 1_000_000, 0)]
    res = simulate(topo, flows, FifoScheduler(4))
    assert res.records[1].grant_ns == res.records[0].done_ns + 500


def test_bandwidth_mismatch_uses_slower_link():
    topo = Topology.star(3, bandwidth_gbps=100.0, latency_ns=0, reconfig_ns=0)
    topo.add_host("slow", bandwidth_gbps=25.0, latency_ns=0)
    res = simulate(topo, [Flow(0, 0, 3, 1_000_000, 0)], FifoScheduler(4))
    assert res.records[0].fct_ns == 320_000


def test_deterministic(star8, flows_uniform_200):
    a = simulate(star8, flows_uniform_200, FifoScheduler(8))
    b = simulate(star8, flows_uniform_200, FifoScheduler(8))
    assert [(r.grant_ns, r.done_ns) for r in a.records] == [(r.grant_ns, r.done_ns) for r in b.records]
    assert a.events_processed == b.events_processed


def test_random_workload_invariants(star8):
    for seed in range(3):
        flows = generate(WorkloadSpec(n_flows=300, n_hosts=8, load=0.8, size=FixedSize(500_000), seed=seed))
        res = simulate(star8, flows, FifoScheduler(8))
        assert len(res.completed()) == 300
        assert check_invariants(res) == []
        assert all(r.fct_ns >= r.ideal_fct_ns + star8.switch.reconfig_ns for r in res.records)


def test_max_time_stops_early(star8):
    flows = generate(WorkloadSpec(n_flows=200, n_hosts=8, load=0.8, seed=1))
    res = simulate(star8, flows, FifoScheduler(8), max_time_ns=flows[50].arrival_ns)
    assert 0 < len(res.completed()) < 200
    assert len(res.pending()) > 0
    assert not res.stalled  # events remain, it was simply cut off
    assert check_invariants(res) == []


def test_construction_errors(star8):
    with pytest.raises(SimulationError):
        Simulation(star8, [], FifoScheduler(4))
    with pytest.raises(Exception):
        Simulation(star8, [Flow(0, 0, 9, 10, 0)], FifoScheduler(8))


def test_port_usage_guards():
    p = PortUsage()
    p.acquire(0)
    with pytest.raises(SimulationError):
        p.acquire(1)
    p.release(10)
    assert p.busy_ns == 10 and p.circuits == 1 and not p.busy
    with pytest.raises(SimulationError):
        p.release(11)


def test_scheduler_registry():
    assert "fifo" in make.__globals__["available"]()
    s = make("fifo", 4)
    assert isinstance(s, FifoScheduler) and s.describe()["name"] == "fifo"
    with pytest.raises(SchedulerError):
        make("nope", 4)
    with pytest.raises(SchedulerError):
        FifoScheduler(1)

    @register
    class Greedy(FifoScheduler):
        name = "test-greedy"

    assert isinstance(make("test-greedy", 4), Greedy)
    with pytest.raises(SchedulerError):
        register(type("Bad", (Scheduler,), {"name": "base"}))


def test_check_selection_catches_bad_schedulers(star8):
    pf = lambda i, s, d: PendingFlow(Flow(i, s, d, 10, 0), 0)  # noqa: E731
    check_selection([pf(0, 0, 1), pf(1, 2, 3)], {0, 2}, {1, 3})
    with pytest.raises(SchedulerError):
        check_selection([pf(0, 0, 1)], set(), {1})
    with pytest.raises(SchedulerError):
        check_selection([pf(0, 0, 1)], {0}, set())
    with pytest.raises(SchedulerError):
        check_selection([pf(0, 0, 1), pf(1, 0, 2)], {0}, {1, 2})
    with pytest.raises(SchedulerError):
        check_selection([pf(0, 0, 1), pf(1, 2, 1)], {0, 2}, {1})

    class Cheat(Scheduler):
        name = "cheat"

        def select(self, pending, free_in, free_out, now_ns):
            return list(pending)  # ignores port availability

    flows = [Flow(0, 0, 1, 10, 0), Flow(1, 0, 2, 10, 0)]
    with pytest.raises(SchedulerError):
        simulate(star8, flows, Cheat(8))


def test_stall_detection(star8):
    class Lazy(Scheduler):
        name = "lazy"

        def select(self, pending, free_in, free_out, now_ns):
            return []

    res = simulate(star8, [Flow(0, 0, 1, 10, 0)], Lazy(8))
    assert res.stalled and len(res.pending()) == 1
    assert res.makespan_ns == 0
