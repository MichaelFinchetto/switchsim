"""Command-line interface.

Examples::

    optiswitch run --hosts 8 --flows 2000 --load 0.6 --seed 1
    optiswitch run --topology lab.json --trace flows.jsonl --scheduler fifo --json
    optiswitch gen-workload --hosts 16 --flows 5000 --pattern hotspot --out flows.jsonl
    optiswitch gen-topology --hosts 8 --reconfig-ns 25000 --out lab.json
    optiswitch validate-trace flows.jsonl --hosts 8
    optiswitch schedulers
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import __version__, workload
from .metrics import MetricsError, check_invariants, summarise
from .report import flows_csv, json_summary, ports_csv, text_summary
from .scheduler import SchedulerError, available, make
from .sim import SimulationError, simulate
from .topology import Topology, TopologyError
from .workload import (
    BimodalSize,
    FixedSize,
    LogNormalSize,
    PATTERNS,
    WorkloadError,
    WorkloadSpec,
    generate,
)


class CliError(Exception):
    pass


def _add_topology_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--topology", help="topology JSON file (overrides --hosts etc.)")
    p.add_argument("--hosts", type=int, default=8)
    p.add_argument("--bandwidth-gbps", type=float, default=100.0)
    p.add_argument("--latency-ns", type=int, default=500)
    p.add_argument("--reconfig-ns", type=int, default=20_000)
    p.add_argument("--guard-ns", type=int, default=0)


def _add_workload_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--trace", help="JSONL flow trace (overrides generator options)")
    p.add_argument("--flows", type=int, default=1000)
    p.add_argument("--pattern", choices=PATTERNS, default="uniform")
    p.add_argument("--load", type=float, default=0.5)
    p.add_argument("--size", default="fixed:1048576",
                   help="fixed:BYTES | lognormal:MEAN[:SIGMA] | bimodal:SMALL:LARGE[:P_LARGE]")
    p.add_argument("--hotspot-fraction", type=float, default=0.5)
    p.add_argument("--seed", type=int, default=0)


def _topology_from_args(args: argparse.Namespace) -> Topology:
    if args.topology:
        return Topology.load(args.topology)
    return Topology.star(args.hosts, args.bandwidth_gbps, args.latency_ns, args.reconfig_ns, args.guard_ns)


def parse_size(spec: str):
    parts = spec.split(":")
    kind = parts[0]
    try:
        if kind == "fixed" and len(parts) == 2:
            return FixedSize(int(parts[1]))
        if kind == "lognormal" and len(parts) in (2, 3):
            return LogNormalSize(float(parts[1]), float(parts[2]) if len(parts) == 3 else 1.0)
        if kind == "bimodal" and len(parts) in (3, 4):
            return BimodalSize(int(parts[1]), int(parts[2]), float(parts[3]) if len(parts) == 4 else 0.1)
    except ValueError as exc:
        raise CliError(f"bad --size value {spec!r}: {exc}") from None
    raise CliError(f"bad --size value {spec!r}")


def _flows_from_args(args: argparse.Namespace, topo: Topology):
    if args.trace:
        flows = workload.load(args.trace)
        workload.validate(flows, topo.n_hosts)
        return flows
    spec = WorkloadSpec(
        pattern=args.pattern, n_flows=args.flows, n_hosts=topo.n_hosts, size=parse_size(args.size),
        load=args.load, bandwidth_gbps=min(h.bandwidth_gbps for h in topo.hosts),
        hotspot_fraction=args.hotspot_fraction, seed=args.seed,
    )
    return generate(spec)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="optiswitch", description="optical circuit switch simulator")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="simulate a workload")
    _add_topology_args(p)
    _add_workload_args(p)
    p.add_argument("--scheduler", default="fifo", help=f"one of: {', '.join(available())}")
    p.add_argument("--max-time-ns", type=int, default=None)
    p.add_argument("--json", action="store_true", help="JSON summary instead of text")
    p.add_argument("--flows-csv", metavar="FILE", help="write per-flow records")
    p.add_argument("--ports-csv", metavar="FILE", help="write per-port utilisation")
    p.add_argument("--check", action="store_true", help="verify simulation invariants (exit 2 on violation)")

    p = sub.add_parser("gen-workload", help="write a JSONL flow trace")
    _add_workload_args(p)
    p.add_argument("--hosts", type=int, default=8)
    p.add_argument("--bandwidth-gbps", type=float, default=100.0)
    p.add_argument("--out", required=True)

    p = sub.add_parser("gen-topology", help="write a topology JSON file")
    _add_topology_args(p)
    p.add_argument("--name", default=None)
    p.add_argument("--out", required=True)

    p = sub.add_parser("validate-trace", help="check a JSONL trace")
    p.add_argument("path")
    p.add_argument("--hosts", type=int, default=None)

    sub.add_parser("schedulers", help="list available schedulers")
    return parser


def cmd_run(args: argparse.Namespace) -> str:
    topo = _topology_from_args(args)
    flows = _flows_from_args(args, topo)
    sched = make(args.scheduler, topo.n_hosts)
    result = simulate(topo, flows, sched, args.max_time_ns)
    if args.flows_csv:
        with open(args.flows_csv, "w", encoding="utf-8") as fh:
            fh.write(flows_csv(result))
    if args.ports_csv:
        with open(args.ports_csv, "w", encoding="utf-8") as fh:
            fh.write(ports_csv(result))
    summary = summarise(result)
    if args.check:
        problems = check_invariants(result)
        if problems:
            raise CliError("invariant violations:\n  " + "\n  ".join(problems))
        summary.extra["invariants"] = "ok"
    return json_summary(summary) if args.json else text_summary(summary)


def cmd_gen_workload(args: argparse.Namespace) -> str:
    spec = WorkloadSpec(
        pattern=args.pattern, n_flows=args.flows, n_hosts=args.hosts, size=parse_size(args.size),
        load=args.load, bandwidth_gbps=args.bandwidth_gbps, hotspot_fraction=args.hotspot_fraction, seed=args.seed,
    )
    flows = generate(spec)
    path = workload.save(flows, args.out)
    return f"wrote {len(flows)} flows ({workload.total_bytes(flows)} bytes) to {path}"


def cmd_gen_topology(args: argparse.Namespace) -> str:
    topo = Topology.star(args.hosts, args.bandwidth_gbps, args.latency_ns, args.reconfig_ns, args.guard_ns, args.name)
    path = topo.save(args.out)
    return f"wrote topology {topo.name!r} with {topo.n_hosts} hosts to {path}"


def cmd_validate_trace(args: argparse.Namespace) -> str:
    flows = workload.load(args.path)
    workload.validate(flows, args.hosts)
    arrivals = [f.arrival_ns for f in flows]
    return (
        f"{len(flows)} flows, {workload.total_bytes(flows)} bytes, "
        f"arrivals {min(arrivals)}..{max(arrivals)} ns, "
        f"hosts {min(min(f.src, f.dst) for f in flows)}..{max(max(f.src, f.dst) for f in flows)}"
    )


def cmd_schedulers(_: argparse.Namespace) -> str:
    return "\n".join(available())


HANDLERS = {
    "run": cmd_run,
    "gen-workload": cmd_gen_workload,
    "gen-topology": cmd_gen_topology,
    "validate-trace": cmd_validate_trace,
    "schedulers": cmd_schedulers,
}


def run(argv: Optional[Sequence[str]] = None) -> str:
    args = build_parser().parse_args(argv)
    return HANDLERS[args.command](args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        print(run(argv))
        return 0
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (TopologyError, WorkloadError, SchedulerError, SimulationError, MetricsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
