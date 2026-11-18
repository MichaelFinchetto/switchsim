import random

import pytest

from optiswitch import workload
from optiswitch.workload import (
    BimodalSize,
    FixedSize,
    Flow,
    LogNormalSize,
    WorkloadError,
    WorkloadSpec,
    demand_matrix,
    generate,
    iter_batches,
    sorted_by_arrival,
    total_bytes,
)


def test_flow_validation():
    Flow(0, 0, 1, 10, 0)
    with pytest.raises(WorkloadError):
        Flow(0, 1, 1, 10, 0)
    with pytest.raises(WorkloadError):
        Flow(0, 0, 1, 0, 0)
    with pytest.raises(WorkloadError):
        Flow(0, 0, 1, 10, -1)
    with pytest.raises(WorkloadError):
        Flow.from_dict({"id": 0})
    with pytest.raises(WorkloadError):
        Flow.from_dict({"id": 0, "src": 0, "dst": 1, "size_bytes": "big", "arrival_ns": 0})


def test_size_distributions():
    rng = random.Random(1)
    assert FixedSize(100).sample(rng) == 100 and FixedSize(100).mean_bytes() == 100
    ln = LogNormalSize(1_000_000, sigma=0.5)
    samples = [ln.sample(random.Random(i)) for i in range(2000)]
    assert abs(sum(samples) / len(samples) / 1_000_000 - 1.0) < 0.1
    assert min(samples) >= ln.min_bytes
    bi = BimodalSize(1000, 1_000_000, 0.25)
    assert bi.mean_bytes() == pytest.approx(0.25 * 1_000_000 + 0.75 * 1000)
    bs = {bi.sample(random.Random(i)) for i in range(200)}
    assert bs == {1000, 1_000_000}
    for bad in (lambda: FixedSize(0), lambda: LogNormalSize(-1), lambda: BimodalSize(1, 2, 1.5)):
        with pytest.raises(WorkloadError):
            bad()


def test_spec_validation():
    with pytest.raises(WorkloadError):
        WorkloadSpec(pattern="weird")
    with pytest.raises(WorkloadError):
        WorkloadSpec(n_flows=0)
    with pytest.raises(WorkloadError):
        WorkloadSpec(n_hosts=1)
    with pytest.raises(WorkloadError):
        WorkloadSpec(load=0.0)
    with pytest.raises(WorkloadError):
        WorkloadSpec(hotspot_fraction=2.0)


def test_arrival_rate_matches_load():
    spec = WorkloadSpec(n_flows=4000, n_hosts=8, size=FixedSize(1_000_000), load=0.5, bandwidth_gbps=100.0, seed=1)
    # 8 hosts x 100 Gbit/s = 800 bit/ns; load 0.5 -> 400 bit/ns; / 8e6 bits per flow = 5e-5 flows/ns
    assert spec.arrival_rate_per_ns() == pytest.approx(5e-5)
    flows = generate(spec)
    span = max(f.arrival_ns for f in flows) - min(f.arrival_ns for f in flows)
    assert len(flows) / span == pytest.approx(5e-5, rel=0.1)


def test_generate_is_deterministic():
    spec = WorkloadSpec(n_flows=100, seed=42)
    assert generate(spec) == generate(spec)
    assert generate(spec) != generate(WorkloadSpec(n_flows=100, seed=43))


@pytest.mark.parametrize("pattern", ["uniform", "hotspot", "permutation", "all_to_all"])
def test_patterns_are_valid(pattern):
    flows = generate(WorkloadSpec(pattern=pattern, n_flows=300, n_hosts=6, seed=7))
    assert len(flows) == 300
    assert all(0 <= f.src < 6 and 0 <= f.dst < 6 and f.src != f.dst for f in flows)
    assert [f.id for f in flows] == list(range(300))
    assert all(a.arrival_ns <= b.arrival_ns for a, b in zip(flows, flows[1:]))


def test_hotspot_targets_host_zero():
    flows = generate(WorkloadSpec(pattern="hotspot", n_flows=1000, n_hosts=8, hotspot_fraction=0.8, seed=1))
    frac = sum(f.dst == 0 for f in flows) / len(flows)
    assert 0.75 < frac < 0.9


def test_permutation_is_fixed_mapping():
    flows = generate(WorkloadSpec(pattern="permutation", n_flows=500, n_hosts=6, seed=2))
    mapping = {}
    for f in flows:
        assert mapping.setdefault(f.src, f.dst) == f.dst
    assert len(set(mapping.values())) == len(mapping)


def test_all_to_all_covers_every_pair():
    n = 5
    flows = generate(WorkloadSpec(pattern="all_to_all", n_flows=n * (n - 1), n_hosts=n, seed=0))
    assert {(f.src, f.dst) for f in flows} == {(a, b) for a in range(n) for b in range(n) if a != b}


def test_jsonl_roundtrip(tmp_path):
    flows = generate(WorkloadSpec(n_flows=50, seed=5))
    text = workload.dumps(flows)
    assert text.count("\n") == 50
    assert workload.loads(text) == flows
    path = workload.save(flows, tmp_path / "f.jsonl")
    assert workload.load(path) == flows
    with pytest.raises(WorkloadError):
        workload.load(tmp_path / "missing.jsonl")
    with pytest.raises(WorkloadError):
        workload.loads("{bad json\n")
    with pytest.raises(WorkloadError):
        workload.loads("[1,2]\n")
    with pytest.raises(WorkloadError):
        workload.loads(workload.dumps([Flow(1, 0, 1, 5, 0), Flow(1, 1, 0, 5, 0)]))  # duplicate id


def test_validate_host_range():
    flows = [Flow(0, 0, 7, 10, 0)]
    workload.validate(flows, 8)
    with pytest.raises(WorkloadError):
        workload.validate(flows, 4)


def test_helpers():
    flows = [Flow(0, 0, 1, 100, 50), Flow(1, 1, 0, 200, 10), Flow(2, 0, 1, 300, 50)]
    assert [f.id for f in sorted_by_arrival(flows)] == [1, 0, 2]
    assert total_bytes(flows) == 600
    assert demand_matrix(flows, 2) == [[0, 400], [200, 0]]
    batches = list(iter_batches(flows, 20))
    assert [[f.id for f in b] for b in batches] == [[1], [], [0, 2]]
    with pytest.raises(WorkloadError):
        list(iter_batches(flows, 0))
