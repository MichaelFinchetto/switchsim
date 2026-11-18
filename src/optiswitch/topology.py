"""Network description: hosts, links and a single optical circuit switch.

The model is a *star*: every host has one bidirectional link to the switch.
A circuit from host ``a`` to host ``b`` occupies the ingress port of ``a``
and the egress port of ``b`` for its duration. The switch needs
``reconfig_ns`` to set up a new circuit before data can flow.

Topologies can be built programmatically or loaded from JSON::

    {
      "name": "lab-8",
      "switch": {"reconfig_ns": 20000},
      "hosts": [{"name": "h0", "bandwidth_gbps": 100, "latency_ns": 500}, ...]
    }
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence


class TopologyError(ValueError):
    pass


@dataclass(frozen=True)
class Host:
    id: int
    name: str
    bandwidth_gbps: float = 100.0
    latency_ns: int = 500
    """One-way propagation delay between the host and the switch."""

    def __post_init__(self) -> None:
        if self.bandwidth_gbps <= 0:
            raise TopologyError(f"host {self.name}: bandwidth must be positive")
        if self.latency_ns < 0:
            raise TopologyError(f"host {self.name}: latency must be non-negative")
        if not self.name:
            raise TopologyError("host name must not be empty")


@dataclass(frozen=True)
class SwitchSpec:
    reconfig_ns: int = 20_000
    """Time to establish a new circuit (MEMS mirrors settle, etc.)."""
    guard_ns: int = 0
    """Extra idle time after a circuit is torn down before the port is reusable."""

    def __post_init__(self) -> None:
        if self.reconfig_ns < 0 or self.guard_ns < 0:
            raise TopologyError("switch timings must be non-negative")


@dataclass
class Topology:
    name: str = "unnamed"
    switch: SwitchSpec = field(default_factory=SwitchSpec)
    hosts: List[Host] = field(default_factory=list)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def add_host(self, name: str, bandwidth_gbps: float = 100.0, latency_ns: int = 500) -> Host:
        if any(h.name == name for h in self.hosts):
            raise TopologyError(f"duplicate host name {name!r}")
        host = Host(len(self.hosts), name, bandwidth_gbps, latency_ns)
        self.hosts.append(host)
        return host

    @classmethod
    def star(
        cls,
        n_hosts: int,
        bandwidth_gbps: float = 100.0,
        latency_ns: int = 500,
        reconfig_ns: int = 20_000,
        guard_ns: int = 0,
        name: Optional[str] = None,
    ) -> "Topology":
        if n_hosts < 2:
            raise TopologyError("a star needs at least two hosts")
        topo = cls(name or f"star-{n_hosts}", SwitchSpec(reconfig_ns, guard_ns))
        for i in range(n_hosts):
            topo.add_host(f"h{i}", bandwidth_gbps, latency_ns)
        return topo

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    @property
    def n_hosts(self) -> int:
        return len(self.hosts)

    def host(self, key: int | str) -> Host:
        if isinstance(key, int):
            if not 0 <= key < len(self.hosts):
                raise TopologyError(f"no host with id {key}")
            return self.hosts[key]
        for h in self.hosts:
            if h.name == key:
                return h
        raise TopologyError(f"no host named {key!r}")

    def host_ids(self) -> Sequence[int]:
        return tuple(h.id for h in self.hosts)

    def path_latency_ns(self, src: int, dst: int) -> int:
        """One-way latency from ``src`` to ``dst`` through the switch."""
        if src == dst:
            raise TopologyError("source and destination must differ")
        return self.host(src).latency_ns + self.host(dst).latency_ns

    def circuit_bandwidth_gbps(self, src: int, dst: int) -> float:
        """A circuit runs at the slower of the two host links."""
        return min(self.host(src).bandwidth_gbps, self.host(dst).bandwidth_gbps)

    def bisection_bandwidth_gbps(self) -> float:
        """Half the hosts talking to the other half, one circuit each."""
        bws = sorted(h.bandwidth_gbps for h in self.hosts)
        return sum(bws[: len(bws) // 2])

    def validate(self) -> None:
        if len(self.hosts) < 2:
            raise TopologyError("topology needs at least two hosts")
        names = [h.name for h in self.hosts]
        if len(set(names)) != len(names):
            raise TopologyError("duplicate host names")
        for i, h in enumerate(self.hosts):
            if h.id != i:
                raise TopologyError("host ids must be contiguous from 0")

    # ------------------------------------------------------------------
    # serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "switch": asdict(self.switch),
            "hosts": [
                {"name": h.name, "bandwidth_gbps": h.bandwidth_gbps, "latency_ns": h.latency_ns}
                for h in self.hosts
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "Topology":
        if not isinstance(data, Mapping):
            raise TopologyError("topology must be a JSON object")
        hosts = data.get("hosts")
        if not isinstance(hosts, list) or not hosts:
            raise TopologyError("topology needs a non-empty 'hosts' list")
        sw = data.get("switch")
        if sw is None:
            sw = {}
        if not isinstance(sw, Mapping):
            raise TopologyError("'switch' must be an object")
        try:
            spec = SwitchSpec(int(sw.get("reconfig_ns", 20_000)), int(sw.get("guard_ns", 0)))
        except (TypeError, ValueError) as exc:
            raise TopologyError(f"bad switch spec: {exc}") from None
        topo = cls(str(data.get("name", "unnamed")), spec)
        for i, h in enumerate(hosts):
            if not isinstance(h, Mapping):
                raise TopologyError(f"host #{i} must be an object")
            try:
                topo.add_host(
                    str(h.get("name", f"h{i}")),
                    float(h.get("bandwidth_gbps", 100.0)),
                    int(h.get("latency_ns", 500)),
                )
            except (TypeError, ValueError) as exc:
                raise TopologyError(f"host #{i}: {exc}") from None
        topo.validate()
        return topo

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Topology":
        try:
            return cls.from_dict(json.loads(text))
        except json.JSONDecodeError as exc:
            raise TopologyError(f"invalid JSON: {exc}") from None

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> "Topology":
        path = Path(path)
        if not path.exists():
            raise TopologyError(f"topology file not found: {path}")
        return cls.from_json(path.read_text(encoding="utf-8"))


def pairs(hosts: Iterable[int]) -> List[tuple[int, int]]:
    """All ordered (src, dst) pairs with src != dst."""
    hs = list(hosts)
    return [(a, b) for a in hs for b in hs if a != b]
