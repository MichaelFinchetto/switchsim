# optiswitch

A small, dependency-free, deterministic discrete-event simulator for
**optical circuit-switched** networks: hosts attached to a single optical
circuit switch, flows that need an end-to-end circuit, and a pluggable
scheduler that decides which circuits to set up.

Typical questions it answers: how much does reconfiguration time cost at a
given load; how does flow completion time scale with flow size; how do
different circuit schedulers compare on the same workload.

## Model

```
host h_i ──(link: bandwidth, latency)──► [ optical circuit switch ] ──► host h_j
```

* Every host has one bidirectional link to the switch: an **ingress port**
  (host → switch) and an **egress port** (switch → host).
* A **flow** is `size_bytes` from `src` to `dst` becoming available at
  `arrival_ns`. It needs a **circuit**: its ingress port and its destination's
  egress port, both free.
* Setting up a circuit takes `reconfig_ns`. Data then flows at the slower of
  the two link bandwidths; the flow is complete when the last bit has been
  *received* (`size/bandwidth + latency_src + latency_dst`). Ports are released
  after an optional `guard_ns`.
* A **scheduler** is invoked whenever a flow arrives or a circuit is released
  and chooses, from the pending flows, a set whose ingress ports are distinct,
  whose egress ports are distinct and which are all free. The baseline ships
  `fifo` (arrival order, skipping flows whose ports are busy).
* Everything is driven by `random.Random(seed)`; the same inputs always
  produce the same result.

## Install and run

```bash
pip install -e ".[test]"
python -m pytest

optiswitch run --hosts 8 --flows 2000 --load 0.6 --seed 1 --check
optiswitch run --hosts 16 --pattern hotspot --size lognormal:2000000:0.8 --json
optiswitch gen-workload --hosts 8 --flows 5000 --pattern permutation --out flows.jsonl
optiswitch gen-topology --hosts 8 --reconfig-ns 25000 --out lab.json
optiswitch run --topology lab.json --trace flows.jsonl --flows-csv per_flow.csv
optiswitch validate-trace flows.jsonl --hosts 8
optiswitch schedulers
```

Without installing, prefix commands with `PYTHONPATH=src python -m optiswitch.cli`.

## Python API

```python
from optiswitch import Topology, WorkloadSpec, generate, make, simulate, summarise
from optiswitch.workload import LogNormalSize

topo = Topology.star(8, bandwidth_gbps=100, reconfig_ns=20_000)
flows = generate(WorkloadSpec(pattern="uniform", n_flows=2000, n_hosts=8,
                              size=LogNormalSize(1_000_000, 0.8), load=0.6, seed=1))
result = simulate(topo, flows, make("fifo", 8))
print(summarise(result).to_dict())
```

## File formats

* **Topology JSON** — `{"name": ..., "switch": {"reconfig_ns": ..., "guard_ns": ...}, "hosts": [{"name", "bandwidth_gbps", "latency_ns"}, ...]}`
* **Flow trace JSONL** — one object per line: `{"id", "src", "dst", "size_bytes", "arrival_ns"}`

## Units

| Quantity   | Unit            |
|------------|-----------------|
| time       | ns (integers)   |
| bandwidth  | Gbit/s          |
| size       | bytes           |
| load       | fraction of total host link capacity |

## Layout

```
src/optiswitch/
  events.py     clock + stable priority queue
  topology.py   hosts, switch spec, JSON I/O
  workload.py   flow generation, size distributions, JSONL
  scheduler.py  Scheduler base, FIFO, registry
  sim.py        the engine
  metrics.py    FCT stats, throughput, utilisation, fairness, Little's law, invariants
  report.py     text / JSON / CSV / comparison table / histogram
  cli.py        argparse entry point
tests/          pytest suite (offline, deterministic)
```

## License

MIT — see `LICENSE`.
