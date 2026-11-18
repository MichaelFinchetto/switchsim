"""optiswitch: a deterministic discrete-event simulator for optical circuit switches.

Modules
-------
events     simulation clock and stable event queue
topology   hosts, links and switch timing; JSON I/O
workload   flow generation, size distributions, JSONL traces
scheduler  circuit scheduler interface and registry (FIFO shipped)
sim        the engine: arrivals, grants, reconfiguration, transmission
metrics    FCT statistics, throughput, utilisation, fairness, invariants
report     text / JSON / CSV output
cli        command-line entry point
"""

__version__ = "0.2.0"

from .metrics import Summary, check_invariants, summarise  # noqa: E402,F401
from .scheduler import FifoScheduler, Scheduler, available, make, register  # noqa: E402,F401
from .sim import SimResult, Simulation, simulate  # noqa: E402,F401
from .topology import Topology  # noqa: E402,F401
from .workload import Flow, WorkloadSpec, generate  # noqa: E402,F401
