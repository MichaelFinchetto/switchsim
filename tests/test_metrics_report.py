import json
import math

import pytest

from optiswitch.metrics import (
    MetricsError,
    check_invariants,
    fct_stats,
    jain_fairness,
    littles_law_check,
    mean,
    offered_load_gbps,
    per_host_throughput_gbps,
    percentile,
    port_utilisation,
    reconfiguration_overhead,
    stdev,
    summarise,
    throughput_gbps,
)
from optiswitch.report import comparison_table, flows_csv, histogram, json_summary, ports_csv, text_summary
from optiswitch.scheduler import FifoScheduler
from optiswitch.sim import simulate
from optiswitch.topology import Topology
from optiswitch.workload import FixedSize, Flow, WorkloadSpec, generate


def test_basic_stats():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 99) == 5
    assert percentile([1, 2, 3, 4, 5], 0) == 1 and percentile([1, 2, 3, 4, 5], 100) == 5
    assert mean([1, 2, 3]) == 2
    assert stdev([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.138, abs=1e-3)
    assert stdev([1]) == 0.0
    assert jain_fairness([1, 1, 1]) == pytest.approx(1.0)
    assert jain_fairness([1, 0, 0, 0]) == pytest.approx(0.25)
    assert jain_fairness([0, 0]) == 1.0
    for bad in (lambda: percentile([], 50), lambda: percentile([1], 101), lambda: mean([]), lambda: jain_fairness([])):
        with pytest.raises(MetricsError):
            bad()


def test_metrics_on_simple_run(star8):
    flows = [Flow(0, 0, 1, 1_000_000, 0), Flow(1, 0, 2, 1_000_000, 0)]
    res = simulate(star8, flows, FifoScheduler(8))
    stats = fct_stats(res.records)
    assert stats.count == 2
    assert stats.max_ns == 202_000
    assert stats.mean_slowdown == pytest.approx((101_000 / 81_000 + 202_000 / 81_000) / 2)
    # 16 Mbit over 202 us
    assert throughput_gbps(res) == pytest.approx(16e6 / 202_000)
    assert offered_load_gbps(res) == math.inf  # both arrived at t=0
    util = port_utilisation(res)
    assert util["ingress"][0] == pytest.approx(1.0)
    assert util["egress"][1] == pytest.approx(101_000 / 202_000)
    assert per_host_throughput_gbps(res)[0] == pytest.approx(16e6 / 202_000)
    assert reconfiguration_overhead(res) == pytest.approx(40_000 / 202_000)


def test_summary_and_reports(star8, flows_uniform_200):
    res = simulate(star8, flows_uniform_200, FifoScheduler(8))
    s = summarise(res)
    assert s.completed == 200 and s.scheduler == "fifo" and not s.stalled
    assert 0 < s.throughput_gbps <= 800
    assert 0 < s.fairness <= 1
    d = json.loads(json_summary(s))
    assert d["fct"]["count"] == 200 and d["flows"] == 200
    text = text_summary(s)
    assert "throughput" in text and "fifo" in text
    csv_text = flows_csv(res)
    assert csv_text.splitlines()[0].startswith("id,src,dst") and len(csv_text.splitlines()) == 201
    pcsv = ports_csv(res)
    assert len(pcsv.splitlines()) == 9 and pcsv.splitlines()[1].startswith("h0,")
    table = comparison_table([s, s])
    assert table.count("\n") == 3 and "fifo" in table


def test_littles_law_on_long_run():
    topo = Topology.star(8, reconfig_ns=5_000)
    flows = generate(WorkloadSpec(n_flows=3000, n_hosts=8, load=0.7, size=FixedSize(500_000), seed=11))
    res = simulate(topo, flows, FifoScheduler(8))
    ll = littles_law_check(res)
    assert ll["relative_difference"] < 0.05
    with pytest.raises(MetricsError):
        littles_law_check(simulate(topo, flows[:1], FifoScheduler(8)))


def test_fct_stats_requires_completion(star8):
    res = simulate(star8, [Flow(0, 0, 1, 10, 0)], FifoScheduler(8), max_time_ns=0)
    # run stopped at t=0 before completion
    with pytest.raises(MetricsError):
        fct_stats(res.records)
    assert throughput_gbps(res) == 0.0
    assert reconfiguration_overhead(res) == 0.0


def test_check_invariants_detects_tampering(star8):
    res = simulate(star8, [Flow(0, 0, 1, 1_000_000, 0), Flow(1, 0, 2, 1_000_000, 0)], FifoScheduler(8))
    assert check_invariants(res) == []
    res.records[1].grant_ns = 0  # overlap on ingress 0
    problems = check_invariants(res)
    assert any("overlap" in p for p in problems)
    res.records[0].done_ns = 10  # faster than light
    assert any("physically" in p for p in check_invariants(res))


def test_histogram():
    h = histogram([1, 2, 2, 3, 10], bins=3, width=10)
    assert h.count("\n") == 2 and "(4)" in h and "(0)" in h and "(1)" in h
    assert histogram([]) == "(no data)"
    assert "####" in histogram([5, 5, 5])
    with pytest.raises(ValueError):
        histogram([1, 2], bins=0)
