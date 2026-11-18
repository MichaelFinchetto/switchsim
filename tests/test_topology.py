import json

import pytest

from optiswitch.topology import Host, SwitchSpec, Topology, TopologyError, pairs


def test_star_construction(star8):
    assert star8.n_hosts == 8
    assert star8.name == "star-8"
    assert [h.name for h in star8.hosts] == [f"h{i}" for i in range(8)]
    assert star8.host("h3").id == 3 and star8.host(3).name == "h3"
    assert star8.host_ids() == tuple(range(8))
    star8.validate()


def test_latency_and_bandwidth(star8):
    assert star8.path_latency_ns(0, 1) == 1000
    assert star8.circuit_bandwidth_gbps(0, 1) == 100.0
    star8.add_host("slow", bandwidth_gbps=40.0, latency_ns=2000)
    assert star8.circuit_bandwidth_gbps(0, 8) == 40.0
    assert star8.path_latency_ns(8, 0) == 2500
    with pytest.raises(TopologyError):
        star8.path_latency_ns(1, 1)


def test_bisection_bandwidth():
    t = Topology.star(4, bandwidth_gbps=100.0)
    assert t.bisection_bandwidth_gbps() == 200.0
    t.add_host("x", 10.0)
    assert t.bisection_bandwidth_gbps() == 110.0  # two slowest of five


def test_errors():
    with pytest.raises(TopologyError):
        Topology.star(1)
    with pytest.raises(TopologyError):
        Host(0, "a", bandwidth_gbps=0)
    with pytest.raises(TopologyError):
        Host(0, "", 1.0)
    with pytest.raises(TopologyError):
        Host(0, "a", 1.0, latency_ns=-1)
    with pytest.raises(TopologyError):
        SwitchSpec(reconfig_ns=-1)
    t = Topology.star(2)
    with pytest.raises(TopologyError):
        t.add_host("h0")
    with pytest.raises(TopologyError):
        t.host(5)
    with pytest.raises(TopologyError):
        t.host("nope")
    with pytest.raises(TopologyError):
        Topology().validate()


def test_json_roundtrip(tmp_path, star8):
    star8.switch = SwitchSpec(25_000, 100)
    text = star8.to_json()
    data = json.loads(text)
    assert data["switch"] == {"reconfig_ns": 25_000, "guard_ns": 100}
    back = Topology.from_json(text)
    assert back.to_dict() == star8.to_dict()
    path = star8.save(tmp_path / "t.json")
    assert Topology.load(path).n_hosts == 8
    with pytest.raises(TopologyError):
        Topology.load(tmp_path / "missing.json")


def test_from_dict_validation():
    with pytest.raises(TopologyError):
        Topology.from_dict([])
    with pytest.raises(TopologyError):
        Topology.from_dict({"hosts": []})
    with pytest.raises(TopologyError):
        Topology.from_dict({"hosts": [1, 2]})
    with pytest.raises(TopologyError):
        Topology.from_dict({"hosts": [{"name": "a"}, {"name": "b"}], "switch": []})
    with pytest.raises(TopologyError):
        Topology.from_dict({"hosts": [{"name": "a", "bandwidth_gbps": "fast"}, {"name": "b"}]})
    with pytest.raises(TopologyError):
        Topology.from_json("{not json")
    t = Topology.from_dict({"hosts": [{}, {}]})
    assert [h.name for h in t.hosts] == ["h0", "h1"]


def test_pairs():
    assert pairs([0, 1, 2]) == [(0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1)]
