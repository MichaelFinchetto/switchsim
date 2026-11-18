import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from optiswitch.topology import Topology  # noqa: E402
from optiswitch.workload import FixedSize, WorkloadSpec, generate  # noqa: E402


@pytest.fixture
def star8() -> Topology:
    return Topology.star(8, bandwidth_gbps=100.0, latency_ns=500, reconfig_ns=20_000)


@pytest.fixture
def flows_uniform_200():
    return generate(WorkloadSpec(pattern="uniform", n_flows=200, n_hosts=8, size=FixedSize(1_000_000), load=0.5, seed=3))
