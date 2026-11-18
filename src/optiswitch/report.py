"""Human- and machine-readable reports."""

from __future__ import annotations

import csv
import io
import json
from typing import Iterable, Sequence

from .metrics import Summary, port_utilisation
from .sim import SimResult


def _fmt_ns(ns: float) -> str:
    if ns >= 1e9:
        return f"{ns / 1e9:.3f} s"
    if ns >= 1e6:
        return f"{ns / 1e6:.3f} ms"
    if ns >= 1e3:
        return f"{ns / 1e3:.3f} us"
    return f"{ns:.0f} ns"


def text_summary(summary: Summary) -> str:
    lines = [
        f"scheduler:          {summary.scheduler}",
        f"flows:              {summary.completed}/{summary.flows} completed"
        + ("  (STALLED)" if summary.stalled else ""),
        f"makespan:           {_fmt_ns(summary.makespan_ns)}",
        f"offered load:       {summary.offered_gbps:.2f} Gbit/s",
        f"throughput:         {summary.throughput_gbps:.2f} Gbit/s",
        f"FCT mean / p50 / p99 / max: {_fmt_ns(summary.fct.mean_ns)} / {_fmt_ns(summary.fct.median_ns)}"
        f" / {_fmt_ns(summary.fct.p99_ns)} / {_fmt_ns(summary.fct.max_ns)}",
        f"slowdown mean / p99: {summary.fct.mean_slowdown:.2f} / {summary.fct.p99_slowdown:.2f}",
        f"fairness (Jain):    {summary.fairness:.3f}",
        f"reconfig overhead:  {summary.reconfig_overhead * 100:.1f} %",
        f"port utilisation:   in {summary.mean_ingress_util * 100:.1f} %  out {summary.mean_egress_util * 100:.1f} %",
    ]
    for k, v in summary.extra.items():
        lines.append(f"{k + ':':20}{v}")
    return "\n".join(lines)


def json_summary(summary: Summary) -> str:
    return json.dumps(summary.to_dict(), indent=2, sort_keys=True)


def flows_csv(result: SimResult) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "src", "dst", "size_bytes", "arrival_ns", "grant_ns", "start_ns", "done_ns", "fct_ns", "ideal_fct_ns", "attempts"])
    for r in result.records:
        f = r.flow
        w.writerow([f.id, f.src, f.dst, f.size_bytes, f.arrival_ns, r.grant_ns, r.start_ns, r.done_ns, r.fct_ns, r.ideal_fct_ns, r.attempts])
    return buf.getvalue()


def ports_csv(result: SimResult) -> str:
    util = port_utilisation(result)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["host", "ingress_busy_ns", "ingress_util", "ingress_circuits", "egress_busy_ns", "egress_util", "egress_circuits"])
    for h, (i, o) in enumerate(zip(result.ingress, result.egress)):
        w.writerow([result.topology.host(h).name, i.busy_ns, f"{util['ingress'][h]:.6f}", i.circuits, o.busy_ns, f"{util['egress'][h]:.6f}", o.circuits])
    return buf.getvalue()


def comparison_table(summaries: Sequence[Summary]) -> str:
    """Side-by-side table of several runs (e.g. different schedulers)."""
    cols = [
        ("scheduler", lambda s: s.scheduler),
        ("completed", lambda s: f"{s.completed}/{s.flows}"),
        ("throughput", lambda s: f"{s.throughput_gbps:.1f}"),
        ("fct_mean", lambda s: _fmt_ns(s.fct.mean_ns)),
        ("fct_p99", lambda s: _fmt_ns(s.fct.p99_ns)),
        ("slowdown", lambda s: f"{s.fct.mean_slowdown:.2f}"),
        ("fairness", lambda s: f"{s.fairness:.3f}"),
        ("reconfig%", lambda s: f"{s.reconfig_overhead * 100:.1f}"),
    ]
    rows = [[fn(s) for _, fn in cols] for s in summaries]
    widths = [max(len(name), *(len(r[i]) for r in rows)) for i, (name, _) in enumerate(cols)]
    out = ["  ".join(name.ljust(w) for (name, _), w in zip(cols, widths))]
    out.append("  ".join("-" * w for w in widths))
    for r in rows:
        out.append("  ".join(c.ljust(w) for c, w in zip(r, widths)))
    return "\n".join(out)


def histogram(values: Iterable[float], bins: int = 10, width: int = 40) -> str:
    """ASCII histogram of ``values`` (e.g. FCTs)."""
    vals = sorted(values)
    if not vals:
        return "(no data)"
    if bins < 1:
        raise ValueError("bins must be positive")
    lo, hi = vals[0], vals[-1]
    if hi == lo:
        return f"{lo:g}: {'#' * width} ({len(vals)})"
    step = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = min(int((v - lo) / step), bins - 1)
        counts[idx] += 1
    peak = max(counts)
    lines = []
    for i, c in enumerate(counts):
        bar = "#" * int(round(width * c / peak))
        lines.append(f"{lo + i * step:12.4g} - {lo + (i + 1) * step:12.4g} | {bar} ({c})")
    return "\n".join(lines)
