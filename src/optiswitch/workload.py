"""Flow generation and the JSONL trace format.

A *flow* is a unit of work: ``size_bytes`` from ``src`` to ``dst`` that
becomes available at ``arrival_ns``. Generators are driven by
``random.Random(seed)`` so every workload is reproducible.

Trace files are JSON Lines, one flow per line::

    {"id": 0, "src": 3, "dst": 5, "size_bytes": 1048576, "arrival_ns": 0}
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence


class WorkloadError(ValueError):
    pass


@dataclass(frozen=True)
class Flow:
    id: int
    src: int
    dst: int
    size_bytes: int
    arrival_ns: int

    def __post_init__(self) -> None:
        if self.src == self.dst:
            raise WorkloadError(f"flow {self.id}: src and dst must differ")
        if self.size_bytes <= 0:
            raise WorkloadError(f"flow {self.id}: size must be positive")
        if self.arrival_ns < 0:
            raise WorkloadError(f"flow {self.id}: arrival must be non-negative")

    def to_dict(self) -> Dict[str, int]:
        return {"id": self.id, "src": self.src, "dst": self.dst,
                "size_bytes": self.size_bytes, "arrival_ns": self.arrival_ns}

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "Flow":
        try:
            return cls(int(d["id"]), int(d["src"]), int(d["dst"]), int(d["size_bytes"]), int(d["arrival_ns"]))
        except KeyError as exc:
            raise WorkloadError(f"flow record missing field {exc}") from None
        except (TypeError, ValueError) as exc:
            raise WorkloadError(f"bad flow record: {exc}") from None


# --------------------------------------------------------------------------
# Size distributions
# --------------------------------------------------------------------------

class SizeDistribution:
    """Callable returning a flow size in bytes."""

    def sample(self, rng: random.Random) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def mean_bytes(self) -> float:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass(frozen=True)
class FixedSize(SizeDistribution):
    size_bytes: int

    def __post_init__(self) -> None:
        if self.size_bytes <= 0:
            raise WorkloadError("size must be positive")

    def sample(self, rng: random.Random) -> int:
        return self.size_bytes

    def mean_bytes(self) -> float:
        return float(self.size_bytes)


@dataclass(frozen=True)
class LogNormalSize(SizeDistribution):
    """Log-normal sizes parameterised by their mean and sigma (of ln size)."""

    mean_bytes_: float
    sigma: float = 1.0
    min_bytes: int = 64

    def __post_init__(self) -> None:
        if self.mean_bytes_ <= 0 or self.sigma < 0 or self.min_bytes <= 0:
            raise WorkloadError("log-normal parameters must be positive")

    def sample(self, rng: random.Random) -> int:
        mu = math.log(self.mean_bytes_) - 0.5 * self.sigma**2
        return max(self.min_bytes, int(round(rng.lognormvariate(mu, self.sigma))))

    def mean_bytes(self) -> float:
        return self.mean_bytes_


@dataclass(frozen=True)
class BimodalSize(SizeDistribution):
    """Mice and elephants: ``p_large`` of flows are ``large_bytes``."""

    small_bytes: int
    large_bytes: int
    p_large: float = 0.1

    def __post_init__(self) -> None:
        if self.small_bytes <= 0 or self.large_bytes <= 0 or not 0.0 <= self.p_large <= 1.0:
            raise WorkloadError("bimodal parameters out of range")

    def sample(self, rng: random.Random) -> int:
        return self.large_bytes if rng.random() < self.p_large else self.small_bytes

    def mean_bytes(self) -> float:
        return self.p_large * self.large_bytes + (1 - self.p_large) * self.small_bytes


# --------------------------------------------------------------------------
# Traffic patterns
# --------------------------------------------------------------------------

PATTERNS = ("uniform", "hotspot", "permutation", "all_to_all")


def _poisson_arrivals(rng: random.Random, n: int, rate_per_ns: float) -> List[int]:
    t = 0.0
    out = []
    for _ in range(n):
        t += rng.expovariate(rate_per_ns)
        out.append(int(t))
    return out


@dataclass(frozen=True)
class WorkloadSpec:
    pattern: str = "uniform"
    n_flows: int = 1000
    n_hosts: int = 8
    size: SizeDistribution = FixedSize(1_048_576)
    load: float = 0.5
    """Offered load as a fraction of per-host link capacity (0 < load <= ~1)."""
    bandwidth_gbps: float = 100.0
    hotspot_fraction: float = 0.5
    """Fraction of flows directed at host 0 for the ``hotspot`` pattern."""
    seed: int = 0

    def __post_init__(self) -> None:
        if self.pattern not in PATTERNS:
            raise WorkloadError(f"unknown pattern {self.pattern!r}; choose from {PATTERNS}")
        if self.n_flows <= 0 or self.n_hosts < 2:
            raise WorkloadError("n_flows must be positive and n_hosts >= 2")
        if not 0.0 < self.load:
            raise WorkloadError("load must be positive")
        if not 0.0 <= self.hotspot_fraction <= 1.0:
            raise WorkloadError("hotspot_fraction must lie in [0, 1]")

    def arrival_rate_per_ns(self) -> float:
        """Aggregate flow arrival rate that produces the requested load.

        Each host can transmit ``bandwidth_gbps`` Gbit/s; total capacity is
        ``n_hosts * bandwidth``. Load ``L`` means the offered bit rate is
        ``L * capacity``; dividing by the mean flow size gives flows/ns.
        """
        capacity_bits_per_ns = self.n_hosts * self.bandwidth_gbps
        return self.load * capacity_bits_per_ns / (self.size.mean_bytes() * 8)


def generate(spec: WorkloadSpec) -> List[Flow]:
    """Generate flows for ``spec`` deterministically from ``spec.seed``."""
    rng = random.Random(spec.seed)
    arrivals = _poisson_arrivals(rng, spec.n_flows, spec.arrival_rate_per_ns())
    hosts = list(range(spec.n_hosts))
    flows: List[Flow] = []

    if spec.pattern == "permutation":
        perm = hosts[:]
        while any(perm[i] == i for i in range(len(perm))):
            rng.shuffle(perm)
        mapping = {i: perm[i] for i in hosts}

    for i in range(spec.n_flows):
        if spec.pattern == "uniform":
            src = rng.choice(hosts)
            dst = rng.choice([h for h in hosts if h != src])
        elif spec.pattern == "hotspot":
            if rng.random() < spec.hotspot_fraction:
                dst = 0
                src = rng.choice(hosts[1:])
            else:
                src = rng.choice(hosts)
                dst = rng.choice([h for h in hosts if h != src])
        elif spec.pattern == "permutation":
            src = rng.choice(hosts)
            dst = mapping[src]
        else:  # all_to_all: round-robin over every ordered pair
            k = i % (spec.n_hosts * (spec.n_hosts - 1))
            src, off = divmod(k, spec.n_hosts - 1)
            dst = off if off < src else off + 1
        flows.append(Flow(i, src, dst, spec.size.sample(rng), arrivals[i]))
    return flows


# --------------------------------------------------------------------------
# JSONL I/O
# --------------------------------------------------------------------------

def dumps(flows: Iterable[Flow]) -> str:
    return "".join(json.dumps(f.to_dict(), separators=(",", ":")) + "\n" for f in flows)


def loads(text: str) -> List[Flow]:
    flows = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkloadError(f"line {lineno}: invalid JSON ({exc.msg})") from None
        if not isinstance(rec, dict):
            raise WorkloadError(f"line {lineno}: expected an object")
        flows.append(Flow.from_dict(rec))
    validate(flows)
    return flows


def save(flows: Iterable[Flow], path: str | Path) -> Path:
    path = Path(path)
    path.write_text(dumps(flows), encoding="utf-8")
    return path


def load(path: str | Path) -> List[Flow]:
    path = Path(path)
    if not path.exists():
        raise WorkloadError(f"trace file not found: {path}")
    return loads(path.read_text(encoding="utf-8"))


def validate(flows: Sequence[Flow], n_hosts: Optional[int] = None) -> None:
    """Check ids are unique and hosts are in range; arrivals need not be sorted."""
    ids = [f.id for f in flows]
    if len(set(ids)) != len(ids):
        raise WorkloadError("duplicate flow ids")
    if n_hosts is not None:
        for f in flows:
            if not (0 <= f.src < n_hosts and 0 <= f.dst < n_hosts):
                raise WorkloadError(f"flow {f.id}: host out of range for {n_hosts} hosts")


def sorted_by_arrival(flows: Iterable[Flow]) -> List[Flow]:
    return sorted(flows, key=lambda f: (f.arrival_ns, f.id))


def total_bytes(flows: Iterable[Flow]) -> int:
    return sum(f.size_bytes for f in flows)


def demand_matrix(flows: Iterable[Flow], n_hosts: int) -> List[List[int]]:
    """Bytes offered from each src (row) to each dst (column)."""
    m = [[0] * n_hosts for _ in range(n_hosts)]
    for f in flows:
        m[f.src][f.dst] += f.size_bytes
    return m


def iter_batches(flows: Sequence[Flow], window_ns: int) -> Iterator[List[Flow]]:
    """Group flows (sorted by arrival) into consecutive time windows."""
    if window_ns <= 0:
        raise WorkloadError("window must be positive")
    batch: List[Flow] = []
    edge = window_ns
    for f in sorted_by_arrival(flows):
        while f.arrival_ns >= edge:
            yield batch
            batch = []
            edge += window_ns
        batch.append(f)
    yield batch
